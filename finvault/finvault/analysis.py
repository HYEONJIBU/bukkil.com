"""분석 API. 질문에 답할 때 이 모듈을 쓴다.

    from finvault.analysis import Vault
    vault = Vault()
    vault.metrics("삼성전자", ["revenue", "operating_income"], freq="Q", last=8)
    vault.ratios("삼성전자", freq="A")
    vault.compare(["삼성전자", "SK하이닉스"], "operating_income", freq="Q")
    vault.account("삼성전자", "연구개발")          # 표준 지표에 없는 계정 검색
    vault.items("삼성전자", "alotMatter")          # 배당 등 정기보고서 주요정보
    vault.sql("SELECT ...")
"""

from __future__ import annotations

import json

import pandas as pd

from . import db
from .accounts import METRICS, METRICS_BY_NAME

UNITS = {"원": 1, "천": 1e3, "백만": 1e6, "억": 1e8, "조": 1e12}


def _period_index(freq: str, periods: pd.Index) -> pd.Index:
    """비어있는 분기가 있어도 shift가 어긋나지 않도록 연속 기간 인덱스를 만든다."""
    if len(periods) == 0:
        return periods
    if freq == "A":
        years = [int(p[:4]) for p in periods]
        return pd.Index([f"{y}A" for y in range(min(years), max(years) + 1)], name="period")
    keys = sorted((int(p[:4]), int(p[-1])) for p in periods)
    (y0, q0), (y1, q1) = keys[0], keys[-1]
    out, y, q = [], y0, q0
    while (y, q) <= (y1, q1):
        out.append(f"{y}Q{q}")
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return pd.Index(out, name="period")


class Vault:
    def __init__(self, db_path=None):
        self.conn = db.connect(db_path)

    # --- 기본 -------------------------------------------------------------
    def sql(self, query: str, params=()) -> pd.DataFrame:
        return pd.read_sql_query(query, self.conn, params=params)

    def companies(self) -> pd.DataFrame:
        return self.sql(
            """SELECT c.corp_name, c.stock_code, c.corp_code, c.acc_mt,
                      MIN(m.period) AS first_period, MAX(m.period) AS last_period
               FROM companies c LEFT JOIN metrics m ON m.corp_code = c.corp_code AND m.period_type = 'Q'
               GROUP BY c.corp_code ORDER BY c.corp_name"""
        )

    def corp_code(self, company: str) -> str:
        row = self.conn.execute(
            """SELECT corp_code FROM companies
               WHERE corp_code = ?1 OR stock_code = ?1 OR corp_name = ?1 OR stock_name = ?1
               UNION ALL
               SELECT corp_code FROM companies WHERE corp_name LIKE '%' || ?1 || '%'
               LIMIT 1""",
            (company,),
        ).fetchone()
        if not row:
            raise LookupError(f"DB에 '{company}' 데이터가 없습니다. 먼저 `finvault ingest {company}` 를 실행하세요.")
        return row["corp_code"]

    def corp_name(self, corp_code: str) -> str:
        row = self.conn.execute("SELECT corp_name FROM companies WHERE corp_code = ?", (corp_code,)).fetchone()
        return row["corp_name"] if row else corp_code

    def _fs_div(self, corp_code: str, fs_div: str) -> str:
        if fs_div != "auto":
            return fs_div
        row = self.conn.execute(
            "SELECT 1 FROM metrics WHERE corp_code = ? AND fs_div = 'CFS' LIMIT 1", (corp_code,)
        ).fetchone()
        return "CFS" if row else "OFS"

    # --- 지표 -------------------------------------------------------------
    def metrics(self, company: str, metrics: list[str] | None = None, freq: str = "A",
                fs_div: str = "auto", start: str | None = None, end: str | None = None,
                last: int | None = None, unit: str = "원") -> pd.DataFrame:
        """행=기간(2024A / 2024Q3), 열=지표. freq: 'A' 연간, 'Q' 분기(3개월, BS는 분기말 잔액)."""
        cc = self.corp_code(company)
        fs = self._fs_div(cc, fs_div)
        names = metrics or [m.name for m in METRICS]
        unknown = [n for n in names if n not in METRICS_BY_NAME]
        if unknown:
            raise KeyError(f"알 수 없는 지표: {unknown}. 사용 가능: {list(METRICS_BY_NAME)}")
        df = self.sql(
            f"""SELECT period, metric, value FROM metrics
                WHERE corp_code = ? AND fs_div = ? AND period_type = ?
                  AND metric IN ({','.join('?' * len(names))})""",
            (cc, fs, freq, *names),
        )
        wide = df.pivot(index="period", columns="metric", values="value") if not df.empty else pd.DataFrame()
        wide = wide.reindex(_period_index(freq, wide.index)).reindex(columns=names)
        wide = self._slice(wide, start, end, last)
        if unit != "원":
            per_share = [c for c in wide.columns if c == "eps"]
            wide.loc[:, [c for c in wide.columns if c not in per_share]] /= UNITS[unit]
        wide.attrs.update(corp_code=cc, corp_name=self.corp_name(cc), fs_div=fs, freq=freq, unit=unit)
        return wide

    @staticmethod
    def _slice(df: pd.DataFrame, start, end, last) -> pd.DataFrame:
        if start:
            df = df[df.index >= start]
        if end:
            df = df[df.index <= end]
        if last:
            df = df.tail(last)
        return df

    def ttm(self, company: str, metrics: list[str] | None = None, fs_div: str = "auto", **kw) -> pd.DataFrame:
        """최근 4개 분기 합(손익/현금흐름). BS 지표는 분기말 잔액 그대로."""
        names = metrics or [m.name for m in METRICS]
        q = self.metrics(company, names, "Q", fs_div)
        out = q.copy()
        for n in names:
            if METRICS_BY_NAME[n].is_flow:
                out[n] = q[n].rolling(4, min_periods=4).sum()
        out.attrs = q.attrs
        return self._slice(out, kw.get("start"), kw.get("end"), kw.get("last"))

    def ratios(self, company: str, freq: str = "A", fs_div: str = "auto", start=None, end=None,
               last=None) -> pd.DataFrame:
        """수익성/안정성/성장성 비율(%). 분기(freq='Q')는 ROE/ROA를 TTM 기준으로 계산."""
        base = self.ttm(company, fs_div=fs_div) if freq == "Q" else self.metrics(company, freq="A", fs_div=fs_div)
        raw_q = self.metrics(company, freq="Q", fs_div=fs_div) if freq == "Q" else base
        lag = 4 if freq == "Q" else 1
        ni = base["net_income_owners"].fillna(base["net_income"])
        eq = base["equity_owners"].fillna(base["total_equity"])
        avg_eq = (eq + eq.shift(lag)) / 2
        avg_assets = (base["total_assets"] + base["total_assets"].shift(lag)) / 2
        r = pd.DataFrame(index=base.index)
        r["gross_margin"] = base["gross_profit"] / base["revenue"] * 100
        r["operating_margin"] = base["operating_income"] / base["revenue"] * 100
        r["net_margin"] = base["net_income"] / base["revenue"] * 100
        r["roe"] = ni / avg_eq.fillna(eq) * 100
        r["roa"] = base["net_income"] / avg_assets.fillna(base["total_assets"]) * 100
        r["debt_ratio"] = base["total_liabilities"] / base["total_equity"] * 100
        r["current_ratio"] = base["current_assets"] / base["current_liabilities"] * 100
        r["revenue_yoy"] = (raw_q["revenue"] / raw_q["revenue"].shift(lag) - 1) * 100
        r["operating_income_yoy"] = (raw_q["operating_income"] / raw_q["operating_income"].shift(lag) - 1) * 100
        r["fcf"] = base["cfo"] - base["capex"]
        if freq == "Q":
            r["revenue_qoq"] = (raw_q["revenue"] / raw_q["revenue"].shift(1) - 1) * 100
        r.attrs = {**base.attrs, "note": "분기 margin/ROE/ROA는 TTM 기준, yoy/qoq는 해당 분기 3개월 기준"}
        return self._slice(r, start, end, last)

    def compare(self, companies: list[str], metric: str, freq: str = "A", fs_div: str = "auto",
                ttm: bool = False, **kw) -> pd.DataFrame:
        cols = {}
        for c in companies:
            df = self.ttm(c, [metric], fs_div) if ttm else self.metrics(c, [metric], freq, fs_div)
            cols[df.attrs["corp_name"]] = df[metric]
        out = pd.DataFrame(cols).sort_index()
        return self._slice(out, kw.get("start"), kw.get("end"), kw.get("last"))

    # --- 임의 계정 / 기타 -------------------------------------------------
    def find_accounts(self, company: str, pattern: str, fs_div: str = "auto") -> pd.DataFrame:
        cc = self.corp_code(company)
        return self.sql(
            """SELECT sj_div, account_key, account_nm, COUNT(*) AS n_periods, MIN(period) AS first, MAX(period) AS last
               FROM fs_values WHERE corp_code = ? AND fs_div = ? AND (account_nm LIKE ? OR account_key LIKE ?)
               GROUP BY sj_div, account_key ORDER BY sj_div, n_periods DESC""",
            (cc, self._fs_div(cc, fs_div), f"%{pattern}%", f"%{pattern}%"),
        )

    def account(self, company: str, pattern: str, freq: str = "A", fs_div: str = "auto",
                sj_div: str | None = None, **kw) -> pd.DataFrame:
        """표준 지표에 없는 계정을 이름(부분일치)/account_id로 조회. 열=계정."""
        cc = self.corp_code(company)
        fs = self._fs_div(cc, fs_div)
        q = """SELECT period, sj_div || ':' || account_nm AS col, value FROM fs_values
               WHERE corp_code = ? AND fs_div = ? AND period_type = ? AND (account_nm LIKE ? OR account_key LIKE ?)"""
        params = [cc, fs, freq, f"%{pattern}%", f"%{pattern}%"]
        if sj_div:
            q += " AND sj_div = ?"
            params.append(sj_div)
        df = self.sql(q, params)
        if df.empty:
            return df
        wide = df.pivot_table(index="period", columns="col", values="value", aggfunc="first")
        wide = wide.reindex(_period_index(freq, wide.index))
        return self._slice(wide, kw.get("start"), kw.get("end"), kw.get("last"))

    def items(self, company: str, api: str, year: int | None = None, reprt_code: str | None = None) -> pd.DataFrame:
        """정기보고서 주요정보(report_items)를 표로 펼친다. api 예: alotMatter, empSttus, hyslrSttus."""
        cc = self.corp_code(company)
        q = "SELECT bsns_year, reprt_code, data FROM report_items WHERE corp_code = ? AND api = ?"
        params: list = [cc, api]
        if year:
            q += " AND bsns_year = ?"
            params.append(year)
        if reprt_code:
            q += " AND reprt_code = ?"
            params.append(reprt_code)
        rows = self.conn.execute(q + " ORDER BY bsns_year, reprt_code, seq", params).fetchall()
        return pd.DataFrame([{"bsns_year": r["bsns_year"], "reprt_code": r["reprt_code"], **json.loads(r["data"])}
                             for r in rows])

    def indices(self, company: str, pattern: str | None = None) -> pd.DataFrame:
        cc = self.corp_code(company)
        q = "SELECT bsns_year, reprt_code, idx_cl_nm, idx_nm, idx_val FROM fin_indices WHERE corp_code = ?"
        params = [cc]
        if pattern:
            q += " AND idx_nm LIKE ?"
            params.append(f"%{pattern}%")
        return self.sql(q + " ORDER BY bsns_year, reprt_code, idx_cl_code, idx_code", params)

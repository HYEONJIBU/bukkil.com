"""DB 내용을 엑셀(.xlsx)로 내보내기."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import config
from .accounts import KEY_REPORT_APIS, METRICS_BY_NAME, account_key, is_per_share
from .analysis import UNITS, Vault

RATIO_LABELS = {
    "gross_margin": "매출총이익률(%)", "operating_margin": "영업이익률(%)", "net_margin": "순이익률(%)",
    "roe": "ROE(%)", "roa": "ROA(%)", "debt_ratio": "부채비율(%)", "current_ratio": "유동비율(%)",
    "revenue_yoy": "매출 YoY(%)", "operating_income_yoy": "영업이익 YoY(%)", "fcf": "FCF(원)",
    "revenue_qoq": "매출 QoQ(%)",
}
STATEMENT_SHEETS = {"BS": "재무상태표", "IS": "손익계산서", "CIS": "포괄손익계산서", "CF": "현금흐름표"}
FREQ_LABEL = {"A": "연간", "Q": "분기"}

# DART의 ord가 계정코드 알파벳순인 회사가 있어 손익계산서는 표준 순서를 우선 적용
IS_ORDER = [
    "ifrs-full_Revenue", "ifrs-full_CostOfSales", "ifrs-full_GrossProfit",
    "dart_TotalSellingGeneralAdministrativeExpenses", "dart_OperatingIncomeLoss",
    "dart_OtherGains", "dart_OtherLosses", "ifrs-full_FinanceIncome", "ifrs-full_FinanceCosts",
    "ifrs-full_ShareOfProfitLossOfAssociatesAndJointVenturesAccountedForUsingEquityMethod",
    "ifrs-full_ProfitLossBeforeTax", "ifrs-full_IncomeTaxExpenseContinuingOperations", "ifrs-full_ProfitLoss",
    "ifrs-full_OtherComprehensiveIncome", "ifrs-full_ComprehensiveIncome",
    "ifrs-full_ProfitLossAttributableToOwnersOfParent", "ifrs-full_ProfitLossAttributableToNoncontrollingInterests",
    "ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent",
    "ifrs-full_ComprehensiveIncomeAttributableToNoncontrollingInterests",
    "ifrs-full_BasicEarningsLossPerShare", "ifrs-full_DilutedEarningsLossPerShare",
]


def _metric_table(vault: Vault, company, freq, fs_div, unit) -> pd.DataFrame:
    df = vault.metrics(company, freq=freq, fs_div=fs_div).dropna(axis=1, how="all")
    for c in df.columns:
        if c != "eps":
            df[c] = df[c] / UNITS[unit]
    df.columns = [f"{METRICS_BY_NAME[c].label}" + ("(원)" if c == "eps" else f"({unit}원)") for c in df.columns]
    return df.T


def _ratio_table(vault: Vault, company, freq, fs_div, unit) -> pd.DataFrame:
    df = vault.ratios(company, freq=freq, fs_div=fs_div).dropna(axis=1, how="all")
    if "fcf" in df:
        df["fcf"] = df["fcf"] / UNITS[unit]
    df.columns = [RATIO_LABELS.get(c, c).replace("FCF(원)", f"FCF({unit}원)") for c in df.columns]
    return df.T


def _statement_table(vault: Vault, corp_code, fs_div, sj_div, freq, unit) -> pd.DataFrame:
    vals = vault.sql(
        "SELECT account_key, account_nm, period, value FROM fs_values "
        "WHERE corp_code=? AND fs_div=? AND sj_div=? AND period_type=?",
        (corp_code, fs_div, sj_div, freq),
    )
    if vals.empty:
        return vals
    # 계정 순서: 가장 최근 보고서의 표시 순서(ord)
    order: dict[str, int] = {}
    for r in vault.conn.execute(
        "SELECT account_id, account_nm, ord FROM fs_raw WHERE corp_code=? AND fs_div=? AND sj_div=? "
        "ORDER BY reprt_code = '11011' DESC, bsns_year DESC, reprt_code DESC, ord",
        (corp_code, fs_div, sj_div),
    ):
        order.setdefault(account_key(r["account_id"], r["account_nm"]), len(order))
    if sj_div in ("IS", "CIS"):
        pos = {k: i for i, k in enumerate(IS_ORDER)}
        order = {k: (pos[k] if k in pos else len(IS_ORDER) + v) for k, v in order.items()}
    names = vals.drop_duplicates("account_key", keep="last").set_index("account_key")["account_nm"]
    wide = vals.pivot_table(index="account_key", columns="period", values="value", aggfunc="first")
    wide = wide.loc[sorted(wide.index, key=lambda k: order.get(k, 10**6))]
    per_share = [k for k in wide.index if is_per_share(k, names[k])]
    wide.loc[[k for k in wide.index if k not in per_share]] /= UNITS[unit]
    wide.index = [names[k] + (" (원)" if k in per_share else "") for k in wide.index]
    wide.index.name = f"계정 ({unit}원)"
    return wide


def export_company(company: str, path: str | Path | None = None, fs_div: str = "auto", unit: str = "억",
                   vault: Vault | None = None) -> Path:
    vault = vault or Vault()
    cc = vault.corp_code(company)
    fs = vault._fs_div(cc, fs_div)
    name = vault.corp_name(cc)
    path = Path(path) if path else config.db_path().parent / f"{name}_FinVault.xlsx"

    sheets: dict[str, pd.DataFrame] = {}
    for freq in ("A", "Q"):
        sheets[f"요약_{FREQ_LABEL[freq]}"] = _metric_table(vault, cc, freq, fs, unit)
        sheets[f"비율_{FREQ_LABEL[freq]}"] = _ratio_table(vault, cc, freq, fs, unit)
    for freq in ("A", "Q"):
        for sj, label in STATEMENT_SHEETS.items():
            sheets[f"{label}_{FREQ_LABEL[freq]}"] = _statement_table(vault, cc, fs, sj, freq, unit)
    for api, label in KEY_REPORT_APIS.items():
        df = vault.items(cc, api)
        if not df.empty:
            sheets[label[:31].replace("·", "")] = df
    indices = vault.indices(cc)
    if not indices.empty:
        sheets["DART재무지표"] = indices
    discl = vault.sql("SELECT rcept_dt, report_nm, rcept_no, "
                      "'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' || rcept_no AS url "
                      "FROM disclosures WHERE corp_code=? ORDER BY rcept_dt DESC", (cc,))
    if not discl.empty:
        sheets["정기공시목록"] = discl

    info = pd.DataFrame({
        "항목": ["회사", "고유번호", "재무제표 기준", "금액 단위", "분기 값 규칙"],
        "내용": [name, cc, "연결(CFS)" if fs == "CFS" else "별도(OFS)", f"{unit}원 (주당이익은 원)",
               "손익=해당 분기 3개월(Q4=연간-3분기누적), 현금흐름=누적 차감, 재무상태=분기말 잔액. "
               "분기 비율의 마진/ROE/ROA는 최근 4분기 합 기준"],
    })

    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        info.to_excel(xw, sheet_name="안내", index=False)
        for sheet, df in sheets.items():
            if df is None or df.empty:
                continue
            df.to_excel(xw, sheet_name=sheet, index=not sheet.startswith(("DART", "정기")) and
                        sheet.split("_")[0] in ("요약", "비율", *STATEMENT_SHEETS.values()))
            ws = xw.sheets[sheet]
            ws.freeze_panes = "B2"
            ws.column_dimensions["A"].width = 34
            for row in ws.iter_rows(min_row=2, min_col=2):
                for cell in row:
                    if isinstance(cell.value, float):
                        cell.number_format = "#,##0.00" if sheet.startswith("비율") else "#,##0"
    return path

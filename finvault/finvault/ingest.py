"""수집 파이프라인: DART API -> SQLite."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Callable, Iterable

from .accounts import INDEX_CLASSES, KEY_REPORT_APIS
from .client import REPORT_CODES, DartClient, DartError, RateLimitError
from . import transform

FIRST_FS_YEAR = 2015          # fnlttSinglAcntAll 제공 시작 연도
FIRST_INDEX_YEAR = 2023       # fnlttSinglIndx 제공 시작 연도
AMOUNT_FIELDS = ("thstrm_amount", "thstrm_add_amount", "frmtrm_amount", "frmtrm_q_amount",
                 "frmtrm_add_amount", "bfefrmtrm_amount")
FS_FIELDS = ("rcept_no", "sj_div", "sj_nm", "account_id", "account_nm", "account_detail", "ord", "currency",
             "thstrm_nm", "thstrm_amount", "thstrm_add_amount", "frmtrm_nm", "frmtrm_amount", "frmtrm_q_nm",
             "frmtrm_q_amount", "frmtrm_add_amount", "bfefrmtrm_nm", "bfefrmtrm_amount")

Logger = Callable[[str], None]


def parse_amount(value) -> float | None:
    if value is None:
        return None
    s = str(value).replace(",", "").strip()
    if s in ("", "-"):
        return None
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


# --- 회사 식별 ---------------------------------------------------------------
def sync_corp_codes(conn: sqlite3.Connection, client: DartClient) -> int:
    items = client.corp_codes()
    with conn:
        conn.execute("DELETE FROM corp_codes")
        conn.executemany(
            "INSERT INTO corp_codes VALUES (?,?,?,?,?)",
            [(i.get("corp_code"), i.get("corp_name"), i.get("corp_eng_name"),
              (i.get("stock_code") or "").strip() or None, i.get("modify_date")) for i in items],
        )
    return len(items)


def search_corps(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[sqlite3.Row]:
    q = query.strip()
    return conn.execute(
        """SELECT corp_code, corp_name, stock_code FROM corp_codes
           WHERE corp_code = ?1 OR stock_code = ?1 OR corp_name LIKE '%' || ?1 || '%'
              OR corp_eng_name LIKE '%' || ?1 || '%'
           ORDER BY (corp_name = ?1 OR stock_code = ?1 OR corp_code = ?1) DESC,
                    (stock_code IS NOT NULL) DESC, length(corp_name)
           LIMIT ?2""",
        (q, limit),
    ).fetchall()


def resolve_corp(conn: sqlite3.Connection, query: str) -> sqlite3.Row:
    """회사명/종목코드/고유번호 -> corp_codes 행. 정확히 일치하는 것이 없고 후보가 여럿이면 에러."""
    rows = search_corps(conn, query)
    if not rows:
        raise LookupError(f"'{query}'에 해당하는 회사를 찾지 못했습니다. (corp-sync를 먼저 실행했나요?)")
    q = query.strip()
    exact = [r for r in rows if q in (r["corp_name"], r["stock_code"], r["corp_code"])]
    listed_exact = [r for r in exact if r["stock_code"]]
    if listed_exact:
        return listed_exact[0]
    if len(exact) == 1:
        return exact[0]
    if len(rows) == 1:
        return rows[0]
    cands = ", ".join(f"{r['corp_name']}({r['stock_code'] or r['corp_code']})" for r in rows[:10])
    raise LookupError(f"'{query}' 후보가 여러 개입니다: {cands}")


# --- 수집 --------------------------------------------------------------------
def _already_ok(conn, corp_code, dataset, year, reprt_code, variant) -> bool:
    row = conn.execute(
        "SELECT status FROM fetch_log WHERE corp_code=? AND dataset=? AND bsns_year=? AND reprt_code=? AND variant=?",
        (corp_code, dataset, year, reprt_code, variant),
    ).fetchone()
    return bool(row) and row["status"] == "ok"


def _log(conn, corp_code, dataset, year, reprt_code, variant, status, message=""):
    conn.execute(
        "INSERT OR REPLACE INTO fetch_log (corp_code, dataset, bsns_year, reprt_code, variant, status, message) "
        "VALUES (?,?,?,?,?,?,?)",
        (corp_code, dataset, year, reprt_code, variant, status, message),
    )


def _available(year: int, reprt_code: str, today: dt.date) -> bool:
    """제출 기한 전인 보고서는 호출하지 않는다 (12월 결산 기준 대략적 판단)."""
    if year < today.year - 1:
        return True
    q = REPORT_CODES[reprt_code][1]
    # 분기/반기: 분기말 + 45일, 사업보고서: 다음 해 3월 말
    period_end = dt.date(year, 3 * q, 30 if q in (2, 3) else 31) if q < 4 else dt.date(year, 12, 31)
    lag = 90 if q == 4 else 45
    return today >= period_end + dt.timedelta(days=lag)


def save_company(conn, client: DartClient, corp_code: str) -> dict:
    info = client.company(corp_code)
    with conn:
        conn.execute(
            """INSERT OR REPLACE INTO companies
               (corp_code, corp_name, corp_name_eng, stock_name, stock_code, ceo_nm, corp_cls,
                induty_code, est_dt, acc_mt, hm_url, raw_json, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (corp_code, info.get("corp_name"), info.get("corp_name_eng"), info.get("stock_name"),
             (info.get("stock_code") or "").strip() or None, info.get("ceo_nm"), info.get("corp_cls"),
             info.get("induty_code"), info.get("est_dt"), info.get("acc_mt"), info.get("hm_url"),
             json.dumps(info, ensure_ascii=False)),
        )
    return info


def ingest_financials(conn, client, corp_code, years: Iterable[int], fs_divs=("CFS", "OFS"),
                      refresh=False, log: Logger = print, today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    fetched = 0
    for year in years:
        if year < FIRST_FS_YEAR:
            continue
        for reprt_code in REPORT_CODES:
            if not _available(year, reprt_code, today):
                continue
            for fs_div in fs_divs:
                if not refresh and _already_ok(conn, corp_code, "fs", year, reprt_code, fs_div):
                    continue
                label = f"{year} {REPORT_CODES[reprt_code][0]} {fs_div}"
                try:
                    rows = client.financial_statements(corp_code, year, reprt_code, fs_div)
                except RateLimitError:
                    raise
                except DartError as e:
                    with conn:
                        _log(conn, corp_code, "fs", year, reprt_code, fs_div, "error", str(e))
                    log(f"  ! {label}: {e}")
                    continue
                with conn:
                    conn.execute("DELETE FROM fs_raw WHERE corp_code=? AND bsns_year=? AND reprt_code=? AND fs_div=?",
                                 (corp_code, year, reprt_code, fs_div))
                    conn.executemany(
                        f"INSERT INTO fs_raw (corp_code, bsns_year, reprt_code, fs_div, {', '.join(FS_FIELDS)}) "
                        f"VALUES ({', '.join('?' * (4 + len(FS_FIELDS)))})",
                        [(corp_code, year, reprt_code, fs_div,
                          *[parse_amount(r.get(f)) if f in AMOUNT_FIELDS
                            else (int(r[f]) if f == "ord" and str(r.get(f, "")).isdigit() else r.get(f))
                            for f in FS_FIELDS])
                         for r in rows],
                    )
                    _log(conn, corp_code, "fs", year, reprt_code, fs_div, "ok" if rows else "nodata")
                fetched += len(rows)
                log(f"  {label}: {len(rows)}행" if rows else f"  {label}: 데이터 없음")
    return fetched


def ingest_key_reports(conn, client, corp_code, years: Iterable[int], reprt_codes=("11011",),
                       apis: Iterable[str] = KEY_REPORT_APIS, refresh=False, log: Logger = print,
                       today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    total = 0
    for year in years:
        for reprt_code in reprt_codes:
            if not _available(year, reprt_code, today):
                continue
            for api in apis:
                if not refresh and _already_ok(conn, corp_code, "key", year, reprt_code, api):
                    continue
                try:
                    rows = client.key_report(api, corp_code, year, reprt_code)
                except RateLimitError:
                    raise
                except DartError as e:
                    with conn:
                        _log(conn, corp_code, "key", year, reprt_code, api, "error", str(e))
                    continue
                with conn:
                    conn.execute("DELETE FROM report_items WHERE corp_code=? AND api=? AND bsns_year=? AND reprt_code=?",
                                 (corp_code, api, year, reprt_code))
                    conn.executemany(
                        "INSERT INTO report_items VALUES (?,?,?,?,?,?,?)",
                        [(corp_code, api, year, reprt_code, i, r.get("rcept_no"), json.dumps(r, ensure_ascii=False))
                         for i, r in enumerate(rows)],
                    )
                    _log(conn, corp_code, "key", year, reprt_code, api, "ok" if rows else "nodata")
                total += len(rows)
        log(f"  {year} 주요정보: 누적 {total}행")
    return total


def ingest_indices(conn, client, corp_code, years: Iterable[int], refresh=False, log: Logger = print,
                   today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    total = 0
    for year in years:
        if year < FIRST_INDEX_YEAR:
            continue
        for reprt_code in REPORT_CODES:
            if not _available(year, reprt_code, today):
                continue
            for cl in INDEX_CLASSES:
                if not refresh and _already_ok(conn, corp_code, "indx", year, reprt_code, cl):
                    continue
                try:
                    rows = client.financial_indices(corp_code, year, reprt_code, cl)
                except RateLimitError:
                    raise
                except DartError as e:
                    with conn:
                        _log(conn, corp_code, "indx", year, reprt_code, cl, "error", str(e))
                    continue
                with conn:
                    conn.executemany(
                        "INSERT OR REPLACE INTO fin_indices VALUES (?,?,?,?,?,?,?,?,?)",
                        [(corp_code, year, reprt_code, cl, r.get("idx_cl_nm"), r.get("idx_code"), r.get("idx_nm"),
                          parse_amount(r.get("idx_val")), r.get("stlm_dt")) for r in rows],
                    )
                    _log(conn, corp_code, "indx", year, reprt_code, cl, "ok" if rows else "nodata")
                total += len(rows)
    log(f"  주요 재무지표: {total}행")
    return total


def ingest_disclosures(conn, client, corp_code, start_year: int, log: Logger = print) -> int:
    rows = client.disclosures(corp_code, f"{start_year}0101", dt.date.today().strftime("%Y%m%d"))
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO disclosures VALUES (?,?,?,?,?,?,?)",
            [(r.get("rcept_no"), r.get("corp_code"), r.get("corp_name"), r.get("report_nm"), r.get("flr_nm"),
              r.get("rcept_dt"), r.get("rm")) for r in rows],
        )
    log(f"  정기공시 목록: {len(rows)}건")
    return len(rows)


def ingest_company(conn, client: DartClient, query: str, start_year: int, end_year: int,
                   fs_divs=("CFS", "OFS"), extras=True, refresh=False, log: Logger = print) -> str:
    if not conn.execute("SELECT 1 FROM corp_codes LIMIT 1").fetchone():
        log("DART 회사 고유번호 목록을 내려받는 중... (최초 1회, 수 분 걸릴 수 있음)")
        sync_corp_codes(conn, client)
    corp = resolve_corp(conn, query)
    corp_code = corp["corp_code"]
    log(f"[{corp['corp_name']}] corp_code={corp_code} stock_code={corp['stock_code']} ({start_year}~{end_year})")
    save_company(conn, client, corp_code)
    years = range(start_year, end_year + 1)
    ingest_financials(conn, client, corp_code, years, fs_divs, refresh, log)
    if extras:
        ingest_key_reports(conn, client, corp_code, years, refresh=refresh, log=log)
        ingest_indices(conn, client, corp_code, years, refresh=refresh, log=log)
        ingest_disclosures(conn, client, corp_code, start_year, log=log)
    n_values, n_metrics = transform.rebuild(conn, corp_code)
    log(f"  변환 완료: fs_values {n_values}행, metrics {n_metrics}행")
    return corp_code

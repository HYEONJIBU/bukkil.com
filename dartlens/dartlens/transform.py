"""fs_raw -> fs_values(기간별 값) -> metrics(표준 지표) 변환.

DART 분기/반기 보고서 금액 규칙
- BS  : thstrm_amount = 해당 시점 잔액
- IS  : thstrm_amount = 해당 3개월, thstrm_add_amount = 연초부터 누적
         (사업보고서는 thstrm_amount = 연간)
- CF  : thstrm_amount = 연초부터 누적
따라서 4분기(Q4) 손익/현금흐름 = 연간 - 3분기 누적 으로 계산한다.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from .accounts import METRICS, account_key, is_per_share, normalize_name
from .client import QUARTER_OF_REPORT

STATEMENTS = ("BS", "IS", "CIS", "CF")


def period_label(period_type: str, year: int, q: int) -> str:
    return f"{year}A" if period_type == "A" else f"{year}Q{q}"


def derive_periods(sj_div: str, by_quarter: dict[int, tuple[float | None, float | None]],
                   per_share: bool = False) -> list[tuple[str, int, str, float]]:
    """by_quarter: {1..4: (thstrm_amount, thstrm_add_amount)} (4 = 사업보고서).

    반환: [(period_type, fiscal_q, basis, value)]
    """
    out: list[tuple[str, int, str, float]] = []

    if sj_div == "BS":
        for q in sorted(by_quarter):
            amt = by_quarter[q][0]
            if amt is not None:
                out.append(("Q", q, "PIT", amt))
        if 4 in by_quarter and by_quarter[4][0] is not None:
            out.append(("A", 0, "PIT", by_quarter[4][0]))
        return out

    three_m: dict[int, float] = {}
    cum: dict[int, float] = {}
    for q, (amt, add) in by_quarter.items():
        if q == 4:
            if amt is not None:
                cum[4] = amt
        elif sj_div == "CF":
            val = add if add is not None else amt
            if val is not None:
                cum[q] = val
        else:
            if amt is not None:
                three_m[q] = amt
            if add is not None:
                cum[q] = add
            elif q == 1 and amt is not None:
                cum[1] = amt

    # 누적이 비어 있으면 3개월 값으로 채움
    for q in (1, 2, 3):
        if q not in cum and q in three_m and (q == 1 or (q - 1) in cum):
            cum[q] = three_m[q] + (cum[q - 1] if q > 1 else 0)

    for q in (1, 2, 3, 4):
        val = three_m.get(q)
        if val is None and q in cum and not per_share:
            if q == 1:
                val = cum[1]
            elif (q - 1) in cum:
                val = cum[q] - cum[q - 1]
        if val is not None:
            out.append(("Q", q, "3M", val))
    if 4 in cum:
        out.append(("A", 0, "CUM", cum[4]))
    return out


def build_fs_values(conn: sqlite3.Connection, corp_code: str) -> int:
    rows = conn.execute(
        f"""SELECT fs_div, sj_div, bsns_year, reprt_code, account_id, account_nm,
                   thstrm_amount, thstrm_add_amount
            FROM fs_raw WHERE corp_code = ? AND sj_div IN ({','.join('?' * len(STATEMENTS))})
            ORDER BY fs_div, sj_div, bsns_year, reprt_code, ord""",
        (corp_code, *STATEMENTS),
    ).fetchall()

    # (fs_div, sj_div, key) -> year -> q -> (amt, add);  같은 보고서 내 중복 계정은 첫 행만 사용
    grouped: dict[tuple, dict[int, dict[int, tuple]]] = defaultdict(lambda: defaultdict(dict))
    names: dict[tuple, tuple[str, str]] = {}
    for r in rows:
        key = (r["fs_div"], r["sj_div"], account_key(r["account_id"], r["account_nm"]))
        q = QUARTER_OF_REPORT[r["reprt_code"]]
        if q in grouped[key][r["bsns_year"]]:
            continue
        grouped[key][r["bsns_year"]][q] = (r["thstrm_amount"], r["thstrm_add_amount"])
        names[key] = (r["account_id"], r["account_nm"])  # 최신 연도 명칭이 남음

    records = []
    for key, years in grouped.items():
        fs_div, sj_div, acc_key = key
        acc_id, acc_nm = names[key]
        per_share = is_per_share(acc_id, acc_nm)
        for year, by_q in years.items():
            for ptype, q, basis, value in derive_periods(sj_div, by_q, per_share):
                records.append((corp_code, fs_div, sj_div, acc_key, acc_id, acc_nm, ptype, year, q,
                                period_label(ptype, year, q), basis, value))

    with conn:
        conn.execute("DELETE FROM fs_values WHERE corp_code = ?", (corp_code,))
        conn.executemany("INSERT INTO fs_values VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", records)
    return len(records)


def build_metrics(conn: sqlite3.Connection, corp_code: str) -> int:
    rows = conn.execute(
        "SELECT fs_div, sj_div, account_key, account_id, account_nm, period_type, fiscal_year, fiscal_q, value "
        "FROM fs_values WHERE corp_code = ?",
        (corp_code,),
    ).fetchall()

    # 기간별 조회 인덱스: (fs_div, ptype, year, q) -> {(sj_div, id): (value, label)}, {(sj_div, name): ...}
    by_id: dict[tuple, dict] = defaultdict(dict)
    by_nm: dict[tuple, dict] = defaultdict(dict)
    for r in rows:
        pkey = (r["fs_div"], r["period_type"], r["fiscal_year"], r["fiscal_q"])
        entry = (r["value"], r["account_nm"])
        by_id[pkey].setdefault((r["sj_div"], r["account_id"]), entry)
        by_nm[pkey].setdefault((r["sj_div"], normalize_name(r["account_nm"])), entry)

    records = []
    for pkey in by_id:
        fs_div, ptype, year, q = pkey
        for m in METRICS:
            found = None
            for sj in m.statements:
                for acc_id in m.ids:
                    found = by_id[pkey].get((sj, acc_id))
                    if found:
                        break
                if not found:
                    for nm in m.names:
                        found = by_nm[pkey].get((sj, normalize_name(nm)))
                        if found:
                            break
                if found:
                    break
            if found:
                records.append((corp_code, fs_div, m.name, ptype, year, q,
                                period_label(ptype, year, q), found[0], found[1]))

    with conn:
        conn.execute("DELETE FROM metrics WHERE corp_code = ?", (corp_code,))
        conn.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?,?,?,?)", records)
    return len(records)


def rebuild(conn: sqlite3.Connection, corp_code: str) -> tuple[int, int]:
    return build_fs_values(conn, corp_code), build_metrics(conn, corp_code)

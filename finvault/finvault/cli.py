"""명령줄 인터페이스: python -m finvault <command> ..."""

from __future__ import annotations

import argparse
import datetime as dt
import functools
import sys

import pandas as pd

from . import config, db, export, ingest, transform
from .accounts import KEY_REPORT_APIS, METRICS
from .analysis import UNITS, Vault


def _client():
    from .client import DartClient
    return DartClient(config.api_key())


def _print(df: pd.DataFrame, fmt: str = "{:,.1f}") -> None:
    if df is None or df.empty:
        print("(데이터 없음)")
        return
    for k in ("corp_name", "fs_div", "freq", "unit", "note"):
        if k in df.attrs:
            print(f"{k}: {df.attrs[k]}", end="  ")
    if df.attrs:
        print()
    with pd.option_context("display.max_rows", 500, "display.max_columns", 50, "display.width", 250):
        print(df.to_string(float_format=lambda v: fmt.format(v)))


def main(argv=None) -> None:
    try:
        _main(argv)
    except (LookupError, RuntimeError) as e:
        sys.exit(f"오류: {e.args[0] if e.args else e}")


def _main(argv=None) -> None:
    this_year = dt.date.today().year
    p = argparse.ArgumentParser(prog="finvault", description="DART 재무 DB & 분석")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("corp-sync", help="DART 고유번호 전체 목록 내려받기")
    s = sub.add_parser("search", help="회사 검색")
    s.add_argument("query")

    s = sub.add_parser("ingest", help="회사 재무제표/정기보고서 수집 후 DB화")
    s.add_argument("companies", nargs="+", help="회사명, 종목코드 또는 고유번호")
    s.add_argument("--from", dest="start", type=int, default=ingest.FIRST_FS_YEAR)
    s.add_argument("--to", dest="end", type=int, default=this_year)
    s.add_argument("--fs", default="CFS,OFS", help="CFS(연결), OFS(별도)")
    s.add_argument("--no-extras", action="store_true", help="주요정보/지표/공시목록 수집 생략")
    s.add_argument("--refresh", action="store_true", help="이미 수집한 기간도 다시 받기")
    s.add_argument("--excel", action="store_true", help="수집 후 엑셀 파일도 만들기")

    s = sub.add_parser("export", help="엑셀(.xlsx)로 내보내기")
    s.add_argument("companies", nargs="+")
    s.add_argument("-o", "--output", help="파일 경로 (회사 1곳일 때). 기본: data/<회사명>_FinVault.xlsx")
    s.add_argument("--fs", default="auto")
    s.add_argument("--unit", default="억", choices=list(UNITS))

    s = sub.add_parser("rebuild", help="fs_raw로부터 fs_values/metrics 재계산")
    s.add_argument("companies", nargs="*")

    sub.add_parser("status", help="DB에 있는 회사와 기간")

    s = sub.add_parser("show", help="표준 지표 조회")
    s.add_argument("company")
    s.add_argument("-m", "--metrics", help="쉼표 구분. 예: revenue,operating_income (기본: 전체)")
    s.add_argument("-f", "--freq", choices=["A", "Q", "TTM"], default="A")
    s.add_argument("--fs", default="auto")
    s.add_argument("--last", type=int)
    s.add_argument("--unit", default="억", choices=list(UNITS))

    s = sub.add_parser("ratios", help="재무비율")
    s.add_argument("company")
    s.add_argument("-f", "--freq", choices=["A", "Q"], default="A")
    s.add_argument("--fs", default="auto")
    s.add_argument("--last", type=int)

    s = sub.add_parser("account", help="임의 계정 조회 (부분일치)")
    s.add_argument("company")
    s.add_argument("pattern")
    s.add_argument("-f", "--freq", choices=["A", "Q"], default="A")
    s.add_argument("--fs", default="auto")
    s.add_argument("--last", type=int)

    s = sub.add_parser("items", help="정기보고서 주요정보: " + ", ".join(KEY_REPORT_APIS))
    s.add_argument("company")
    s.add_argument("api")
    s.add_argument("--year", type=int)

    s = sub.add_parser("sql", help="SQL 직접 실행")
    s.add_argument("query")

    sub.add_parser("list-metrics", help="표준 지표 목록")

    args = p.parse_args(argv)

    if args.cmd == "corp-sync":
        n = ingest.sync_corp_codes(db.connect(), _client())
        print(f"{n:,}개 회사 고유번호 저장")
    elif args.cmd == "search":
        conn = db.connect()
        if not conn.execute("SELECT 1 FROM corp_codes LIMIT 1").fetchone():
            ingest.sync_corp_codes(conn, _client())
        for r in ingest.search_corps(conn, args.query):
            print(f"{r['corp_code']}  {r['stock_code'] or '------'}  {r['corp_name']}")
    elif args.cmd == "ingest":
        conn, client = db.connect(), _client()
        for c in args.companies:
            ingest.ingest_company(conn, client, c, args.start, args.end, tuple(args.fs.split(",")),
                                  extras=not args.no_extras, refresh=args.refresh,
                                  log=functools.partial(print, flush=True))
            if args.excel:
                print("엑셀 저장:", export.export_company(c), flush=True)
    elif args.cmd == "rebuild":
        conn = db.connect()
        codes = [ingest.resolve_corp(conn, c)["corp_code"] for c in args.companies] or \
                [r[0] for r in conn.execute("SELECT DISTINCT corp_code FROM fs_raw")]
        for cc in codes:
            print(cc, transform.rebuild(conn, cc))
    elif args.cmd == "export":
        for c in args.companies:
            out = args.output if len(args.companies) == 1 else None
            print("엑셀 저장:", export.export_company(c, out, args.fs, args.unit))
    elif args.cmd == "list-metrics":
        for m in METRICS:
            print(f"{m.name:22s} {m.label:12s} {'/'.join(m.statements)}")
    else:
        vault = Vault()
        if args.cmd == "status":
            _print(vault.companies())
        elif args.cmd == "show":
            ms = args.metrics.split(",") if args.metrics else None
            if args.freq == "TTM":
                df = vault.ttm(args.company, ms, args.fs, last=args.last)
                df = df.drop(columns=[c for c in df.columns if c == "eps"]) / UNITS[args.unit]
            else:
                df = vault.metrics(args.company, ms, args.freq, args.fs, last=args.last, unit=args.unit)
            _print(df.T if len(df) <= 12 else df)
        elif args.cmd == "ratios":
            _print(vault.ratios(args.company, args.freq, args.fs, last=args.last).T, "{:,.2f}")
        elif args.cmd == "account":
            _print(vault.account(args.company, args.pattern, args.freq, args.fs, last=args.last) / 1e8)
        elif args.cmd == "items":
            _print(vault.items(args.company, args.api, args.year))
        elif args.cmd == "sql":
            _print(vault.sql(args.query), "{:,.2f}")


if __name__ == "__main__":
    sys.exit(main())

"""SQLite 스키마와 연결.

테이블 개요
- corp_codes    : DART 전체 고유번호(회사명/종목코드 검색용)
- companies     : 수집 대상 기업 개황
- fs_raw        : 재무제표 원본 행 (API 응답 그대로, 금액만 숫자화)
- fs_values     : fs_raw에서 계산한 기간별 값 (연간 A / 분기 Q, 모든 계정)
- metrics       : fs_values에서 뽑은 표준 지표(매출액, 영업이익, 자산총계 ...)
- report_items  : 정기보고서 주요정보(배당, 최대주주, 직원 등) — 행 단위 JSON
- fin_indices   : DART 제공 주요 재무지표
- disclosures   : 정기공시 목록
- fetch_log     : 수집 이력(재수집 방지)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS corp_codes (
    corp_code TEXT PRIMARY KEY,
    corp_name TEXT,
    corp_eng_name TEXT,
    stock_code TEXT,
    modify_date TEXT
);
CREATE INDEX IF NOT EXISTS idx_corp_codes_name ON corp_codes(corp_name);
CREATE INDEX IF NOT EXISTS idx_corp_codes_stock ON corp_codes(stock_code);

CREATE TABLE IF NOT EXISTS companies (
    corp_code TEXT PRIMARY KEY,
    corp_name TEXT,
    corp_name_eng TEXT,
    stock_name TEXT,
    stock_code TEXT,
    ceo_nm TEXT,
    corp_cls TEXT,          -- Y 유가증권, K 코스닥, N 코넥스, E 기타
    induty_code TEXT,
    est_dt TEXT,
    acc_mt TEXT,            -- 결산월
    hm_url TEXT,
    raw_json TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fetch_log (
    corp_code TEXT,
    dataset TEXT,
    bsns_year INTEGER,
    reprt_code TEXT,
    variant TEXT,           -- fs_div, API 이름, 지표 분류 등
    status TEXT,            -- ok / nodata / error
    message TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (corp_code, dataset, bsns_year, reprt_code, variant)
);

CREATE TABLE IF NOT EXISTS fs_raw (
    corp_code TEXT,
    bsns_year INTEGER,
    reprt_code TEXT,
    fs_div TEXT,            -- CFS 연결 / OFS 별도
    rcept_no TEXT,
    sj_div TEXT,            -- BS, IS, CIS, CF, SCE
    sj_nm TEXT,
    account_id TEXT,
    account_nm TEXT,
    account_detail TEXT,
    ord INTEGER,
    currency TEXT,
    thstrm_nm TEXT,
    thstrm_amount REAL,
    thstrm_add_amount REAL,
    frmtrm_nm TEXT,
    frmtrm_amount REAL,
    frmtrm_q_nm TEXT,
    frmtrm_q_amount REAL,
    frmtrm_add_amount REAL,
    bfefrmtrm_nm TEXT,
    bfefrmtrm_amount REAL
);
CREATE INDEX IF NOT EXISTS idx_fs_raw_key ON fs_raw(corp_code, bsns_year, reprt_code, fs_div);

CREATE TABLE IF NOT EXISTS fs_values (
    corp_code TEXT,
    fs_div TEXT,
    sj_div TEXT,
    account_key TEXT,       -- account_id 또는 'nm:<정규화된 계정명>'
    account_id TEXT,
    account_nm TEXT,
    period_type TEXT,       -- A 연간 / Q 분기
    fiscal_year INTEGER,
    fiscal_q INTEGER,       -- 연간은 0
    period TEXT,            -- 2024A, 2024Q3
    basis TEXT,             -- PIT 시점잔액 / 3M 분기 / CUM 연간누적
    value REAL,
    PRIMARY KEY (corp_code, fs_div, sj_div, account_key, period_type, fiscal_year, fiscal_q)
);
CREATE INDEX IF NOT EXISTS idx_fs_values_nm ON fs_values(corp_code, account_nm);

CREATE TABLE IF NOT EXISTS metrics (
    corp_code TEXT,
    fs_div TEXT,
    metric TEXT,
    period_type TEXT,
    fiscal_year INTEGER,
    fiscal_q INTEGER,
    period TEXT,
    value REAL,
    source_account TEXT,
    PRIMARY KEY (corp_code, fs_div, metric, period_type, fiscal_year, fiscal_q)
);

CREATE TABLE IF NOT EXISTS report_items (
    corp_code TEXT,
    api TEXT,
    bsns_year INTEGER,
    reprt_code TEXT,
    seq INTEGER,
    rcept_no TEXT,
    data TEXT,              -- 원본 행 JSON (json_extract로 조회)
    PRIMARY KEY (corp_code, api, bsns_year, reprt_code, seq)
);

CREATE TABLE IF NOT EXISTS fin_indices (
    corp_code TEXT,
    bsns_year INTEGER,
    reprt_code TEXT,
    idx_cl_code TEXT,
    idx_cl_nm TEXT,
    idx_code TEXT,
    idx_nm TEXT,
    idx_val REAL,
    stlm_dt TEXT,
    PRIMARY KEY (corp_code, bsns_year, reprt_code, idx_cl_code, idx_code)
);

CREATE TABLE IF NOT EXISTS disclosures (
    rcept_no TEXT PRIMARY KEY,
    corp_code TEXT,
    corp_name TEXT,
    report_nm TEXT,
    flr_nm TEXT,
    rcept_dt TEXT,
    rm TEXT
);

CREATE VIEW IF NOT EXISTS v_metrics AS
    SELECT c.corp_name, m.* FROM metrics m LEFT JOIN companies c USING (corp_code);
CREATE VIEW IF NOT EXISTS v_fs_values AS
    SELECT c.corp_name, v.* FROM fs_values v LEFT JOIN companies c USING (corp_code);
"""


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(path) if path else config.db_path()
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn

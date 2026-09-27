import pytest

from dartlens.accounts import account_key, normalize_name
from dartlens.ingest import parse_amount
from dartlens.transform import derive_periods


def as_dict(out):
    return {(p, q): (b, v) for p, q, b, v in out}


def test_normalize_name():
    assert normalize_name("Ⅰ. 영업이익(손실)") == "영업이익"
    assert normalize_name("반기순이익") == "당기순이익"
    assert normalize_name("3분기순손실") == "당기순손실"
    assert normalize_name("유형자산의 취득") == "유형자산의취득"
    assert account_key("-표준계정코드 미사용-", "분기순이익") == "nm:당기순이익"
    assert account_key("ifrs-full_Revenue", "매출액") == "ifrs-full_Revenue"


def test_parse_amount():
    assert parse_amount("1,234") == 1234
    assert parse_amount("-500") == -500
    assert parse_amount("(300)") == -300
    assert parse_amount("") is None
    assert parse_amount("-") is None


def test_income_statement_q4_is_annual_minus_q3_cumulative():
    out = as_dict(derive_periods("IS", {1: (100, 100), 2: (110, 210), 3: (120, 330), 4: (460, None)}))
    assert out[("Q", 1)] == ("3M", 100)
    assert out[("Q", 2)] == ("3M", 110)
    assert out[("Q", 3)] == ("3M", 120)
    assert out[("Q", 4)] == ("3M", 130)
    assert out[("A", 0)] == ("CUM", 460)


def test_income_statement_missing_cumulative_filled_from_3m():
    out = as_dict(derive_periods("IS", {1: (100, None), 2: (110, None), 3: (120, None), 4: (460, None)}))
    assert out[("Q", 4)] == ("3M", 130)


def test_cash_flow_cumulative_differenced():
    out = as_dict(derive_periods("CF", {1: (10, None), 2: (25, None), 3: (45, None), 4: (70, None)}))
    assert [out[("Q", q)][1] for q in (1, 2, 3, 4)] == [10, 15, 20, 25]
    assert out[("A", 0)] == ("CUM", 70)


def test_balance_sheet_point_in_time():
    out = as_dict(derive_periods("BS", {1: (1, None), 2: (2, None), 4: (4, None)}))
    assert out[("Q", 1)] == ("PIT", 1)
    assert ("Q", 3) not in out
    assert out[("Q", 4)] == ("PIT", 4)
    assert out[("A", 0)] == ("PIT", 4)


def test_q4_not_derived_when_q3_missing():
    out = as_dict(derive_periods("IS", {1: (100, 100), 2: (110, 210), 4: (460, None)}))
    assert ("Q", 4) not in out
    assert ("Q", 3) not in out


def test_per_share_q4_not_derived():
    out = as_dict(derive_periods("IS", {1: (10, 10), 2: (11, 21), 3: (12, 33), 4: (46, None)}, per_share=True))
    assert ("Q", 4) not in out
    assert out[("A", 0)][1] == 46

"""계정 정규화와 표준 지표 정의.

회사마다 계정명이 조금씩 다르므로 (영업이익 / 영업이익(손실) / 분기순이익 ...)
표준 지표는 account_id(IFRS/DART 표준계정코드) 우선, 없으면 정규화된 계정명으로 찾는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NON_STANDARD_PREFIX = "-표준계정코드"

_LEADING_NUMBERING = re.compile(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|\d+|\(\d+\)|[가-하])[\.\)]\s*|^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*")
_PARENS_PL = re.compile(r"\((?:손실|이익|손익)\)")


def normalize_name(name: str | None) -> str:
    """'Ⅰ. 영업이익(손실)' -> '영업이익', '반기순이익' -> '당기순이익'."""
    s = re.sub(r"\s+", "", name or "")
    s = _LEADING_NUMBERING.sub("", s)
    s = _PARENS_PL.sub("", s)
    s = re.sub(r"(?:\d?분기|반기)순(이익|손실|손익)", r"당기순\1", s)
    return s


def is_standard_id(account_id: str | None) -> bool:
    return bool(account_id) and not account_id.startswith(NON_STANDARD_PREFIX)


def account_key(account_id: str | None, account_nm: str | None) -> str:
    if is_standard_id(account_id):
        return account_id
    return "nm:" + normalize_name(account_nm)


def is_per_share(account_id: str | None, account_nm: str | None) -> bool:
    return "PerShare" in (account_id or "") or "주당" in (account_nm or "")


@dataclass(frozen=True)
class MetricDef:
    name: str
    label: str
    statements: tuple[str, ...]
    ids: tuple[str, ...]
    names: tuple[str, ...] = ()

    @property
    def is_flow(self) -> bool:
        return self.statements[0] != "BS"


_IS = ("IS", "CIS")
_BS = ("BS",)
_CF = ("CF",)

METRICS: tuple[MetricDef, ...] = (
    # 손익
    MetricDef("revenue", "매출액", _IS, ("ifrs-full_Revenue",), ("매출액", "수익(매출액)", "영업수익", "매출")),
    MetricDef("cost_of_sales", "매출원가", _IS, ("ifrs-full_CostOfSales",), ("매출원가",)),
    MetricDef("gross_profit", "매출총이익", _IS, ("ifrs-full_GrossProfit",), ("매출총이익",)),
    MetricDef("sga", "판매비와관리비", _IS, ("dart_TotalSellingGeneralAdministrativeExpenses",), ("판매비와관리비",)),
    MetricDef("operating_income", "영업이익", _IS, ("dart_OperatingIncomeLoss",), ("영업이익", "영업손실")),
    MetricDef("pretax_income", "법인세차감전순이익", _IS, ("ifrs-full_ProfitLossBeforeTax",),
              ("법인세비용차감전순이익", "법인세비용차감전순손실", "법인세차감전순이익")),
    MetricDef("income_tax", "법인세비용", _IS, ("ifrs-full_IncomeTaxExpenseContinuingOperations",), ("법인세비용",)),
    MetricDef("net_income", "당기순이익", _IS, ("ifrs-full_ProfitLoss",), ("당기순이익", "당기순손실")),
    MetricDef("net_income_owners", "지배주주순이익", _IS, ("ifrs-full_ProfitLossAttributableToOwnersOfParent",),
              ("지배기업의소유주에게귀속되는당기순이익", "지배기업소유주지분", "지배기업의소유주지분")),
    MetricDef("eps", "기본주당이익", _IS, ("ifrs-full_BasicEarningsLossPerShare",), ("기본주당이익", "기본주당순이익")),
    # 재무상태
    MetricDef("total_assets", "자산총계", _BS, ("ifrs-full_Assets",), ("자산총계",)),
    MetricDef("current_assets", "유동자산", _BS, ("ifrs-full_CurrentAssets",), ("유동자산",)),
    MetricDef("cash", "현금및현금성자산", _BS, ("ifrs-full_CashAndCashEquivalents",), ("현금및현금성자산",)),
    MetricDef("trade_receivables", "매출채권", _BS, ("ifrs-full_CurrentTradeReceivables", "dart_ShortTermTradeReceivable"),
              ("매출채권", "매출채권및기타채권")),
    MetricDef("inventories", "재고자산", _BS, ("ifrs-full_Inventories",), ("재고자산",)),
    MetricDef("ppe", "유형자산", _BS, ("ifrs-full_PropertyPlantAndEquipment",), ("유형자산",)),
    MetricDef("total_liabilities", "부채총계", _BS, ("ifrs-full_Liabilities",), ("부채총계",)),
    MetricDef("current_liabilities", "유동부채", _BS, ("ifrs-full_CurrentLiabilities",), ("유동부채",)),
    MetricDef("short_term_borrowings", "단기차입금", _BS, ("ifrs-full_ShortTermBorrowings",), ("단기차입금",)),
    MetricDef("total_equity", "자본총계", _BS, ("ifrs-full_Equity",), ("자본총계",)),
    MetricDef("equity_owners", "지배주주지분", _BS, ("ifrs-full_EquityAttributableToOwnersOfParent",),
              ("지배기업의소유주에게귀속되는자본", "지배기업소유주지분", "지배기업의소유주지분")),
    # 현금흐름
    MetricDef("cfo", "영업활동현금흐름", _CF, ("ifrs-full_CashFlowsFromUsedInOperatingActivities",),
              ("영업활동현금흐름", "영업활동으로인한현금흐름")),
    MetricDef("cfi", "투자활동현금흐름", _CF, ("ifrs-full_CashFlowsFromUsedInInvestingActivities",),
              ("투자활동현금흐름", "투자활동으로인한현금흐름")),
    MetricDef("cff", "재무활동현금흐름", _CF, ("ifrs-full_CashFlowsFromUsedInFinancingActivities",),
              ("재무활동현금흐름", "재무활동으로인한현금흐름")),
    MetricDef("capex", "유형자산취득", _CF, ("ifrs-full_PurchaseOfPropertyPlantAndEquipment",), ("유형자산의취득",)),
    MetricDef("dividends_paid", "배당금지급", _CF, ("ifrs-full_DividendsPaidClassifiedAsFinancingActivities",),
              ("배당금의지급", "배당금지급")),
)

METRICS_BY_NAME = {m.name: m for m in METRICS}

# 정기보고서 주요정보 API (report_items 테이블에 저장)
KEY_REPORT_APIS = {
    "alotMatter": "배당에 관한 사항",
    "stockTotqySttus": "주식의 총수 현황",
    "tesstkAcqsDspsSttus": "자기주식 취득 및 처분 현황",
    "irdsSttus": "증자(감자) 현황",
    "hyslrSttus": "최대주주 현황",
    "hyslrChgSttus": "최대주주 변동현황",
    "mrhlSttus": "소액주주 현황",
    "exctvSttus": "임원 현황",
    "empSttus": "직원 현황",
    "hmvAuditAllSttus": "이사·감사 전체 보수현황",
    "indvdlByPay": "개인별 보수지급 금액(5억 이상)",
    "otrCprInvstmntSttus": "타법인 출자현황",
    "accnutAdtorNmNdAdtOpinion": "회계감사인 및 감사의견",
    "cprndNrdmpBlce": "회사채 미상환 잔액",
}

# 주요 재무지표 분류 (fnlttSinglIndx)
INDEX_CLASSES = {
    "M210000": "수익성지표",
    "M220000": "안정성지표",
    "M230000": "성장성지표",
    "M240000": "활동성지표",
}

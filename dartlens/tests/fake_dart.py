"""테스트용 가짜 DART 서버 (requests.Session 대체)."""

import io
import json
import zipfile

CORP = {"corp_code": "00000001", "corp_name": "테스트전자", "stock_code": "999990"}
QUARTER = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}


def revenue(year, q):
    return 1000 + 100 * q + (year - 2023) * 50


def _row(sj, acc_id, nm, amt, add=None, ord_=1):
    return {"rcept_no": "20240101000001", "sj_div": sj, "sj_nm": sj, "account_id": acc_id, "account_nm": nm,
            "account_detail": "-", "ord": str(ord_), "currency": "KRW", "thstrm_nm": "당기",
            "thstrm_amount": "" if amt is None else f"{amt:,.0f}".replace(",", ""),
            "thstrm_add_amount": "" if add is None else str(int(add))}


def fs_rows(year, reprt_code, fs_div):
    q = QUARTER[reprt_code]
    scale = 1 if fs_div == "CFS" else 0.5
    rev = [revenue(year, i) * scale for i in (1, 2, 3, 4)]
    cum = sum(rev[:q])
    if q == 4:
        rev_amt, rev_add = sum(rev), None
    else:
        rev_amt, rev_add = rev[q - 1], cum
    op_amt = rev_amt * 0.1
    op_add = None if rev_add is None else rev_add * 0.1
    # 분기보고서의 순이익 계정명은 '분기순이익' / '반기순이익' (표준코드 미사용)
    ni_name = {1: "분기순이익", 2: "반기순이익", 3: "분기순이익", 4: "당기순이익"}[q]
    ni_amt = rev_amt * 0.08
    ni_add = None if rev_add is None else rev_add * 0.08
    return [
        _row("BS", "ifrs-full_Assets", "자산총계", 10000 * scale + q * 10, ord_=1),
        _row("BS", "ifrs-full_CurrentAssets", "유동자산", 4000 * scale, ord_=2),
        _row("BS", "ifrs-full_Liabilities", "부채총계", 4000 * scale, ord_=3),
        _row("BS", "ifrs-full_CurrentLiabilities", "유동부채", 2000 * scale, ord_=4),
        _row("BS", "ifrs-full_Equity", "자본총계", 6000 * scale + q * 10, ord_=5),
        _row("IS", "ifrs-full_Revenue", "매출액", rev_amt, rev_add, ord_=1),
        _row("IS", "dart_OperatingIncomeLoss", "영업이익(손실)", op_amt, op_add, ord_=2),
        _row("IS", "-표준계정코드 미사용-", ni_name, ni_amt, ni_add, ord_=3),
        _row("IS", "-표준계정코드 미사용-", "연구개발비", rev_amt * 0.05, None if rev_add is None else rev_add * 0.05, 4),
        _row("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", cum * 0.2, ord_=1),
        _row("CF", "ifrs-full_PurchaseOfPropertyPlantAndEquipment", "유형자산의 취득", cum * 0.05, ord_=2),
    ]


class FakeResponse:
    def __init__(self, payload=None, content=None, status_code=200):
        self._payload = payload
        self.content = content if content is not None else json.dumps(payload).encode()
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        endpoint = url.rsplit("/", 1)[-1]
        self.calls.append((endpoint, dict(params or {})))
        if endpoint == "corpCode.xml":
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("CORPCODE.xml",
                            "<result><list><corp_code>00000001</corp_code><corp_name>테스트전자</corp_name>"
                            "<corp_eng_name>Test Elec</corp_eng_name><stock_code>999990</stock_code>"
                            "<modify_date>20240101</modify_date></list>"
                            "<list><corp_code>00000002</corp_code><corp_name>테스트전자서비스</corp_name>"
                            "<corp_eng_name/><stock_code> </stock_code><modify_date>20240101</modify_date></list>"
                            "</result>")
            return FakeResponse(content=buf.getvalue())
        if endpoint == "company.json":
            return FakeResponse({"status": "000", **CORP, "stock_name": "테스트전자", "acc_mt": "12"})
        if endpoint == "fnlttSinglAcntAll.json":
            rows = fs_rows(int(params["bsns_year"]), params["reprt_code"], params["fs_div"])
            return FakeResponse({"status": "000", "list": rows})
        if endpoint == "alotMatter.json":
            return FakeResponse({"status": "000", "list": [
                {"rcept_no": "1", "se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "1,444"}]})
        if endpoint == "list.json":
            return FakeResponse({"status": "000", "total_page": 1, "list": [
                {"rcept_no": "20240315000001", "corp_code": "00000001", "corp_name": "테스트전자",
                 "report_nm": "사업보고서 (2023.12)", "flr_nm": "테스트전자", "rcept_dt": "20240315", "rm": ""}]})
        return FakeResponse({"status": "013", "message": "조회된 데이타가 없습니다."})

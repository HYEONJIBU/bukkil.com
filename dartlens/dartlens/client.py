"""DART OpenAPI 클라이언트 (https://opendart.fss.or.kr/guide/main.do).

- 요청 간 최소 간격(throttle)과 네트워크 오류 재시도를 처리한다.
- status "013"(조회된 데이터 없음)은 예외 대신 빈 결과로 돌려준다.
"""

from __future__ import annotations

import io
import time
import zipfile
import xml.etree.ElementTree as ET

import requests

BASE_URL = "https://opendart.fss.or.kr/api"

# 보고서 코드 -> (이름, 회계연도 내 분기)
REPORT_CODES = {
    "11013": ("1분기보고서", 1),
    "11012": ("반기보고서", 2),
    "11014": ("3분기보고서", 3),
    "11011": ("사업보고서", 4),
}
QUARTER_OF_REPORT = {code: q for code, (_, q) in REPORT_CODES.items()}

STATUS_OK = "000"
STATUS_NO_DATA = "013"
STATUS_RATE_LIMIT = "020"


class DartError(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message


class RateLimitError(DartError):
    """일일 요청 한도(약 20,000건) 초과."""


class DartClient:
    def __init__(
        self,
        api_key: str,
        min_interval: float = 0.2,
        timeout: float = 30,
        max_retries: int = 3,
        session: requests.Session | None = None,
    ):
        self.api_key = api_key
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self._last_call = 0.0

    # --- 저수준 ---------------------------------------------------------
    def _request(self, path: str, params: dict) -> requests.Response:
        params = {"crtfc_key": self.api_key, **{k: v for k, v in params.items() if v is not None}}
        url = f"{BASE_URL}/{path}"
        for attempt in range(self.max_retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                resp.raise_for_status()
                return resp
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
                if attempt == self.max_retries:
                    raise
                time.sleep(2 ** attempt)
        raise AssertionError("unreachable")

    def get_json(self, endpoint: str, **params) -> dict:
        """endpoint 예: 'fnlttSinglAcntAll'. 응답 dict를 반환(데이터 없음이면 list=[])."""
        data = self._request(f"{endpoint}.json", params).json()
        status = data.get("status", STATUS_OK)
        if status == STATUS_OK:
            return data
        if status == STATUS_NO_DATA:
            return {**data, "list": []}
        if status == STATUS_RATE_LIMIT:
            raise RateLimitError(status, data.get("message", ""))
        raise DartError(status, data.get("message", ""))

    def get_list(self, endpoint: str, **params) -> list[dict]:
        return self.get_json(endpoint, **params).get("list", []) or []

    # --- 고수준 ---------------------------------------------------------
    def corp_codes(self) -> list[dict]:
        """전체 고유번호 목록(corpCode.xml, zip)."""
        resp = self._request("corpCode.xml", {})
        if not resp.content.startswith(b"PK"):
            # 오류 시 zip 대신 XML/JSON 상태 메시지가 온다
            raise DartError("corpCode", resp.content[:300].decode("utf-8", "replace"))
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_bytes = zf.read(zf.namelist()[0])
        root = ET.fromstring(xml_bytes)
        return [
            {child.tag: (child.text or "").strip() for child in item}
            for item in root.iter("list")
        ]

    def company(self, corp_code: str) -> dict:
        return self.get_json("company", corp_code=corp_code)

    def financial_statements(self, corp_code: str, year: int, reprt_code: str, fs_div: str) -> list[dict]:
        """단일회사 전체 재무제표. fs_div: CFS(연결) / OFS(별도). 2015년 이후 제공."""
        return self.get_list(
            "fnlttSinglAcntAll", corp_code=corp_code, bsns_year=str(year),
            reprt_code=reprt_code, fs_div=fs_div,
        )

    def key_report(self, api: str, corp_code: str, year: int, reprt_code: str) -> list[dict]:
        """정기보고서 주요정보 (배당, 최대주주, 직원현황 등)."""
        return self.get_list(api, corp_code=corp_code, bsns_year=str(year), reprt_code=reprt_code)

    def financial_indices(self, corp_code: str, year: int, reprt_code: str, idx_cl_code: str) -> list[dict]:
        """단일회사 주요 재무지표 (2023년 3분기 이후 제공)."""
        return self.get_list(
            "fnlttSinglIndx", corp_code=corp_code, bsns_year=str(year),
            reprt_code=reprt_code, idx_cl_code=idx_cl_code,
        )

    def disclosures(self, corp_code: str, bgn_de: str, end_de: str, pblntf_ty: str | None = "A") -> list[dict]:
        """공시검색. pblntf_ty='A'는 정기공시. 모든 페이지를 순회한다."""
        out, page = [], 1
        while True:
            data = self.get_json(
                "list", corp_code=corp_code, bgn_de=bgn_de, end_de=end_de,
                pblntf_ty=pblntf_ty, page_no=page, page_count=100,
            )
            out.extend(data.get("list", []) or [])
            if page >= int(data.get("total_page", 1) or 1):
                return out
            page += 1

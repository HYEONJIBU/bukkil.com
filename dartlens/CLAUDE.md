# DARTLens — 분석 작업 가이드 (Claude용)

사용자가 기업 재무에 대해 질문하면 이 DB에서 데이터를 꺼내 답한다. 코드 위치: `dartlens/`.

## 질문에 답하는 순서
1. `python -m dartlens status` 로 DB에 어떤 회사/기간이 있는지 확인.
2. 없으면 `python -m dartlens ingest <회사명|종목코드>` 로 수집 (DART_API_KEY 필요, `.env`).
   - 이름이 모호하면 `python -m dartlens search <이름>` 결과를 사용자에게 보여주고 고른다.
3. 표준 지표는 `Lens.metrics / ttm / ratios / compare`, 그 외 계정은 `Lens.find_accounts` → `Lens.account`,
   배당·주주·직원·임원보수 등은 `Lens.items(company, api)`, 그 밖은 `Lens.sql`.
4. 답변에는 항상 **단위(억원 등), 연결/별도(fs_div), 기간 기준(연간/분기 3개월/TTM)** 을 명시한다.

## 주의
- 기본은 연결(CFS). 연결재무제표가 없는 회사는 자동으로 별도(OFS).
- 분기 손익은 3개월 값, Q4는 연간-3분기누적으로 계산된 값이다. 현금흐름 분기값도 누적 차감으로 만든 값.
- 비율을 비교할 땐 같은 fs_div, 같은 기간 기준끼리만 비교.
- `metrics`에 값이 비어 있으면 계정명이 표준과 다른 것이다 → `find_accounts`로 찾아 `account`로 조회하고,
  반복되는 패턴이면 `dartlens/accounts.py`의 `METRICS` 후보 이름에 추가한 뒤 `python -m dartlens rebuild`.
- 원 데이터 확인이 필요하면 `fs_raw` (보고서별 원본 행), 공시 원문은 `disclosures.rcept_no`로 링크.

## 개발
- 테스트: `pytest -q` (tests/fake_dart.py 가 가짜 DART 서버)
- 스키마: `dartlens/db.py`, 분기 계산: `dartlens/transform.py`

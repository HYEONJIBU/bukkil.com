# FinVault

DART OpenAPI로 기업의 사업보고서·분기보고서를 불러와 **연간(A)·분기(Q) 재무제표(I/S, B/S, C/F)와
정기보고서 주요정보(배당, 최대주주, 직원, 임원보수 등)를 SQLite DB로 쌓고**, 질문에 맞게 꺼내 분석하는 도구입니다.

## 프로젝트 범위

**수집 대상**
- 재무제표 전체(BS·IS·CIS·CF·SCE)를 2015년~올해, 연결(CFS)과 별도(OFS) 모두
- 정기보고서 주요정보 14종(배당, 최대주주, 직원, 임원보수, 자기주식, 감사의견 등)
- DART가 제공하는 주요 재무지표
- 정기공시 목록

**분기 값 계산**
- I/S: 분기보고서의 3개월 금액 사용, Q4 = 연간 − 3분기 누적
- C/F: 보고서 금액이 연초부터 누적이므로 직전 분기와의 차이로 분기 값 계산
- B/S: 분기말 잔액 그대로

**표준 지표 26개**(매출액, 영업이익, 자산총계, 영업활동현금흐름 등): 회사마다 다른 계정명
("영업이익(손실)", "반기순이익" 등)을 정리해 맞춘다. 표준 지표에 없는 계정은 이름으로 검색해 조회한다.

**분석 기능**: 연간/분기/최근 4분기 합(TTM) 조회, 재무비율(마진, ROE, 부채비율, 전년·전분기 대비 증가율),
여러 회사 비교, SQL 직접 조회

**`CLAUDE.md`**: "삼성전자 최근 8분기 영업이익률 추이는?" 같은 질문에 Claude가 이 DB를 어떻게 조회해 답할지 적어둔 안내서

## 1. 설치

```bash
cd finvault
pip install -e ".[dev]"          # requests, pandas, openpyxl, pytest
cp .env.example .env             # DART_API_KEY=발급받은키 입력
```

인증키 발급: https://opendart.fss.or.kr → 인증키 신청 (하루 약 20,000건 호출 가능)

## 2. 수집

```bash
python -m finvault search 삼성                       # 회사 검색 (최초 실행 시 고유번호 목록 자동 다운로드)
python -m finvault ingest 삼성전자                   # 2015년~올해, 연결(CFS)+별도(OFS), 주요정보 포함
python -m finvault ingest 005930 000660 --from 2018  # 종목코드로 여러 회사
python -m finvault ingest 삼성전자 --refresh         # 이미 받은 기간도 다시 받기
```

회사 1곳을 2015~2026년 전체 수집하면 대략 300~400회 호출하며, 호출당 수 초가 걸려 **30분~1시간** 정도 소요됩니다. 최초 1회는 회사 고유번호 목록(약 12만 개) 다운로드에 수 분이 더 걸립니다. 이미 받은 기간은 `fetch_log`에 기록돼 다시 호출하지 않습니다.

## 3. 엑셀로 보기

`finvault.db`는 SQLite 파일이라 엑셀에서 바로 열리지 않습니다. 엑셀 파일로 내보내세요.

```bash
python -m finvault export 삼성전자                  # data/삼성전자(주)_FinVault.xlsx
python -m finvault export 삼성전자 --unit 백만 -o 삼성.xlsx
python -m finvault ingest 삼성전자 --excel          # 수집 후 바로 엑셀까지
```

시트 구성: 안내 / 요약(표준 지표)·비율 (연간·분기) / 재무상태표·손익계산서·포괄손익계산서·현금흐름표 (연간·분기) /
정기보고서 주요정보(배당, 최대주주, 직원 등) / DART재무지표 / 정기공시목록(원문 링크)

## 4. 조회 · 분석

```bash
python -m finvault list-metrics                                   # 표준 지표 목록
python -m finvault show 삼성전자 -f A                              # 연간 전체 지표 (억원)
python -m finvault show 삼성전자 -f Q -m revenue,operating_income --last 8
python -m finvault show 삼성전자 -f TTM -m revenue,net_income      # 최근 4분기 합
python -m finvault ratios 삼성전자 -f Q --last 8                   # 마진, ROE, 부채비율, YoY...
python -m finvault account 삼성전자 연구개발 -f A                   # 표준 지표에 없는 계정
python -m finvault items 삼성전자 alotMatter                        # 배당
python -m finvault sql "SELECT * FROM v_metrics WHERE metric='revenue' AND period_type='A'"
```

Python:

```python
from finvault.analysis import Vault
vault = Vault()
vault.metrics("삼성전자", ["revenue", "operating_income"], freq="Q", last=8, unit="억")
vault.ratios("삼성전자", freq="A")
vault.compare(["삼성전자", "SK하이닉스"], "operating_income", freq="Q", last=12)
```

## 5. DB 구조 (`data/finvault.db`)

| 테이블 | 내용 |
|---|---|
| `corp_codes` | DART 전체 회사 고유번호 (검색용) |
| `companies` | 수집한 회사 개황 (결산월 `acc_mt` 등) |
| `fs_raw` | 재무제표 원본 행 (API 응답 그대로, BS/IS/CIS/CF/SCE) |
| `fs_values` | 모든 계정의 기간별 값: `period`=`2024A` / `2024Q3` |
| `metrics` | 표준 지표 (매출액·영업이익·자산총계 등 26개) — 뷰 `v_metrics`에 회사명 포함 |
| `report_items` | 정기보고서 주요정보, 행 단위 JSON (`json_extract(data,'$.thstrm')`) |
| `fin_indices` | DART 제공 주요 재무지표 (2023년 이후) |
| `disclosures` | 정기공시 목록 (접수번호 → 원문 링크 `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=...`) |
| `fetch_log` | 수집 이력 |

### 분기 값 계산 규칙
- **B/S**: 분기말 잔액 그대로 (`basis=PIT`)
- **I/S**: 분기보고서의 3개월 금액 사용, **Q4 = 연간 − 3분기 누적** (`basis=3M`)
- **C/F**: 보고서 금액이 연초부터 누적이므로 **직전 분기 누적과의 차이**로 계산
- 주당이익(EPS)은 Q4를 차감으로 만들지 않음 (주식수 변동 때문에 부정확)
- 연간(A) 손익·현금흐름은 사업보고서 금액 (`basis=CUM`)
- `fiscal_year`/`fiscal_q`는 DART의 사업연도 기준입니다 (12월 결산이 아닌 회사는 달력 분기와 다름)

## 6. 테스트

```bash
pytest -q        # 가짜 DART 서버로 수집→변환→분석 전 과정을 검증
```

## 한계 / 다음 단계 후보
- 사업부문별 매출, 주석 세부 내역은 재무제표 API에 없음 → 공시 원문(`document.xml`) 또는 XBRL 파싱 필요
- 2015년 이전 재무제표는 `fnlttSinglAcntAll`에서 제공되지 않음
- 금융업(은행·보험·증권)은 계정 체계가 달라 일부 표준 지표(매출액 등)가 비어 있을 수 있음 → `account` 명령으로 직접 조회

# CANSLIM-KR

윌리엄 오닐 CANSLIM 기법의 **한국 주식 전용** 분석 엔진.
미국 시장 기준을 그대로 옮기면 오작동하는 지점들을 보정했습니다.

## 설치

```bash
pip install pykrx finance-datareader requests pandas numpy
```

API 키 (둘 다 무료):
- DART: https://opendart.fss.or.kr — 재무제표·공시. C/A/S 항목에 필수
- KRX (선택): https://openapi.krx.co.kr — 공식 API. pykrx 스크래핑 대체용

```bash
export DART_API_KEY="발급받은키"
```

## 사용

```python
from canslim_kr import CanslimKR, DEFAULT, strict_preset

kr = CanslimKR(DEFAULT)

# 1) 오늘 시장이 살 만한 국면인가 (M)
kr.market_gate()

# 2) 종목 하나 정밀 분석
rep = kr.analyze("005930", account_size=50_000_000)
print(kr.to_markdown(rep))

# 3) 전 종목 스크리닝 → 앱용 JSON
res = kr.screen(top_n=30, deep_n=40, account_size=50_000_000)
kr.save_json(res, "canslim_result.json")
```

`canslim_result.json`을 모바일 앱(`canslim-kr-app.jsx`)의 "JSON 불러오기"에 붙여넣으면 그대로 렌더링됩니다.

## 파이프라인

```
M 시장판정 → 유니버스 필터(2,600→500) → RS Rating(→200) 
→ 신고가 필터(→50) → C/A/S/I 정밀분석 → 매매계획
```

가장 비싼 DART 재무 조회를 마지막에 두는 순서가 핵심입니다.
그 시점엔 대상이 수십 개로 줄어 API 호출이 실용적인 수준이 됩니다.

## 한국 시장 보정 내역

| 항목 | 오닐 원본 | CANSLIM-KR | 이유 |
|---|---|---|---|
| C 분기 EPS | +25% | +20% 통과 / +40% 만점 | 한국 성장률 분포가 낮음. 흑자전환은 별도 점수 |
| A ROE | 17% | 12% 통과 / 17% 만점 | 한국 상장사 평균 ROE가 구조적으로 낮음 |
| N 주가 | $15 이상 | 2,000원 이상 | 액면가 체계 차이. 동전주 배제 목적 |
| S 물량 | 유통주식수 | + 유상증자·CB/BW 감점, 자사주 가점 | 한국 특유의 희석 리스크 |
| I 기관 | 13F (분기 지연) | 일별 기관·외국인 순매수 | **한국이 미국보다 데이터가 우수한 유일한 항목** |
| M FTD | +1.2~1.7% | KOSPI +1.0% / KOSDAQ +1.4% | 지수별 변동성 차이 |
| 베이스 깊이 | 12~33% | 12~40% | 한국 변동성이 더 큼 |

## 정확도상 중요한 처리

1. **누적 재무제표 차분** — DART 분기 손익계산서는 누적입니다.
   3분기보고서는 1~9월 누적이므로 단일 3분기 = 3Q누적 − 반기누적.
   이 처리를 안 하면 C 항목이 통째로 틀립니다. (`quarterly_from_cumulative`)

2. **연결 우선, 별도 폴백** — 지주사·자회사 구조가 많아 CFS를 우선합니다.

3. **RS는 백분위** — 절대 수익률이 아니라 유니버스 전체 대비 순위입니다.
   유니버스를 바꾸면 RS도 바뀝니다.

4. **손잡이 탐지** — 저점 이후 단순 최고가를 쓰면 돌파봉 자체가 잡혀
   손잡이를 놓칩니다. 스윙 고점 중 뒤에 5일 이상 남은 것을 씁니다.

5. **공시 시차** — 한국 분기보고서는 최대 45일 지연. 135일 초과 시
   "스테일" 표시 후 감점합니다.

## 조정

`config.py` 한 곳에만 임계값이 있습니다.

```python
from canslim_kr import strict_preset, loose_preset
kr = CanslimKR(strict_preset())   # 오닐 원본에 가깝게. 통과 종목 급감
kr = CanslimKR(loose_preset())    # 후보를 넓게. 초기 탐색용
```

## 검증

```bash
python -m canslim_kr.selftest
```

네트워크 없이 합성 데이터로 베이스 탐지·RS·분산일·FTD·차분·채점을 검증합니다.

## 한계

- pykrx는 KRX/네이버 스크래핑입니다. 과도한 호출을 자제하고 캐시를 쓰세요.
- 자동 베이스 탐지는 후보를 좁히는 용도입니다. **최종 판단은 주봉 차트를 직접 확인하세요.**
- 업종 분류는 FinanceDataReader 기준이라 WICS/GICS와 다를 수 있습니다.
- 백테스트 모듈은 아직 없습니다. 임계값은 오닐 원본에서 유도한 값이지
  한국 데이터로 최적화한 값이 아닙니다.

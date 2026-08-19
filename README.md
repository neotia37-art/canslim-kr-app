# CANSLIM 한국

윌리엄 오닐 CANSLIM 기법의 **한국 주식 전용** 분석 앱.
종목코드를 넣으면 실시간으로 데이터를 받아 7항목 전부를 분석합니다.

---

## 1. GitHub 올리기

터미널에서 이 폴더로 이동한 뒤:

```bash
git init
git add .
git commit -m "CANSLIM 한국 앱 초기 커밋"
git branch -M main
git remote add origin https://github.com/<본인아이디>/canslim-kr.git
git push -u origin main
```

`.gitignore`가 `.streamlit/secrets.toml`을 막고 있어 **DART 키는 올라가지 않습니다.**
푸시 전에 확인하려면:

```bash
git status --short          # secrets.toml이 목록에 없어야 정상
git check-ignore -v .streamlit/secrets.toml   # 무시 규칙이 잡히는지 확인
```

## 2. Streamlit Cloud 배포

1. <https://share.streamlit.io> 접속 → GitHub 계정으로 로그인
2. **Create app** → 방금 올린 저장소 선택
3. Main file path: `app.py`
4. **Advanced settings → Secrets** 에 아래를 붙여넣기:

```toml
DART_API_KEY = "본인_DART_키"
```

5. Deploy. 3~5분 뒤 `https://<앱이름>.streamlit.app` 주소가 나옵니다.

### 폰에서 앱처럼 쓰기

배포된 주소를 폰 브라우저로 열고
- **iOS 사파리**: 공유 → 홈 화면에 추가
- **안드로이드 크롬**: 메뉴 → 홈 화면에 추가

주소창 없는 전체화면으로 실행됩니다. 앱스토어 설치와 같은 사용감입니다.

### 코드 수정 후 재배포

```bash
git add . && git commit -m "수정 내용" && git push
```

푸시하면 Streamlit Cloud가 자동으로 재배포합니다. 별도 조작이 필요 없습니다.

---

## 3. 로컬 실행

```bash
./run_local.sh
```

또는 직접:

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # 키 입력
streamlit run app.py
```

---

## 4. 화면 구성

| 탭 | 내용 |
|---|---|
| **시장** | 코스피·코스닥 분산일/후속일(FTD) 판정. 어느 날이 왜 분산일인지 등락률·거래량 근거를 표로 제시 |
| **종목 분석** | 코드/이름 검색 → 7항목 점수, 베이스 차트(피벗·매수구간·손절선), 매매 계획 |
| **관심목록** | 누적 관리, 추가·삭제·메모, 일괄 재분석, JSON/CSV 내보내기 |
| **스크리닝** | 전 종목 → RS → 신고가 → 재무·수급 순으로 후보 발굴 |

---

## 5. 왜 HTML 단일 파일이 아니라 Streamlit인가

브라우저 단독으로는 한국 주식 데이터를 가져올 수 없습니다.

- **KRX**: 공식 웹 API가 CORS 헤더를 주지 않아 브라우저 fetch가 차단됩니다
- **DART**: 마찬가지로 브라우저 직접 호출이 막힙니다
- **pykrx**: 파이썬 스크래핑 라이브러리라 브라우저에서 실행 불가

즉 단일 HTML은 "데이터를 손으로 붙여넣는 뷰어"밖에 될 수 없습니다.
Streamlit은 서버에서 파이썬을 돌리고 화면만 폰에 보내므로,
**종목코드를 넣으면 그 자리에서 실시간 분석**이 됩니다.
홈 화면에 추가하면 사용감은 네이티브 앱과 같습니다.

---

## 6. 관심목록 저장에 대해

Streamlit Cloud는 앱이 재시작되면 서버 파일이 초기화됩니다. 3중으로 둡니다.

1. 세션 상태 — 조작 즉시 반영
2. `data/watchlist.json` — 컨테이너가 살아 있는 동안 유지
3. **내보내기/불러오기** — 영구 보관은 이걸 쓰세요

종목을 정리한 뒤 JSON으로 한 번 내려받아 두시는 걸 권합니다.

---

## 7. 백테스트 (임계값 실증 최적화)

앱의 판정 임계값은 오닐 원본에서 유도한 값이지 한국 데이터로 검증한 값이 아닙니다.
`canslim_kr.backtest`로 2015~2026 실제 데이터에 대해 최적화할 수 있습니다.

```python
from canslim_kr import KRDataHub, DEFAULT
from canslim_kr.backtest import (PanelStore, SignalPanel, Backtester,
                                 Optimizer, report, DEFAULT_GRID, sanity_checks)

hub = KRDataHub(dart_key="...")
store = PanelStore(hub)

# 1) 패널 구축 (최초 1회, 수 시간~수 일 / 중단 후 재개 가능)
store.build_all(start="2014-01-01", end="2026-08-18", max_codes=600)

# 2) 신호 계산
sig = SignalPanel(store, DEFAULT); sig.compute()

# 3) 단일 백테스트
bt = Backtester(store, sig, DEFAULT, initial_capital=100_000_000)
res = bt.run("2015-01-01", "2026-08-18")
print(report(res, benchmark=store.index("KOSPI")["종가"]))

# 4) 워크포워드 최적화 — 과최적화 방지
opt = Optimizer(bt, DEFAULT)
wf = opt.walk_forward("2015-01-01", "2026-08-18", DEFAULT_GRID,
                      train_years=3, test_years=1,
                      benchmark=store.index("KOSPI")["종가"])
print(wf["요약"], wf["권장파라미터"])
for m in sanity_checks(wf): print("·", m)

# 5) 결과를 앱 설정에 반영
tuned = Optimizer.apply(DEFAULT, wf["권장파라미터"])
```

`wf["요약"]["검증_평균CAGR"]`가 실제로 기대할 수 있는 수치입니다.
학습구간 성과는 참고용이며, 둘의 격차가 과최적화 정도를 보여줍니다.

검증:
```bash
python -m canslim_kr.selftest            # 엔진
python -m canslim_kr.backtest.selftest   # 백테스트
```

---

## 8. 한국 시장 보정 내역

| 항목 | 오닐 원본 | 이 앱 | 이유 |
|---|---|---|---|
| C 분기 EPS | +25% | +20% 통과 / +40% 만점 | 한국 성장률 분포가 낮음. 흑자전환은 별도 점수 |
| A ROE | 17% | 12% 통과 / 17% 만점 | 한국 상장사 ROE가 구조적으로 낮음 |
| N 주가 | $15 이상 | 2,000원 이상 | 액면가 체계 차이 |
| S 물량 | 유통주식수 | + 유상증자 −25 / CB·BW −15 / 자사주 소각 +18 | 한국 특유의 희석 리스크 |
| I 기관 | 13F (분기 지연) | 일별 기관·외국인 순매수 | **한국이 미국보다 데이터가 우수한 유일한 항목** |
| M FTD | +1.2~1.7% | 코스피 +1.0% / 코스닥 +1.4% | 지수별 변동성 차이 |
| 베이스 깊이 | 12~33% | 12~40% | 한국 변동성이 더 큼 |
| 거래세 | — | 시점별 스케줄 (2026년 0.20%) | 백테스트 수익률에 직접 반영 |

### 정확도상 중요한 처리

1. **누적 재무제표 차분** — DART 분기 손익계산서는 누적입니다.
   3분기보고서는 1~9월 누적이므로 단일 3분기 = 3Q누적 − 반기누적.
   이 처리를 안 하면 C 항목이 통째로 틀립니다.
2. **시점정합(PIT)** — 백테스트에서 2024년 1분기 실적은 2024-05-15부터만
   사용합니다. 공시 지연을 무시하면 미래를 아는 백테스트가 됩니다.
3. **생존 편향 차단** — 과거 시점의 실제 상장 종목으로 유니버스를 구성해
   상장폐지 종목을 포함시킵니다.
4. **D+1 시가 체결** — 종가 신호를 종가에 체결시키면 그 자체가 미래 참조입니다.

---

## 9. 한계

- 베이스 자동 탐지는 후보를 좁히는 용도입니다. **최종 판단은 주봉 차트를 직접 확인하세요.**
- pykrx는 KRX 스크래핑이라 과도한 호출은 자제해야 합니다 (앱이 캐시로 처리).
- 분기 실적은 공시까지 최대 45일 지연됩니다.
- **이 앱은 정보 제공용이며 투자 권유가 아닙니다.** 투자 판단과 결과는 이용자 본인에게 귀속됩니다.

## 10. 데이터 출처

- 시세·수급·공매도: [pykrx](https://github.com/sharebook-kr/pykrx) (KRX)
- 재무·공시: [OpenDART](https://opendart.fss.or.kr)
- 종목 마스터: [FinanceDataReader](https://github.com/FinanceData/FinanceDataReader)

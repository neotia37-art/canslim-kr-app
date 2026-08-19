"""
CANSLIM 한국 — 윌리엄 오닐 기법 한국 주식 분석 앱

실행
    streamlit run app.py

배포 (Streamlit Community Cloud)
    README.md 참고. GitHub 저장소 연결 후 Secrets에 DART_API_KEY 등록.
"""

import os
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="CANSLIM 한국",
    page_icon="📈",
    layout="centered",
    initial_sidebar_state="collapsed",
    menu_items={"about": "윌리엄 오닐 CANSLIM 기법의 한국 주식 적용. 투자 권유가 아닙니다."},
)

from ui.style import inject, card, kv_rows, pill, UP, DOWN, PIVOT, DIM, FAINT, TEXT
from ui import market_tab, stock_tab, watchlist, screener_tab, diag_tab

inject()


# ─────────────────────────────────────────────────────────────
# DART 키: Secrets → 환경변수 → 사이드바 입력 순으로 찾습니다
# ─────────────────────────────────────────────────────────────
def resolve_dart_key() -> str:
    if "dart_key_manual" in st.session_state and st.session_state.dart_key_manual:
        return st.session_state.dart_key_manual
    try:
        if "DART_API_KEY" in st.secrets:
            return st.secrets["DART_API_KEY"]
    except Exception:
        pass
    return os.environ.get("DART_API_KEY", "")


@st.cache_resource(show_spinner=False)
def get_engine(dart_key: str, preset: str):
    from canslim_kr import KRDataHub, CanslimKR, DEFAULT, strict_preset, loose_preset
    cfg = {"기본": DEFAULT, "엄격 (오닐 원본)": strict_preset(),
           "완화 (탐색용)": loose_preset()}.get(preset, DEFAULT)
    hub = KRDataHub(dart_key=dart_key, verbose=False)
    return hub, CanslimKR(cfg, hub=hub, verbose=False), cfg


# ─────────────────────────────────────────────────────────────
# 사이드바 — 설정
# ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 설정")

    preset = st.selectbox("판정 기준", ["기본", "엄격 (오닐 원본)", "완화 (탐색용)"],
                          help="엄격은 오닐 원본 임계값에 가깝고 통과 종목이 크게 줄어듭니다.")

    account = st.number_input("투자 원금 (원)", min_value=0, step=1_000_000,
                              value=int(st.session_state.get("account_size", 0)),
                              help="입력하면 종목별 권장 수량과 최대손실을 계산합니다.")
    st.session_state["account_size"] = account

    st.divider()
    key_found = resolve_dart_key()
    if key_found:
        st.success(f"DART 키 연결됨 ({key_found[:6]}…)")
    else:
        st.warning("DART 키 없음 — C·A 항목(재무)이 중립 처리됩니다")
    manual = st.text_input("DART API 키 직접 입력", type="password",
                           value="", key="dart_key_manual",
                           help="opendart.fss.or.kr 무료 발급. "
                                "Secrets에 넣어두면 여기 입력이 필요 없습니다.")

    st.divider()
    if st.button("데이터 캐시 비우기"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.success("비웠습니다")

    st.caption("이 앱은 정보 제공용입니다. 투자 판단과 그 결과는 "
               "이용자 본인에게 귀속됩니다.")

dart_key = resolve_dart_key()
st.session_state["dart_key"] = dart_key

try:
    hub, kr, cfg = get_engine(dart_key, preset)
except Exception as e:
    st.error(f"엔진 초기화 실패: {e}")
    st.stop()


# ─────────────────────────────────────────────────────────────
# 헤더
# ─────────────────────────────────────────────────────────────
st.markdown(
    f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
    f'margin-bottom:10px">'
    f'<div style="font-size:19px;font-weight:800;letter-spacing:-.02em">CANSLIM 한국</div>'
    f'<div style="font-size:11px;color:{FAINT}">오닐 기법 · {preset}</div>'
    f'</div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# 탭
# ─────────────────────────────────────────────────────────────
tab_m, tab_s, tab_w, tab_scan, tab_diag, tab_help = st.tabs(
    ["시장", "종목 분석", "관심목록", "스크리닝", "진단", "도움말"])

if "market_states" not in st.session_state:
    st.session_state["market_states"] = {}

with tab_m:
    try:
        ms = market_tab.render(hub, cfg)
        if ms:
            st.session_state["market_states"] = ms
    except Exception as e:
        st.error(f"시장 분석 실패: {e}")
        st.info("**진단** 탭에서 어느 데이터 경로가 막혔는지 확인할 수 있습니다. "
                "KRX 지수 조회는 서버 위치에 따라 일시적으로 차단되기도 하며, "
                "이 경우 앱이 FinanceDataReader로 자동 전환합니다.")

states = st.session_state.get("market_states", {})

with tab_s:
    try:
        stock_tab.render(hub, kr, cfg, states)
    except Exception as e:
        st.error(f"종목 분석 실패: {e}")

with tab_w:
    try:
        watchlist.render(hub, kr, cfg, states)
    except Exception as e:
        st.error(f"관심목록 오류: {e}")

with tab_scan:
    try:
        screener_tab.render(hub, kr, cfg, states)
    except Exception as e:
        st.error(f"스크리닝 실패: {e}")

with tab_diag:
    try:
        diag_tab.render(hub, kr, cfg)
    except Exception as e:
        st.error(f"진단 실패: {e}")

with tab_help:
    st.markdown("""
### 이 앱이 하는 일

윌리엄 오닐의 CANSLIM 기법을 한국 주식에 적용합니다.
미국 기준을 그대로 옮기면 오작동하는 지점들을 보정했습니다.

| 항목 | 무엇을 보는가 |
|---|---|
| **C** | 최근 분기 이익·매출이 전년 동기 대비 얼마나 늘었나 |
| **A** | 3년 연평균 이익 성장률, ROE — 구조적으로 돈 버는 회사인가 |
| **N** | 52주 신고가 근처인가, 매수 기준가(피벗)가 정의되는가 |
| **S** | 거래량 매집 강도, 유상증자·CB 같은 물량 희석이 있는가 |
| **L** | RS Rating — 시장 전체 대비 상대강도 순위 (80 미만 제외) |
| **I** | 기관·외국인의 실제 순매수 |
| **M** | 시장 방향 — 조정이면 나머지가 좋아도 사지 않습니다 |

### 순서

1. **시장** 탭에서 오늘 살 만한 국면인지 먼저 확인합니다
2. 국면이 괜찮으면 **종목 분석**에서 개별 종목을 봅니다
3. 볼 만한 종목은 **관심목록**에 넣고 돌파를 기다립니다
4. **스크리닝**은 전 종목에서 후보를 찾아냅니다 (시간이 걸립니다)

### 한국 시장 보정

- **분기 실적**: DART 손익계산서는 누적이라 차분해야 단일 분기가 나옵니다.
  이 처리를 안 하면 C 항목이 통째로 틀립니다
- **ROE**: 오닐 17% → 통과선 12%. 한국 상장사 ROE가 구조적으로 낮습니다
- **수급(I)**: 미국은 13F 분기 지연 추정이지만 한국은 일 단위 공개라
  이 항목만은 미국보다 정밀합니다
- **물량(S)**: 유상증자 −25점, CB/BW −15점, 자사주 소각 +18점.
  한국 특유의 리스크라 미국판에는 없는 항목입니다
- **FTD**: 코스피 +1.0% / 코스닥 +1.4%로 분리 적용 (변동성 차이)

### 한계

- 베이스 자동 탐지는 후보를 좁히는 용도입니다.
  **최종 판단은 주봉 차트를 직접 확인하세요.**
- 임계값은 오닐 원본에서 유도한 값입니다. 백테스트 모듈
  (`canslim_kr.backtest`)로 한국 데이터 실증 최적화를 할 수 있습니다
- 분기 실적은 공시까지 최대 45일 지연됩니다
- 이 앱은 정보 제공용이며 투자 권유가 아닙니다

### 데이터 출처

- 시세·수급·공매도: [pykrx](https://github.com/sharebook-kr/pykrx) (KRX)
- 재무·공시: [OpenDART](https://opendart.fss.or.kr)
- 종목 마스터: [FinanceDataReader](https://github.com/FinanceData/FinanceDataReader)
""")

st.markdown(
    f'<div style="text-align:center;font-size:10px;color:{FAINT};margin-top:24px">'
    f'CANSLIM-KR · 정보 제공용이며 투자 권유가 아닙니다</div>',
    unsafe_allow_html=True)

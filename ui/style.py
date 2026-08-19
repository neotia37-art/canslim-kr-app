"""
ui/style.py — 모바일 우선 스타일

Streamlit 기본 UI는 데스크톱 대시보드 느낌이라 폰에서 어색합니다.
여백을 줄이고, 탭을 가로 스크롤로 만들고, 터치 타깃을 키우고,
숫자에 tabular-nums를 적용해 자릿수가 흔들리지 않게 합니다.

색 규칙은 한국 시장 관행을 따릅니다 — 상승 빨강 / 하락 파랑.
(미국 차트 라이브러리 기본값과 반대이므로 차트마다 명시적으로 지정합니다.)
"""

import streamlit as st

# 한국 시장 관행 색상
UP = "#E5484D"        # 상승 · 강세
DOWN = "#4C86F0"      # 하락 · 약세
PIVOT = "#F2C14E"     # 매수 기준가
OK = "#3FB68B"
INK = "#0E0F16"
SURFACE = "#171923"
RAISED = "#1F2231"
LINE = "#2C3042"
TEXT = "#EDEBE8"
DIM = "#8A90A2"
FAINT = "#5A6076"

CSS = f"""
<style>
/* ── 모바일 여백 ── */
.block-container {{
    padding: 0.6rem 0.8rem 4rem 0.8rem !important;
    max-width: 780px;
}}
#MainMenu, footer, header {{ visibility: hidden; }}
[data-testid="stToolbar"] {{ display: none; }}
[data-testid="stDecoration"] {{ display: none; }}

/* ── 전역 ── */
html, body, [class*="css"] {{
    font-family: 'Pretendard','Apple SD Gothic Neo','Malgun Gothic',
                 -apple-system, BlinkMacSystemFont, sans-serif;
    -webkit-font-smoothing: antialiased;
}}
[data-testid="stAppViewContainer"] {{ background: {INK}; }}
[data-testid="stSidebar"] {{ background: {SURFACE}; }}

/* ── 탭: 가로 스크롤 + 터치 타깃 ── */
.stTabs [data-baseweb="tab-list"] {{
    gap: 2px; overflow-x: auto; scrollbar-width: none;
    background: {SURFACE}; border-radius: 10px; padding: 4px;
    position: sticky; top: 0; z-index: 99;
}}
.stTabs [data-baseweb="tab-list"]::-webkit-scrollbar {{ display: none; }}
.stTabs [data-baseweb="tab"] {{
    height: 40px; padding: 0 14px; border-radius: 7px;
    background: transparent; color: {DIM}; font-size: 13px; font-weight: 600;
    white-space: nowrap; flex-shrink: 0;
}}
.stTabs [aria-selected="true"] {{ background: {RAISED} !important; color: {TEXT} !important; }}
.stTabs [data-baseweb="tab-highlight"] {{ display: none; }}
.stTabs [data-baseweb="tab-border"] {{ display: none; }}

/* ── 입력 ── */
.stTextInput input, .stNumberInput input, .stSelectbox div[data-baseweb="select"] > div {{
    background: {RAISED} !important; color: {TEXT} !important;
    border: 1px solid {LINE} !important; border-radius: 8px !important;
    font-size: 16px !important;   /* iOS 자동 확대 방지: 16px 미만이면 확대됨 */
    min-height: 44px;
}}
.stButton > button {{
    width: 100%; min-height: 44px; border-radius: 9px;
    background: {RAISED}; color: {TEXT}; border: 1px solid {LINE};
    font-size: 14px; font-weight: 600;
}}
.stButton > button:hover {{ border-color: {DIM}; color: #fff; }}
.stButton > button[kind="primary"] {{
    background: {UP}; border-color: {UP}; color: #fff;
}}
.stDownloadButton > button {{ width: 100%; min-height: 44px; border-radius: 9px; }}

/* ── 지표 ── */
[data-testid="stMetric"] {{
    background: {SURFACE}; border: 1px solid {LINE};
    border-radius: 10px; padding: 10px 12px;
}}
[data-testid="stMetricValue"] {{
    font-size: 20px !important; font-variant-numeric: tabular-nums;
}}
[data-testid="stMetricLabel"] {{ font-size: 11px !important; color: {FAINT} !important; }}

/* ── 표 ── */
[data-testid="stDataFrame"] {{ border-radius: 10px; overflow: hidden; }}
[data-testid="stTable"] td, [data-testid="stTable"] th {{ font-size: 12px; }}

/* ── 확장 패널 ── */
.streamlit-expanderHeader, [data-testid="stExpander"] summary {{
    background: {SURFACE} !important; border-radius: 9px !important;
    font-size: 13px !important; font-weight: 600;
}}
[data-testid="stExpander"] {{ border: 1px solid {LINE}; border-radius: 9px; }}

/* ── 커스텀 컴포넌트 ── */
.ck-card {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 11px;
    padding: 14px; margin-bottom: 10px;
}}
.ck-label {{
    font-size: 10px; letter-spacing: .14em; color: {FAINT};
    text-transform: uppercase; margin-bottom: 6px;
}}
.ck-big {{ font-size: 26px; font-weight: 800; letter-spacing: -.02em; line-height: 1.15; }}
.ck-sub {{ font-size: 13px; color: {DIM}; line-height: 1.55; margin-top: 4px; }}
.ck-num {{ font-variant-numeric: tabular-nums; }}
.ck-row {{ display: flex; justify-content: space-between; align-items: baseline;
           padding: 7px 0; border-bottom: 1px solid {LINE}; font-size: 13px; }}
.ck-row:last-child {{ border-bottom: none; }}
.ck-k {{ color: {FAINT}; }}
.ck-v {{ color: {TEXT}; font-variant-numeric: tabular-nums; font-weight: 600; }}
.ck-pill {{
    display: inline-block; padding: 3px 9px; border-radius: 20px;
    font-size: 11px; font-weight: 700; margin-right: 5px; margin-bottom: 4px;
}}
.ck-bar-wrap {{ height: 5px; background: {LINE}; border-radius: 3px; overflow: hidden; margin-top: 5px; }}
.ck-bar {{ height: 100%; border-radius: 3px; }}
.ck-note {{ font-size: 12px; color: {DIM}; line-height: 1.6; margin-top: 5px; }}
.ck-dot-row {{ display: flex; gap: 3px; flex-wrap: wrap; margin-top: 6px; }}
.ck-dot {{ width: 7px; height: 7px; border-radius: 2px; }}

/* ── 알림 ── */
.stAlert {{ border-radius: 9px; font-size: 13px; }}

/* ── 폰 세로 ── */
@media (max-width: 640px) {{
    .block-container {{ padding: 0.4rem 0.6rem 4rem 0.6rem !important; }}
    .ck-big {{ font-size: 22px; }}
    [data-testid="stMetricValue"] {{ font-size: 17px !important; }}
    [data-testid="column"] {{ min-width: 0 !important; }}
}}
</style>
"""


def inject():
    st.markdown(CSS, unsafe_allow_html=True)


# ── 재사용 컴포넌트 ──
def card(html: str):
    st.markdown(f'<div class="ck-card">{html}</div>', unsafe_allow_html=True)


def kv_rows(pairs) -> str:
    return "".join(
        f'<div class="ck-row"><span class="ck-k">{k}</span>'
        f'<span class="ck-v">{v}</span></div>' for k, v in pairs
    )


def pill(text: str, color: str) -> str:
    return (f'<span class="ck-pill" style="background:{color}22;color:{color};'
            f'border:1px solid {color}55">{text}</span>')


def bar(pct: float, color: str) -> str:
    p = max(0, min(100, pct))
    return (f'<div class="ck-bar-wrap"><div class="ck-bar" '
            f'style="width:{p}%;background:{color}"></div></div>')


def score_color(s) -> str:
    if s is None or s < 0:
        return FAINT
    if s >= 80:
        return UP
    if s >= 60:
        return PIVOT
    return DOWN


def dots(count: int, total: int = 25, on=DOWN, off=LINE) -> str:
    d = "".join(
        f'<span class="ck-dot" style="background:{on if i < count else off}"></span>'
        for i in range(total)
    )
    return f'<div class="ck-dot-row">{d}</div>'


def won(n) -> str:
    try:
        return f"{round(float(n)):,}"
    except (TypeError, ValueError):
        return "—"


def pct(n, d=1) -> str:
    try:
        return f"{float(n)*100:.{d}f}%"
    except (TypeError, ValueError):
        return "—"

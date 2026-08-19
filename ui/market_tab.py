"""
ui/market_tab.py — 시장 탭 (M)

오닐 시스템에서 M은 점수가 아니라 스위치입니다.
개별 종목이 아무리 좋아도 시장이 조정이면 신규 매수를 하지 않습니다.
그래서 이 탭이 앱의 첫 화면이고, 여기 판정이 다른 탭의 매수 문구를 바꿉니다.

이 탭이 지키는 원칙: **숫자만 던지지 않고 근거를 같이 보여줍니다.**
"분산일 4개"라고만 하면 믿을 이유가 없으니,
어느 날이 왜 분산일로 잡혔는지 등락률·거래량 증가율을 표로 같이 냅니다.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from canslim_kr import market as mkt
from . import charts
from .style import (card, kv_rows, pill, dots, won, pct,
                    UP, DOWN, PIVOT, OK, DIM, FAINT, TEXT, LINE)

STATE_KO = {
    "CONFIRMED_UPTREND": ("확인된 상승 추세", UP, "신규 매수 가능"),
    "UPTREND_UNDER_PRESSURE": ("상승 추세 · 압박", PIVOT, "신규 매수 축소"),
    "RALLY_ATTEMPT": ("반등 시도", PIVOT, "소량 시험 매수"),
    "MARKET_IN_CORRECTION": ("시장 조정", DOWN, "신규 매수 금지"),
}

# 상태별 쉬운 해석 — 오닐 원문의 취지를 우리말로 풀어 씁니다
PLAIN = {
    "CONFIRMED_UPTREND":
        "기관 매도 흔적(분산일)이 적고 지수가 주요 이평선 위에 있습니다. "
        "오닐 기준으로 돌파 매수의 성공률이 가장 높은 국면입니다. "
        "다만 이 국면에서도 종목 선정 기준을 낮추지는 않습니다.",
    "UPTREND_UNDER_PRESSURE":
        "추세는 살아 있지만 기관 매도가 쌓이고 있습니다. "
        "새 돌파의 실패율이 올라가는 구간이라, 신규 진입은 절반 이하로 줄이고 "
        "이미 보유한 종목의 손절선을 다시 확인하는 게 맞습니다.",
    "RALLY_ATTEMPT":
        "조정에서 반등을 시도 중입니다. 후속일(FTD)이 나왔다면 새 상승장의 "
        "시작일 수 있지만, 오닐도 후속일의 상당수가 실패한다고 했습니다. "
        "소량으로 시험 진입해 시장이 증명하게 두는 국면입니다.",
    "MARKET_IN_CORRECTION":
        "지수가 조정 국면입니다. 오닐 통계에서 돌파 매수 실패의 대부분이 "
        "이 국면에서 발생합니다. 신규 매수를 멈추고 현금을 지키는 것이 "
        "이 전략에서 가장 수익 기여도가 높은 행동입니다.",
}


@st.cache_data(ttl=1800, show_spinner=False)
def _index_data(_hub, code: str, days: int = 760) -> pd.DataFrame:
    from canslim_kr.datahub import last_business_day
    end = last_business_day()
    start = (pd.Timestamp(end) - pd.Timedelta(days=days)).strftime("%Y%m%d")
    return _hub.index_ohlcv(code, start, end)


def _gauge_block(name: str, ms, idx: pd.DataFrame, cfg):
    label, color, verb = STATE_KO.get(ms.state, (ms.state, DIM, ""))

    card(
        f'<div class="ck-label">M · {name} 시장 방향</div>'
        f'<div class="ck-big" style="color:{color}">{label}</div>'
        f'<div class="ck-sub" style="color:{TEXT};font-weight:600">{verb} '
        f'· 허용 투자비중 {pct(ms.max_exposure, 0)}</div>'
        f'<div class="ck-note">{PLAIN.get(ms.state, "")}</div>'
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("분산일", f"{ms.distribution_days}개",
              help="최근 25거래일 중 지수가 0.2% 이상 하락하며 거래량이 늘어난 날. "
                   "기관 매도의 흔적입니다. 4개부터 압박, 6개부터 조정으로 봅니다.")
    c2.metric("50일선", "위" if ms.above_ma50 else "아래" if ms.above_ma50 is False else "—")
    c3.metric("고점 대비", pct(ms.pct_from_high, 1) if ms.pct_from_high is not None else "—")

    st.markdown(
        f'<div class="ck-label" style="margin-top:8px">분산일 누적 '
        f'{ms.distribution_days} / 25거래일</div>{dots(ms.distribution_days)}',
        unsafe_allow_html=True)


def _distribution_section(idx: pd.DataFrame, cfg, name: str):
    ev = charts.distribution_evidence(idx, cfg)
    if ev is None or ev.empty:
        st.info("분산일 판정에 필요한 데이터가 부족합니다.")
        return ev

    active = ev[ev["활성"]]
    expired = ev[ev["소멸"]]

    with st.expander(f"분산일 판정 근거 ({len(active)}개 활성"
                     f"{f', {len(expired)}개 소멸' if len(expired) else ''})", expanded=False):
        st.markdown(
            '<div class="ck-note">'
            '<b>분산일 조건</b> — ① 지수가 전일 대비 0.2% 이상 하락 '
            '<b>그리고</b> ② 거래량이 전일보다 증가.<br>'
            '가격이 내리는데 거래량이 늘었다는 것은 대량 보유자(기관)가 '
            '팔고 있다는 뜻입니다. 개인 매도만으로는 거래량이 이렇게 늘지 않습니다.<br><br>'
            '<b>소멸 조건</b> — 25거래일이 지나거나, 이후 지수가 그날 종가보다 '
            '5% 이상 오르면 그 분산일은 무효가 됩니다. 시장이 그 매도를 소화했다는 뜻입니다.'
            '</div>', unsafe_allow_html=True)

        show = ev[ev["분산일"]].copy()
        if show.empty:
            st.success("최근 25거래일 중 분산일이 없습니다. 기관 매도 압력이 낮은 상태입니다.")
        else:
            show = show.assign(
                날짜=[d.strftime("%m/%d") for d in show.index],
                등락률=show["등락률"].map(lambda x: f"{x:+.2%}"),
                거래량=show["거래량변화"].map(
                    lambda x: f"{x:+.0%}" if pd.notna(x) else "—"),
                상태=["소멸" if s else "활성" for s in show["소멸"]],
            )[["날짜", "등락률", "거래량", "상태"]]
            st.dataframe(show, hide_index=True, use_container_width=True)
            st.caption("거래량 열은 전일 대비 증감률입니다. 양수여야 분산일 조건을 충족합니다.")
    return ev


def _ftd_section(idx: pd.DataFrame, cfg, name: str, ms):
    is_kq = "KOSDAQ" in name.upper()
    thr = cfg.M.ftd_gain_kosdaq if is_kq else cfg.M.ftd_gain_kospi
    ftd = mkt.find_follow_through_day(idx, cfg, is_kosdaq=is_kq)

    if ftd:
        days_ago = ftd["days_since"]
        fresh = days_ago <= 25
        card(
            f'<div class="ck-label">후속일 (Follow-Through Day)</div>'
            f'<div class="ck-big" style="color:{OK if fresh else DIM}">'
            f'{ftd["date"]}</div>'
            f'<div class="ck-sub">랠리 저점 {ftd["rally_low_date"]} 이후 '
            f'<b>{ftd["day_number"]}일차</b>에 <b>{ftd["gain"]:+.2%}</b> 상승 '
            f'· {days_ago}거래일 경과</div>'
            + (f'<div class="ck-note" style="color:{OK}">최근 발생한 후속일입니다. '
               f'새 상승장의 출발점일 수 있습니다.</div>' if fresh else
               f'<div class="ck-note">발생한 지 오래된 후속일입니다. '
               f'현재 국면 판정에는 분산일 누적이 더 중요합니다.</div>')
        )
    else:
        card(
            f'<div class="ck-label">후속일 (Follow-Through Day)</div>'
            f'<div class="ck-big" style="color:{DIM}">미발생</div>'
            f'<div class="ck-note">최근 90거래일에서 후속일 조건을 충족한 날이 없습니다. '
            f'조정 국면이라면 후속일이 나올 때까지 신규 매수를 미루는 것이 오닐 규칙입니다.</div>'
        )

    fe = charts.ftd_evidence(idx, cfg, is_kosdaq=is_kq)
    with st.expander("후속일 판정 근거", expanded=False):
        st.markdown(
            f'<div class="ck-note">'
            f'<b>후속일이란</b> — 하락이 멈추고 반등을 시작한 뒤, '
            f'거래량을 동반한 강한 상승이 나오는 날입니다. '
            f'기관이 다시 사기 시작했다는 신호로 봅니다.<br><br>'
            f'<b>조건</b><br>'
            f'① 랠리 저점 이후 <b>{cfg.M.ftd_min_day}~{cfg.M.ftd_max_day}일차</b>일 것 — '
            f'너무 빠른 반등은 눌림목일 뿐이라 오닐은 1~3일차를 인정하지 않았습니다.<br>'
            f'② 지수 상승률 <b>{thr:+.1%}</b> 이상 — '
            f'{"코스닥은 변동성이 커서 코스피보다 높은 기준을 씁니다." if is_kq else "코스피는 미국 지수보다 일변동성이 낮아 기준을 +1.0%로 낮춰 적용합니다."}<br>'
            f'③ 거래량이 전일보다 증가 — 이게 없으면 매수 주체가 없는 반등입니다.'
            f'</div>', unsafe_allow_html=True)

        if fe is not None and not fe.empty:
            show = fe.assign(
                날짜=[d.strftime("%m/%d") for d in fe.index],
                일차=fe["반등일차"],
                등락률=fe["등락률"].map(lambda x: f"{x:+.2%}" if pd.notna(x) else "—"),
                상승폭=["○" if v else "×" for v in fe["상승폭충족"]],
                일차조건=["○" if v else "×" for v in fe["일차충족"]],
                거래량=["○" if v else ("×" if v is False else "—") for v in fe["거래량증가"]],
                판정=["FTD" if v else "" for v in fe["FTD"]],
            )[["날짜", "일차", "등락률", "상승폭", "일차조건", "거래량", "판정"]]
            st.dataframe(show, hide_index=True, use_container_width=True)
            st.caption("세 조건(상승폭·일차·거래량)이 모두 ○ 인 날만 후속일로 인정됩니다.")
    return ftd


def render(hub, cfg):
    st.markdown('<div class="ck-label">시장 판정 · 오닐 M 지표</div>',
                unsafe_allow_html=True)

    from canslim_kr.datahub import IDX_KOSPI, IDX_KOSDAQ

    tabs = st.tabs(["코스피", "코스닥"])
    results = {}
    for tab, (name, code) in zip(tabs, (("KOSPI", IDX_KOSPI), ("KOSDAQ", IDX_KOSDAQ))):
        with tab:
            try:
                idx = _index_data(hub, code)
            except Exception as e:
                st.error(f"{name} 지수 조회 실패: {e}")
                continue
            if idx is None or idx.empty or len(idx) < 220:
                st.warning(f"{name} 데이터가 부족합니다 (최소 220거래일 필요).")
                continue

            ms = mkt.assess_market(idx, cfg, index_name=name)
            results[name] = ms

            _gauge_block(name, ms, idx, cfg)

            ev = charts.distribution_evidence(idx, cfg)
            ftd = mkt.find_follow_through_day(idx, cfg,
                                              is_kosdaq=("KOSDAQ" in name))
            period = st.select_slider("차트 기간", options=[90, 180, 260, 400],
                                      value=180, key=f"per_{name}",
                                      format_func=lambda x: f"{x}일")
            st.plotly_chart(
                charts.index_chart(idx, ev, ftd, days=period,
                                   title=f"{name} · 분산일 ▼ / 후속일 ▲"),
                use_container_width=True, config={"displayModeBar": False})

            _distribution_section(idx, cfg, name)
            _ftd_section(idx, cfg, name, ms)

            # 판정 근거 종합
            with st.expander("이 판정이 나온 논리", expanded=False):
                for c in ms.commentary:
                    st.markdown(f'<div class="ck-note">· {c}</div>',
                                unsafe_allow_html=True)
                st.markdown(
                    f'<div class="ck-note" style="margin-top:10px">'
                    f'<b>판정 순서</b><br>'
                    f'1. 지수가 50일선 아래이고 고점 대비 −8% 이상이면 → 조정<br>'
                    f'2. 그 상태에서 최근 후속일이 있으면 → 반등 시도<br>'
                    f'3. 분산일 {cfg.M.dd_correction_threshold}개 이상이면 → 조정<br>'
                    f'4. 분산일 {cfg.M.dd_pressure_threshold}개 이상이면 → 압박<br>'
                    f'5. 그 외 → 확인된 상승 추세'
                    f'</div>', unsafe_allow_html=True)

            st.caption(f"기준일 {idx.index[-1].date()} · 종가 {won(idx['종가'].iloc[-1])} "
                       f"· 데이터 {len(idx):,}거래일")

    return results

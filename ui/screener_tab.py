"""
ui/screener_tab.py — 전 종목 스크리닝 탭

주의: 전 종목 시세 수집은 처음 한 번이 오래 걸립니다(수십 분).
그래서 단계를 나누고 진행 상황을 보여주며, 중간 결과를 캐시합니다.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .style import card, kv_rows, pill, score_color, won, pct, UP, DOWN, PIVOT, DIM, FAINT


@st.cache_data(ttl=3600, show_spinner=False)
def _prefilter(_kr):
    return _kr.prefilter()


@st.cache_data(ttl=3600, show_spinner=False)
def _rs_table(_kr, codes: tuple):
    return _kr.build_rs_table(list(codes))


def render(hub, kr, cfg, market_states: dict):
    st.markdown('<div class="ck-label">전 종목 스크리닝</div>', unsafe_allow_html=True)

    blocked = any(m.state == "MARKET_IN_CORRECTION" for m in market_states.values()) \
        if market_states else False
    if blocked:
        st.error("시장이 조정 국면입니다. 스크리닝 결과는 관찰용으로만 쓰세요.")

    st.markdown(
        '<div class="ck-note">'
        '전 종목 시세를 모아 RS Rating을 산출한 뒤, 신고가 근접 종목만 남기고 '
        '재무·수급까지 정밀 분석합니다. 가장 비싼 재무 조회를 마지막에 두는 순서라 '
        '실용적인 속도가 나옵니다.<br><br>'
        '<b>첫 실행은 오래 걸립니다</b> — 종목별 1년치 시세를 순차로 받기 때문입니다. '
        '한 번 받아두면 1시간 동안 캐시됩니다.</div>',
        unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    deep_n = c1.slider("정밀 분석 종목 수", 5, 60, 20, step=5,
                       help="RS 통과 종목 중 상위 몇 개를 재무·수급까지 볼지")
    limit = c2.selectbox("시세 수집 범위", [200, 400, 800, "전체"], index=1,
                         help="전체는 2,000종목 이상이라 매우 오래 걸립니다")

    if not st.button("스크리닝 실행", type="primary"):
        prev = st.session_state.get("scan_result")
        if prev:
            st.caption(f"직전 결과 ({prev['기준일']})")
            _show(prev, blocked)
        return

    with st.status("스크리닝 진행 중", expanded=True) as status:
        st.write("1/4 유니버스 필터")
        uni = _prefilter(kr)
        st.write(f"   → {len(uni):,}종목")

        codes = uni["Code"].tolist()
        if limit != "전체":
            uni = uni.nlargest(int(limit), "시가총액")
            codes = uni["Code"].tolist()
            st.write(f"   → 시총 상위 {len(codes):,}종목으로 축소")

        st.write("2/4 시세 수집 · RS Rating 산출 (가장 오래 걸립니다)")
        rs = _rs_table(kr, tuple(codes))
        st.write(f"   → RS {cfg.L.rs_rating_min}+ "
                 f"{(rs['rs_rating'] >= cfg.L.rs_rating_min).sum():,}종목")

        st.write("3/4 신고가 근접 필터")
        merged = uni.merge(rs, left_on="Code", right_index=True, how="inner")
        cand = merged[merged["rs_rating"] >= cfg.L.rs_rating_min] \
            .sort_values("rs_rating", ascending=False)

        st.write(f"4/4 상위 {deep_n}종목 정밀 분석")
        reports, prog = [], st.progress(0.0)
        for i, (_, r) in enumerate(cand.head(deep_n).iterrows()):
            try:
                rep = kr.analyze(r["Code"], r.get("Name"), r.get("Market"),
                                 account_size=st.session_state.get("account_size") or None,
                                 with_fundamentals=bool(st.session_state.get("dart_key")),
                                 rs_rating=int(r["rs_rating"]))
                reports.append(rep.to_dict())
            except Exception:
                pass
            prog.progress((i + 1) / min(deep_n, len(cand)))
        prog.empty()
        reports.sort(key=lambda x: x["total_score"], reverse=True)
        status.update(label=f"완료 — {len(reports)}종목 분석", state="complete")

    from canslim_kr.datahub import last_business_day
    result = {"기준일": last_business_day(), "유니버스": len(uni),
              "RS통과": int(len(cand)), "종목": reports}
    st.session_state["scan_result"] = result
    _show(result, blocked)


def _show(result: dict, blocked: bool):
    reports = result.get("종목", [])
    if not reports:
        st.info("조건을 통과한 종목이 없습니다. 판정 기준을 '완화'로 바꿔보세요.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("유니버스", f"{result['유니버스']:,}")
    c2.metric("RS 통과", f"{result['RS통과']:,}")
    c3.metric("매수 구간",
              sum(1 for r in reports
                  if "매수 가능" in str(r.get("trade_plan", {}).get("상태", ""))))

    from .watchlist import add_item
    for r in reports:
        plan = r.get("trade_plan", {})
        sc, col = r["total_score"], score_color(r["total_score"])
        status = plan.get("상태", "—")
        is_buy = "매수 가능" in str(status) and not blocked
        base = r.get("base") or {}
        rs = (r["factors"].get("L", {}).get("detail") or {}).get("RS Rating")

        card(
            f'<div style="display:flex;justify-content:space-between">'
            f'<div><div style="font-size:15px;font-weight:700">{r["name"]}</div>'
            f'<div style="font-size:11px;color:{FAINT}" class="ck-num">'
            f'{r["code"]} · {r["market"]}{" · RS " + str(rs) if rs else ""}</div></div>'
            f'<div style="text-align:right"><div style="font-size:20px;font-weight:800;'
            f'color:{col}" class="ck-num">{sc}</div>'
            f'<div style="font-size:10px;color:{FAINT}">{r["grade"]}</div></div></div>'
            f'<div style="margin-top:8px">{pill(status, UP if is_buy else DIM)}'
            f'{pill(base.get("pattern","NONE"), PIVOT if base.get("found") else FAINT)}</div>'
            + (f'<div class="ck-row" style="margin-top:6px">'
               f'<span class="ck-k">현재 {won(plan.get("현재가"))}</span>'
               f'<span class="ck-k">피벗 <b style="color:{PIVOT}">'
               f'{won(plan.get("피벗(매수기준가)"))}</b></span>'
               f'<span class="ck-k">손절 {won(plan.get("손절가"))}</span></div>'
               if plan.get("피벗(매수기준가)") else "")
        )
        if st.button("관심목록에 추가", key=f"scan_add_{r['code']}"):
            st.toast("추가" if add_item(r["code"], r["name"], r["market"])
                     else "이미 있음")

    st.download_button(
        "결과 내려받기 (JSON)",
        data=pd.Series(result).to_json(indent=2, force_ascii=False),
        file_name=f"screening_{result['기준일']}.json", mime="application/json")

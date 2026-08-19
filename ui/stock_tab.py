"""
ui/stock_tab.py — 종목 분석 탭

종목코드나 이름을 넣으면 오닐 7항목을 전부 돌리고,
'왜 그 점수인지'와 '언제 얼마에 사고 어디서 자르는지'를 차트 위에 그립니다.

이 탭의 설계 원칙: 결론보다 근거를 크게 보여줍니다.
점수 86점이라는 숫자보다, 그 86점이 어느 지표에서 왔는지가 판단에 필요한 정보입니다.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
import streamlit as st

from canslim_kr import indicators as ind
from canslim_kr.datahub import last_business_day
from . import charts
from .style import (card, kv_rows, pill, bar, score_color, won, pct,
                    UP, DOWN, PIVOT, OK, DIM, FAINT, TEXT, LINE)

FACTOR_KO = {
    "C": ("최근 분기 실적", "전년 동기 대비 이익·매출이 얼마나 늘었는가. "
                          "오닐은 분기 EPS +25% 이상을 요구했습니다."),
    "A": ("연간 실적", "3년 연평균 이익 성장률과 ROE. 일시적 호실적이 아니라 "
                      "구조적으로 돈을 버는 회사인지 봅니다."),
    "N": ("신고가 · 베이스", "52주 신고가 근처인가, 매수 기준가(피벗)를 정의할 "
                           "베이스가 형성돼 있는가."),
    "S": ("수급 물량", "거래량으로 본 매집 강도, 그리고 유상증자·CB 같은 "
                      "물량 희석 이벤트가 있는가."),
    "L": ("주도주 여부", "RS Rating — 시장 전체 대비 상대강도 순위. "
                       "오닐은 80 미만은 후발주로 보고 제외했습니다."),
    "I": ("기관 수급", "기관·외국인의 실제 순매수. 한국은 이 데이터가 일 단위로 "
                      "공개돼 미국보다 정밀하게 볼 수 있습니다."),
}

BASE_KO = {"CUP_HANDLE": "손잡이 달린 컵", "DOUBLE_BOTTOM": "이중 바닥",
           "FLAT_BASE": "평평한 베이스", "ASCENDING": "상승 삼각형", "NONE": "베이스 없음"}
STATUS_KO = {"BREAKOUT": "돌파 발생", "HANDLE": "손잡이 형성 중",
             "BUILDING": "베이스 형성 중", "EXTENDED": "확장 · 추격 금지",
             "FAILED": "베이스 실패"}


@st.cache_data(ttl=3600, show_spinner=False)
def load_listing(_hub) -> pd.DataFrame:
    return _hub.listing()


def resolve_code(hub, query: str) -> Optional[tuple]:
    """종목코드 6자리 또는 종목명으로 찾습니다."""
    q = (query or "").strip()
    if not q:
        return None
    lst = load_listing(hub)
    if q.isdigit():
        code = q.zfill(6)
        row = lst[lst["Code"] == code]
        if len(row):
            r = row.iloc[0]
            return code, r["Name"], r.get("Market", "KOSPI")
        return code, code, "KOSPI"
    hit = lst[lst["Name"].fillna("").str.contains(q, case=False, na=False)]
    if hit.empty:
        return None
    if len(hit) == 1 or hit.iloc[0]["Name"] == q:
        r = hit.iloc[0]
        return r["Code"], r["Name"], r.get("Market", "KOSPI")
    return ("MULTI", hit.head(12), None)


@st.cache_data(ttl=900, show_spinner=False)
def analyze_cached(_kr, code: str, account: float, with_fund: bool):
    rep = _kr.analyze(code, account_size=account or None,
                      with_fundamentals=with_fund)
    return rep.to_dict()


@st.cache_data(ttl=900, show_spinner=False)
def price_cached(_hub, code: str, days: int = 760) -> pd.DataFrame:
    end = last_business_day()
    start = (pd.Timestamp(end) - pd.Timedelta(days=days)).strftime("%Y%m%d")
    return _hub.ohlcv(code, start, end)


# ─────────────────────────────────────────────────────────────
def _verdict_card(rep: dict, market_blocked: bool):
    sc, gr = rep["total_score"], rep["grade"]
    col = score_color(sc)
    plan = rep.get("trade_plan", {})
    status = plan.get("상태", "—")
    if market_blocked:
        status = "신규 매수 금지 (시장 조정)"

    card(
        f'<div class="ck-label">{rep["market"]} · {rep["code"]} · {rep["date"]}</div>'
        f'<div class="ck-big">{rep["name"]}</div>'
        f'<div style="display:flex;align-items:baseline;gap:10px;margin-top:10px">'
        f'<span style="font-size:40px;font-weight:800;color:{col};'
        f'font-variant-numeric:tabular-nums;letter-spacing:-.03em">{sc}</span>'
        f'<span style="font-size:15px;color:{DIM}">{gr}</span></div>'
        f'<div class="ck-sub">{rep["verdict"]}</div>'
        f'<div style="margin-top:10px">'
        f'{pill(status, DOWN if market_blocked else (UP if "매수 가능" in str(status) else DIM))}'
        f'{pill("시장: " + str(rep["market_state"]["state"]), DIM)}</div>'
    )


def _factor_section(rep: dict):
    st.markdown('<div class="ck-label">CANSLIM 항목별 점수</div>',
                unsafe_allow_html=True)
    st.plotly_chart(charts.factor_bars(rep["factors"]),
                    use_container_width=True, config={"displayModeBar": False})

    for k in ("C", "A", "N", "S", "L", "I"):
        f = rep["factors"].get(k)
        if not f:
            continue
        name, why = FACTOR_KO[k]
        s = f.get("score", -1)
        col = score_color(s)
        label = "데이터 없음" if (s is None or s < 0) else f"{s:.0f}점"

        with st.expander(f"{k} · {name} — {label}", expanded=False):
            st.markdown(f'<div class="ck-note"><b>무엇을 보는가</b><br>{why}</div>',
                        unsafe_allow_html=True)
            det = f.get("detail") or {}
            if det:
                rows = []
                for dk, dv in det.items():
                    if isinstance(dv, bool):
                        v = "예" if dv else "아니오"
                    elif isinstance(dv, dict):
                        v = ", ".join(f"{a} {b}" for a, b in dv.items()) or "—"
                    elif isinstance(dv, float):
                        v = (f"{dv:.2%}" if abs(dv) < 10 else f"{dv:,.2f}")
                    elif isinstance(dv, list):
                        v = ", ".join(map(str, dv[:5]))
                    else:
                        v = str(dv)
                    rows.append((dk, v))
                st.markdown(f'<div style="margin-top:8px">{kv_rows(rows)}</div>',
                            unsafe_allow_html=True)
            for n in (f.get("notes") or []):
                st.markdown(f'<div class="ck-note">· {n}</div>',
                            unsafe_allow_html=True)


def _base_section(rep: dict, df: pd.DataFrame):
    b = rep.get("base", {})
    plan = rep.get("trade_plan", {})

    st.markdown('<div class="ck-label">베이스 구조와 매매 지점</div>',
                unsafe_allow_html=True)
    st.plotly_chart(
        charts.stock_chart(df, b, plan, days=260,
                           title=f"{rep['name']} · 노란 띠 = 매수 구간"),
        use_container_width=True, config={"displayModeBar": False})

    if not b.get("found"):
        st.warning("유효한 베이스가 없습니다. 매수 기준가(피벗)를 정의할 수 없으므로 "
                   "오닐 규칙상 진입하지 않습니다. 베이스가 형성될 때까지 관찰합니다.")
        for n in (b.get("notes") or []):
            st.caption(f"· {n}")
        return

    rows = [
        ("패턴", BASE_KO.get(b["pattern"], b["pattern"])),
        ("상태", STATUS_KO.get(b["status"], b["status"])),
        ("형성 기간", f"{b['weeks']}주"),
        ("깊이 (좌측고점→저점)", pct(b["depth"], 1)),
        ("베이스 차수", f"{b['stage']}차"),
        ("좌측 고점", won(b.get("left_high"))),
        ("베이스 저점", won(b.get("low"))),
        ("피벗 (매수 기준가)", f"<span style='color:{PIVOT}'>{won(b.get('pivot'))}</span>"),
    ]
    if b.get("handle_depth") is not None:
        rows.insert(4, ("손잡이 조정폭", pct(b["handle_depth"], 1)))
    if b.get("prior_uptrend") is not None:
        rows.insert(5, ("베이스 직전 상승", pct(b["prior_uptrend"], 0)))
    card(kv_rows(rows))

    with st.expander("베이스를 이렇게 읽습니다", expanded=False):
        st.markdown(
            f'<div class="ck-note">'
            f'<b>왜 베이스가 필요한가</b><br>'
            f'주가가 오르다 멈추고 옆으로 기는 구간에서, 단기 차익 세력이 빠져나가고 '
            f'장기 보유자만 남습니다. 그 정리가 끝난 지점(피벗)을 거래량과 함께 '
            f'뚫을 때가 오닐이 정의한 유일한 매수 시점입니다.<br><br>'
            f'<b>깊이</b> — {pct(b["depth"],1)}. 오닐 원본은 12~33%를 봤고 '
            f'이 앱은 한국 변동성을 감안해 40%까지 허용합니다. '
            f'너무 깊으면 회복에 힘이 들고, 너무 얕으면 물량 정리가 덜 된 것입니다.<br><br>'
            f'<b>차수</b> — {b["stage"]}차 베이스. '
            f'{"1~2차는 성공률이 높습니다." if b["stage"] <= 2 else "3차 이상은 이미 시장이 다 아는 종목이라 실패율이 급증합니다."}<br><br>'
            f'<b>피벗</b> — {won(b.get("pivot"))}원. 손잡이 고점(없으면 좌측 고점)입니다. '
            f'이 가격 위에서만, 그것도 5% 이내에서만 삽니다.'
            f'</div>', unsafe_allow_html=True)
        for n in (b.get("notes") or []):
            st.markdown(f'<div class="ck-note">· {n}</div>', unsafe_allow_html=True)


def _plan_section(rep: dict, market_blocked: bool):
    plan = rep.get("trade_plan", {})
    st.markdown('<div class="ck-label">매매 계획</div>', unsafe_allow_html=True)

    if market_blocked:
        st.error("시장이 조정 국면입니다. 오닐 규칙상 이 종목의 점수와 무관하게 "
                 "신규 매수를 하지 않습니다. 관찰 목록에만 올려두세요.")

    status = plan.get("상태", "—")
    color = UP if "매수 가능" in str(status) else (PIVOT if "대기" in str(status) else DIM)
    card(
        f'<div class="ck-big" style="color:{DOWN if market_blocked else color};font-size:20px">'
        f'{"신규 매수 금지" if market_blocked else status}</div>'
        f'<div class="ck-sub">{plan.get("사유","")}</div>'
    )

    if plan.get("피벗(매수기준가)"):
        c1, c2, c3 = st.columns(3)
        zone = plan.get("매수구간") or [None, None]
        c1.metric("현재가", won(plan.get("현재가")))
        c2.metric("피벗", won(plan.get("피벗(매수기준가)")))
        c3.metric("손절가", won(plan.get("손절가")))

        rows = [
            ("매수 구간", f"{won(zone[0])} ~ {won(zone[1])}"),
            ("손절폭", plan.get("손절폭", "—")),
            ("1차 목표 (+20%)", won(plan.get("1차 목표"))),
            ("2차 목표 (+25%)", won(plan.get("2차 목표"))),
            ("손익비", plan.get("손익비", "—")),
            ("허용 비중", plan.get("허용비중", "—")),
        ]
        if plan.get("권장수량"):
            rows += [("권장 수량", f"{won(plan['권장수량'])}주"),
                     ("투입 금액", f"{won(plan.get('투입금액'))}원"),
                     ("손절 시 최대손실", f"{won(plan.get('최대손실'))}원")]
        card(kv_rows(rows))

        if plan.get("분할매수"):
            with st.expander("분할 매수 계획", expanded=False):
                st.markdown(
                    '<div class="ck-note">한 번에 전량 사지 않습니다. '
                    '돌파가 진짜인지 시장이 확인해주는 만큼만 비중을 늘립니다.</div>',
                    unsafe_allow_html=True)
                df = pd.DataFrame(plan["분할매수"])
                df["가격"] = df["가격"].map(won)
                st.dataframe(df, hide_index=True, use_container_width=True)

    if plan.get("보유규칙"):
        with st.expander("보유 중 지킬 규칙", expanded=False):
            for r in plan["보유규칙"]:
                st.markdown(f'<div class="ck-note">· {r}</div>',
                            unsafe_allow_html=True)


def render(hub, kr, cfg, market_states: dict):
    st.markdown('<div class="ck-label">종목 분석 · 오닐 전항목</div>',
                unsafe_allow_html=True)

    c1, c2 = st.columns([3, 1])
    q = c1.text_input("종목코드 또는 종목명", key="stock_query",
                      placeholder="예: 005930 또는 삼성전자",
                      label_visibility="collapsed")
    go = c2.button("분석", type="primary", key="stock_go")

    account = st.session_state.get("account_size", 0)
    with_fund = bool(st.session_state.get("dart_key"))

    if not q:
        st.info("종목코드 6자리 또는 종목명을 입력하세요. "
                "재무 분석(C·A 항목)에는 DART 키가 필요합니다.")
        return None

    hit = resolve_code(hub, q)
    if hit is None:
        st.warning("해당 종목을 찾지 못했습니다.")
        return None

    if hit[0] == "MULTI":
        st.markdown('<div class="ck-note">여러 종목이 검색됐습니다. 하나를 고르세요.</div>',
                    unsafe_allow_html=True)
        opts = hit[1]
        pick = st.selectbox(
            "종목 선택",
            options=opts["Code"].tolist(),
            format_func=lambda c: f"{opts[opts.Code==c].Name.iloc[0]} ({c})",
            key="multi_pick", label_visibility="collapsed")
        code = pick
        name = opts[opts.Code == pick].Name.iloc[0]
        market = opts[opts.Code == pick].get("Market", pd.Series(["KOSPI"])).iloc[0]
    else:
        code, name, market = hit

    blocked = False
    ms = market_states.get("KOSDAQ" if "KOSDAQ" in str(market).upper() else "KOSPI")
    if ms is not None:
        blocked = (ms.state == "MARKET_IN_CORRECTION")

    with st.spinner(f"{name} 분석 중 — 시세·수급·재무·공시를 모읍니다"):
        try:
            rep = analyze_cached(kr, code, float(account or 0), with_fund)
            df = price_cached(hub, code)
        except Exception as e:
            st.error(f"분석 실패: {e}")
            st.caption("시세 데이터가 1년 미만이거나 거래정지 종목일 수 있습니다.")
            return None

    _verdict_card(rep, blocked)

    # 관심목록 추가
    ca, cb = st.columns(2)
    if ca.button("관심목록에 추가", key=f"add_{code}"):
        from .watchlist import add_item
        added = add_item(code, rep["name"], rep["market"])
        st.toast("추가했습니다" if added else "이미 목록에 있습니다")
    cb.download_button("분석 결과 내려받기",
                       data=pd.Series(rep).to_json(indent=2, force_ascii=False),
                       file_name=f"{code}_{rep['date']}.json",
                       mime="application/json", key=f"dl_{code}")

    _base_section(rep, df)
    _plan_section(rep, blocked)
    _factor_section(rep)

    # RS Line
    try:
        from canslim_kr.datahub import IDX_KOSPI, IDX_KOSDAQ
        bcode = IDX_KOSDAQ if "KOSDAQ" in str(rep["market"]).upper() else IDX_KOSPI
        bench = hub.index_ohlcv(
            bcode, (pd.Timestamp(last_business_day()) - pd.Timedelta(days=500)
                    ).strftime("%Y%m%d"), last_business_day())
        if bench is not None and not bench.empty:
            st.plotly_chart(charts.rs_line_chart(df["종가"], bench["종가"]),
                            use_container_width=True,
                            config={"displayModeBar": False})
            st.caption("RS Line이 주가보다 먼저 신고가를 내면 오닐이 가장 신뢰한 선행 신호입니다.")
    except Exception:
        pass

    if rep.get("warnings"):
        with st.expander("데이터 주의사항", expanded=False):
            for w in rep["warnings"]:
                st.markdown(f'<div class="ck-note">· {w}</div>',
                            unsafe_allow_html=True)
    return rep

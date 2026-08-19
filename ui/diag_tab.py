"""
ui/diag_tab.py — 데이터 소스 진단

이 탭이 필요한 이유가 있습니다.
KRX·DART·FDR 중 어느 하나가 막히면 화면에는 라이브러리 내부 예외
(예: KeyError: '지수명')만 뜨는데, 그 메시지로는 무엇이 문제인지 알 수 없습니다.

각 데이터 경로를 하나씩 따로 찔러보고 어디까지 되는지 보여줍니다.
"""

from __future__ import annotations

import time
import traceback

import pandas as pd
import streamlit as st

from .style import card, kv_rows, pill, UP, DOWN, PIVOT, OK, DIM, FAINT


def _probe(label: str, fn, hint: str = ""):
    """하나의 데이터 경로를 실행하고 성공/실패와 소요시간을 반환합니다."""
    t0 = time.time()
    try:
        out = fn()
        dt = time.time() - t0
        if out is None or (hasattr(out, "__len__") and len(out) == 0):
            return {"label": label, "ok": False, "msg": "응답은 왔으나 데이터가 비어 있음",
                    "sec": dt, "hint": hint}
        n = len(out) if hasattr(out, "__len__") else 1
        return {"label": label, "ok": True, "msg": f"{n:,}건", "sec": dt, "hint": ""}
    except Exception as e:
        return {"label": label, "ok": False,
                "msg": f"{type(e).__name__}: {str(e)[:120]}",
                "sec": time.time() - t0, "hint": hint,
                "tb": traceback.format_exc()}


def render(hub, kr, cfg):
    st.markdown('<div class="ck-label">데이터 소스 진단</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="ck-note">화면에 알 수 없는 오류가 뜰 때 여기서 '
        '어느 데이터 경로가 막혔는지 확인하세요. '
        'KRX는 서버 위치나 시간대에 따라 일시적으로 차단되기도 합니다.</div>',
        unsafe_allow_html=True)

    if not st.button("진단 실행", type="primary"):
        return

    from canslim_kr.datahub import IDX_KOSPI, IDX_KOSDAQ, last_business_day
    d = last_business_day()
    start = (pd.Timestamp(d) - pd.Timedelta(days=400)).strftime("%Y%m%d")

    probes = [
        ("코스피 지수 (pykrx)",
         lambda: hub.stock.get_index_ohlcv(start, d, IDX_KOSPI, name_display=False),
         "KRX 지수 엔드포인트 차단. 아래 FDR 경로가 살아 있으면 앱은 정상 동작합니다."),
        ("코스피 지수 (FinanceDataReader 폴백)",
         lambda: hub._index_via_fdr(IDX_KOSPI, start, d),
         "두 경로 모두 실패하면 시장 탭을 쓸 수 없습니다."),
        ("코스닥 지수",
         lambda: hub.index_ohlcv(IDX_KOSDAQ, start, d), ""),
        ("종목 마스터 (FinanceDataReader)",
         lambda: hub.fdr.StockListing("KRX"),
         "실패해도 pykrx 폴백이 있습니다."),
        ("종목 마스터 (pykrx 폴백)",
         lambda: hub.stock.get_market_ticker_list(d, market="KOSPI"),
         "두 경로 모두 실패하면 종목 검색·관심목록 추가가 막힙니다."),
        ("개별 종목 시세 (삼성전자)",
         lambda: hub.ohlcv("005930", start, d), ""),
        ("투자자별 수급 (삼성전자)",
         lambda: hub.investor_flow(
             "005930", (pd.Timestamp(d) - pd.Timedelta(days=90)).strftime("%Y%m%d"), d),
         "실패하면 I 항목이 중립 처리됩니다."),
        ("전 종목 스냅샷",
         lambda: hub.market_snapshot(d),
         "실패하면 스크리닝 탭을 쓸 수 없습니다."),
    ]

    if hub.dart_key:
        probes.append(("DART 고유번호 매핑", lambda: hub.corp_codes(),
                       "DART 키가 잘못됐거나 일일 한도(2만건)를 넘겼을 수 있습니다."))
        probes.append(("DART 재무제표 (삼성전자)",
                       lambda: hub.dart_financials("005930", pd.Timestamp(d).year - 1, 4),
                       "실패하면 C·A 항목이 중립 처리됩니다."))
    else:
        st.warning("DART 키가 없어 재무 항목(C·A) 점검을 건너뜁니다.")

    results, prog = [], st.progress(0.0)
    for i, (label, fn, hint) in enumerate(probes):
        prog.progress(i / len(probes), text=label)
        results.append(_probe(label, fn, hint))
    prog.empty()

    ok_n = sum(1 for r in results if r["ok"])
    c1, c2 = st.columns(2)
    c1.metric("정상", f"{ok_n} / {len(results)}")
    c2.metric("총 소요", f"{sum(r['sec'] for r in results):.1f}초")

    for r in results:
        color = OK if r["ok"] else DOWN
        card(
            f'<div style="display:flex;justify-content:space-between;align-items:center">'
            f'<span style="font-size:13px;font-weight:600">{r["label"]}</span>'
            f'{pill("정상" if r["ok"] else "실패", color)}</div>'
            f'<div class="ck-note">{r["msg"]} · {r["sec"]:.1f}초</div>'
            + (f'<div class="ck-note" style="color:{PIVOT}">{r["hint"]}</div>'
               if r.get("hint") else "")
        )
        if not r["ok"] and r.get("tb"):
            with st.expander("상세 오류", expanded=False):
                st.code(r["tb"][-1200:], language="text")

    st.divider()
    st.markdown(
        '<div class="ck-label">자주 나오는 증상</div>'
        '<div class="ck-note">'
        '<b>KeyError: \'지수명\'</b> — pykrx가 지수 이름을 붙이려고 KRX 지수정보를 '
        '조회하는데 그 엔드포인트가 막힌 경우입니다. 시세 자체는 정상 수신되므로 '
        '이름 표시만 끄면 해결됩니다. 이 앱은 이미 꺼둔 상태이며, 그래도 안 되면 '
        'FinanceDataReader로 자동 전환합니다.<br><br>'
        '<b>종목이 검색되지 않음</b> — 종목 마스터 조회 실패입니다. '
        '위 두 마스터 경로 중 하나라도 정상이면 검색이 됩니다.<br><br>'
        '<b>전부 실패</b> — KRX가 서버 IP를 차단했을 수 있습니다. '
        '사이드바에서 캐시를 비우고 몇 분 뒤 다시 시도하거나, '
        'Streamlit Cloud 앱을 재시작(Manage app → Reboot)해 보세요.'
        '</div>', unsafe_allow_html=True)

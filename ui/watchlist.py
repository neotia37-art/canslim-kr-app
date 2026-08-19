"""
ui/watchlist.py — 관심목록

저장 방식에 대해 미리 말해둘 것이 있습니다.
Streamlit Community Cloud는 앱이 재시작되면 파일시스템이 초기화됩니다.
따라서 3중으로 둡니다.

  1) session_state — 화면 조작 중 즉시 반영
  2) watchlist.json — 같은 세션/컨테이너가 살아 있는 동안 유지
  3) 내보내기/불러오기 — 영구 보관은 이걸 써야 합니다

폰에서 쓰신다면 종목을 추가한 뒤 '내보내기'로 파일을 한 번 받아두시는 걸 권합니다.
앱이 재배포되거나 오래 쉬면 2번은 사라집니다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

import pandas as pd
import streamlit as st

from .style import card, kv_rows, pill, score_color, won, pct, UP, DOWN, PIVOT, DIM, FAINT

WL_PATH = Path("./data/watchlist.json")
KEY = "watchlist"


# ─────────────────────────────────────────────────────────────
def _load_file() -> List[Dict[str, Any]]:
    try:
        if WL_PATH.exists():
            return json.loads(WL_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save_file(items: List[Dict[str, Any]]):
    try:
        WL_PATH.parent.mkdir(parents=True, exist_ok=True)
        WL_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    except Exception:
        pass


def get_items() -> List[Dict[str, Any]]:
    if KEY not in st.session_state:
        st.session_state[KEY] = _load_file()
    return st.session_state[KEY]


def set_items(items: List[Dict[str, Any]]):
    st.session_state[KEY] = items
    _save_file(items)


def add_item(code: str, name: str, market: str = "", memo: str = "") -> bool:
    """이미 있으면 False. 중복 추가를 막습니다."""
    items = get_items()
    code = str(code).zfill(6)
    if any(i["code"] == code for i in items):
        return False
    items.append({
        "code": code, "name": name, "market": market, "memo": memo,
        "added": datetime.now().strftime("%Y-%m-%d"),
        "score": None, "grade": None, "status": None,
        "pivot": None, "price": None, "rs": None, "updated": None,
    })
    set_items(items)
    return True


def remove_item(code: str):
    set_items([i for i in get_items() if i["code"] != str(code).zfill(6)])


def update_item(code: str, **fields):
    items = get_items()
    for i in items:
        if i["code"] == str(code).zfill(6):
            i.update(fields)
            i["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    set_items(items)


# ─────────────────────────────────────────────────────────────
def _refresh_one(kr, item: Dict[str, Any], account: float, with_fund: bool) -> Dict:
    rep = kr.analyze(item["code"], account_size=account or None,
                     with_fundamentals=with_fund)
    d = rep.to_dict()
    plan = d.get("trade_plan", {})
    return {
        "score": d["total_score"], "grade": d["grade"],
        "status": plan.get("상태"), "pivot": plan.get("피벗(매수기준가)"),
        "price": plan.get("현재가"),
        "rs": (d["factors"].get("L", {}).get("detail") or {}).get("RS Rating"),
        "base": (d.get("base") or {}).get("pattern"),
        "verdict": d.get("verdict"),
        "name": d.get("name") or item.get("name"),
        "market": d.get("market") or item.get("market"),
    }


def render(hub, kr, cfg, market_states: dict):
    items = get_items()

    st.markdown('<div class="ck-label">관심목록</div>', unsafe_allow_html=True)

    # ── 추가 ──
    with st.form("wl_add", clear_on_submit=True):
        c1, c2 = st.columns([3, 1])
        q = c1.text_input("종목코드 또는 종목명", placeholder="예: 005930 / 삼성전자",
                          label_visibility="collapsed")
        submitted = c2.form_submit_button("추가", use_container_width=True)
    if submitted and q.strip():
        from .stock_tab import resolve_code
        hit = resolve_code(hub, q)
        if hit is None:
            st.warning("종목을 찾지 못했습니다.")
        elif hit[0] == "MULTI":
            st.warning(f"'{q}'에 해당하는 종목이 여러 개입니다. 종목코드로 입력하세요.")
        else:
            code, name, market = hit
            st.success(f"{name} 추가" if add_item(code, name, market)
                       else f"{name}은(는) 이미 목록에 있습니다")
            st.rerun()

    if not items:
        st.info("관심목록이 비어 있습니다. 위에 종목을 넣거나, "
                "종목 분석 탭에서 '관심목록에 추가'를 누르세요.")
        _io_section(items)
        return

    # ── 일괄 갱신 ──
    account = st.session_state.get("account_size", 0)
    with_fund = bool(st.session_state.get("dart_key"))
    c1, c2 = st.columns(2)
    if c1.button(f"전체 재분석 ({len(items)}종목)", type="primary"):
        prog = st.progress(0.0, text="분석 중")
        fails = []
        for n, it in enumerate(items):
            try:
                update_item(it["code"], **_refresh_one(kr, it, account, with_fund))
            except Exception as e:
                fails.append(f"{it.get('name', it['code'])}: {str(e)[:40]}")
            prog.progress((n + 1) / len(items),
                          text=f"{it.get('name', it['code'])} ({n+1}/{len(items)})")
        prog.empty()
        if fails:
            st.warning("일부 실패: " + " / ".join(fails[:4]))
        st.rerun()

    sort_by = c2.selectbox("정렬", ["점수 높은순", "RS 높은순", "추가순", "이름순"],
                           label_visibility="collapsed")

    rows = list(items)
    if sort_by == "점수 높은순":
        rows.sort(key=lambda x: (x.get("score") is None, -(x.get("score") or 0)))
    elif sort_by == "RS 높은순":
        rows.sort(key=lambda x: (x.get("rs") is None, -(x.get("rs") or 0)))
    elif sort_by == "이름순":
        rows.sort(key=lambda x: x.get("name") or "")

    # ── 요약 ──
    scored = [r for r in rows if r.get("score") is not None]
    if scored:
        buyable = [r for r in scored if "매수 가능" in str(r.get("status") or "")]
        c1, c2, c3 = st.columns(3)
        c1.metric("종목", f"{len(rows)}")
        c2.metric("매수 구간", f"{len(buyable)}")
        c3.metric("평균 점수", f"{sum(r['score'] for r in scored)/len(scored):.0f}")

    blocked_kospi = (market_states.get("KOSPI") is not None
                     and market_states["KOSPI"].state == "MARKET_IN_CORRECTION")
    if blocked_kospi:
        st.error("시장이 조정 국면입니다. 아래는 관찰용이며 신규 매수는 하지 않습니다.")

    # ── 목록 ──
    for it in rows:
        sc = it.get("score")
        col = score_color(sc)
        status = it.get("status") or "미분석"
        is_buy = "매수 가능" in str(status) and not blocked_kospi
        gap = None
        if it.get("pivot") and it.get("price"):
            try:
                gap = it["pivot"] / it["price"] - 1
            except Exception:
                gap = None

        with st.container():
            card(
                f'<div style="display:flex;justify-content:space-between;'
                f'align-items:flex-start">'
                f'<div><div style="font-size:16px;font-weight:700">{it.get("name") or it["code"]}</div>'
                f'<div style="font-size:11px;color:{FAINT}" class="ck-num">'
                f'{it["code"]} · {it.get("market","")}'
                f'{" · RS " + str(it["rs"]) if it.get("rs") else ""}</div></div>'
                f'<div style="text-align:right">'
                f'<div style="font-size:22px;font-weight:800;color:{col}" class="ck-num">'
                f'{sc if sc is not None else "—"}</div>'
                f'<div style="font-size:11px;color:{FAINT}">{it.get("grade") or ""}</div>'
                f'</div></div>'
                f'<div style="margin-top:9px">'
                f'{pill(status, UP if is_buy else (DOWN if blocked_kospi else DIM))}'
                f'{pill(it.get("base") or "베이스 없음", PIVOT if it.get("base") and it.get("base")!="NONE" else FAINT)}'
                f'</div>'
                + (f'<div class="ck-row" style="margin-top:8px">'
                   f'<span class="ck-k">현재 {won(it.get("price"))}</span>'
                   f'<span class="ck-k">피벗 <b style="color:{PIVOT}">{won(it.get("pivot"))}</b></span>'
                   f'<span class="ck-k">{"돌파까지 " + pct(gap,1) if gap and gap > 0 else ("돌파 " + pct(-gap,1) + " 진행" if gap is not None else "")}</span>'
                   f'</div>' if it.get("price") else "")
                + (f'<div class="ck-note">{it.get("memo")}</div>' if it.get("memo") else "")
                + (f'<div style="font-size:10px;color:{FAINT};margin-top:6px">'
                   f'갱신 {it["updated"]}</div>' if it.get("updated") else "")
            )
            a, b, c = st.columns([1, 1, 1])
            if a.button("재분석", key=f"r_{it['code']}"):
                try:
                    update_item(it["code"], **_refresh_one(kr, it, account, with_fund))
                    st.rerun()
                except Exception as e:
                    st.error(str(e)[:60])
            with b.popover("메모"):
                memo = st.text_area("메모", value=it.get("memo", ""),
                                    key=f"m_{it['code']}", height=80,
                                    label_visibility="collapsed")
                if st.button("저장", key=f"ms_{it['code']}"):
                    update_item(it["code"], memo=memo)
                    st.rerun()
            if c.button("삭제", key=f"d_{it['code']}"):
                remove_item(it["code"])
                st.rerun()

    _io_section(rows)


def _io_section(items):
    st.divider()
    st.markdown('<div class="ck-label">저장 · 불러오기</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="ck-note">Streamlit Cloud는 앱이 재시작되면 서버 파일이 '
        '초기화됩니다. 목록을 오래 유지하려면 아래 <b>내보내기</b>로 파일을 '
        '한 번 받아두시고, 필요할 때 다시 불러오세요.</div>',
        unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    c1.download_button(
        "내보내기 (JSON)",
        data=json.dumps(items, ensure_ascii=False, indent=2),
        file_name=f"watchlist_{datetime.now():%Y%m%d}.json",
        mime="application/json", use_container_width=True)

    if items:
        df = pd.DataFrame(items)
        c2.download_button(
            "내보내기 (CSV)", data=df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"watchlist_{datetime.now():%Y%m%d}.csv",
            mime="text/csv", use_container_width=True)

    up = st.file_uploader("불러오기 (JSON/CSV)", type=["json", "csv"],
                          label_visibility="collapsed")
    if up is not None:
        try:
            if up.name.endswith(".json"):
                data = json.loads(up.read().decode("utf-8"))
            else:
                data = pd.read_csv(up, dtype={"code": str}).to_dict("records")
            merge = st.radio("방식", ["기존에 합치기", "전부 교체"],
                             horizontal=True, key="wl_merge")
            if st.button("적용", type="primary"):
                data = [{**d, "code": str(d["code"]).zfill(6)} for d in data
                        if d.get("code")]
                if merge == "전부 교체":
                    set_items(data)
                else:
                    cur = get_items()
                    have = {i["code"] for i in cur}
                    set_items(cur + [d for d in data if d["code"] not in have])
                st.success(f"{len(data)}종목 적용")
                st.rerun()
        except Exception as e:
            st.error(f"파일을 읽을 수 없습니다: {e}")

"""
ui/charts.py — 오닐 방식 차트

핵심은 '판정 근거를 차트 위에 그대로 찍는 것'입니다.
분산일이 몇 개라고 숫자만 보여주면 믿을 근거가 없습니다.
어느 날이 왜 분산일인지 차트에 표시하고, 표로 등락률·거래량 증가를 함께 보여줍니다.

색은 한국 관행: 상승 빨강 / 하락 파랑.
plotly 기본값이 반대라 캔들마다 명시적으로 지정합니다.
"""

from __future__ import annotations

from typing import Optional, Dict, Any, List

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .style import UP, DOWN, PIVOT, OK, INK, SURFACE, LINE, TEXT, DIM, FAINT

LAYOUT = dict(
    paper_bgcolor=INK, plot_bgcolor=INK,
    font=dict(color=TEXT, size=11,
              family="Pretendard,Apple SD Gothic Neo,Malgun Gothic,sans-serif"),
    margin=dict(l=8, r=8, t=28, b=8),
    hovermode="x unified",
    xaxis=dict(gridcolor=LINE, showgrid=False, rangeslider=dict(visible=False)),
    yaxis=dict(gridcolor=LINE, side="right", tickformat=","),
    legend=dict(orientation="h", y=1.06, x=0, font=dict(size=10),
                bgcolor="rgba(0,0,0,0)"),
    dragmode="pan",
)


def _candles(df: pd.DataFrame, name="") -> go.Candlestick:
    return go.Candlestick(
        x=df.index, open=df["시가"], high=df["고가"], low=df["저가"], close=df["종가"],
        increasing=dict(line=dict(color=UP, width=1), fillcolor=UP),
        decreasing=dict(line=dict(color=DOWN, width=1), fillcolor=DOWN),
        name=name, showlegend=False,
    )


def _ma(df: pd.DataFrame, n: int, color: str, dash=None) -> go.Scatter:
    s = df["종가"].rolling(n, min_periods=max(2, n // 2)).mean()
    return go.Scatter(x=df.index, y=s, name=f"{n}일선", mode="lines",
                      line=dict(color=color, width=1.2, dash=dash))


# ─────────────────────────────────────────────────────────────
# 1. 지수 차트 — 분산일 · FTD (시장 탭)
# ─────────────────────────────────────────────────────────────
def index_chart(idx: pd.DataFrame, evidence: pd.DataFrame,
                ftd: Optional[Dict] = None, days: int = 180,
                title: str = "KOSPI") -> go.Figure:
    """
    지수 캔들 + 50/200일선 + 분산일 표시 + FTD 표시.

    분산일은 캔들 위에 파란 ▼로, FTD는 아래에 빨간 ▲로 찍습니다.
    랠리 저점부터 FTD까지의 구간은 음영으로 묶어 '반등 며칠째'가 보이게 합니다.
    """
    d = idx.tail(days)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.74, 0.26], vertical_spacing=0.03)

    fig.add_trace(_candles(d), row=1, col=1)
    fig.add_trace(_ma(idx, 50, PIVOT).update(x=d.index,
                  y=idx["종가"].rolling(50, min_periods=25).mean().reindex(d.index)),
                  row=1, col=1)
    fig.add_trace(_ma(idx, 200, DIM).update(x=d.index,
                  y=idx["종가"].rolling(200, min_periods=100).mean().reindex(d.index)),
                  row=1, col=1)

    # 분산일 마커
    if evidence is not None and not evidence.empty:
        ev = evidence[evidence["활성"]] if "활성" in evidence.columns else evidence
        ev = ev[ev.index.isin(d.index)] if isinstance(ev.index, pd.DatetimeIndex) else ev
        if len(ev):
            xs = ev.index if isinstance(ev.index, pd.DatetimeIndex) else pd.to_datetime(ev["날짜"])
            ys = d["고가"].reindex(xs) * 1.008
            fig.add_trace(go.Scatter(
                x=xs, y=ys, mode="markers", name="분산일",
                marker=dict(symbol="triangle-down", size=9, color=DOWN,
                            line=dict(width=0)),
                hovertemplate="분산일 %{x|%m/%d}<extra></extra>",
            ), row=1, col=1)

    # FTD 및 랠리 저점
    if ftd:
        low_d = pd.Timestamp(ftd["rally_low_date"])
        ftd_d = pd.Timestamp(ftd["date"])
        if low_d in d.index:
            fig.add_trace(go.Scatter(
                x=[low_d], y=[d["저가"].loc[low_d] * 0.985], mode="markers+text",
                marker=dict(symbol="circle", size=9, color=DIM),
                text=["랠리 저점"], textposition="bottom center",
                textfont=dict(size=9, color=DIM), name="랠리 저점",
                hovertemplate="랠리 저점 %{x|%Y-%m-%d}<extra></extra>",
            ), row=1, col=1)
        if ftd_d in d.index:
            fig.add_trace(go.Scatter(
                x=[ftd_d], y=[d["저가"].loc[ftd_d] * 0.982], mode="markers+text",
                marker=dict(symbol="triangle-up", size=13, color=UP),
                text=[f"FTD {ftd['day_number']}일차"], textposition="bottom center",
                textfont=dict(size=10, color=UP), name="후속일(FTD)",
                hovertemplate=(f"후속일 {ftd['gain']:+.2%}"
                               f"<br>반등 {ftd['day_number']}일차<extra></extra>"),
            ), row=1, col=1)
        if low_d in d.index and ftd_d in d.index:
            fig.add_vrect(x0=low_d, x1=ftd_d, fillcolor=OK, opacity=0.07,
                          line_width=0, row=1, col=1)

    # 거래량
    vcol = "거래량" if "거래량" in d.columns else None
    if vcol:
        chg = d["종가"].pct_change()
        colors = [UP if c > 0 else DOWN for c in chg.fillna(0)]
        fig.add_trace(go.Bar(x=d.index, y=d[vcol], marker_color=colors,
                             name="거래량", showlegend=False, opacity=0.55),
                      row=2, col=1)
        fig.add_trace(go.Scatter(
            x=d.index, y=d[vcol].rolling(20, min_periods=5).mean(),
            mode="lines", line=dict(color=FAINT, width=1),
            name="20일 평균", showlegend=False), row=2, col=1)

    fig.update_layout(**LAYOUT, height=420, title=dict(text=title, font=dict(size=13)))
    fig.update_xaxes(showgrid=False, row=1, col=1)
    fig.update_xaxes(showgrid=False, row=2, col=1)
    fig.update_yaxes(gridcolor=LINE, side="right", row=1, col=1)
    fig.update_yaxes(showgrid=False, side="right", row=2, col=1)
    return fig


# ─────────────────────────────────────────────────────────────
# 2. 종목 차트 — 베이스 · 피벗 · 매수구간 (종목 탭)
# ─────────────────────────────────────────────────────────────
def stock_chart(df: pd.DataFrame, base: Dict[str, Any],
                plan: Optional[Dict] = None, days: int = 260,
                title: str = "") -> go.Figure:
    """
    베이스 구간을 음영으로 감싸고, 피벗/매수구간/손절선을 수평 밴드로 표시합니다.
    '어디서 사고 어디서 자르는가'가 한눈에 보여야 합니다.
    """
    d = df.tail(days)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.75, 0.25], vertical_spacing=0.03)

    fig.add_trace(_candles(d), row=1, col=1)
    for n, col, dash in ((50, PIVOT, None), (150, DIM, "dot"), (200, FAINT, None)):
        s = df["종가"].rolling(n, min_periods=max(2, n // 2)).mean().reindex(d.index)
        fig.add_trace(go.Scatter(x=d.index, y=s, name=f"{n}일",
                                 mode="lines", line=dict(color=col, width=1.1, dash=dash)),
                      row=1, col=1)

    # 베이스 구간 음영
    if base and base.get("found") and base.get("start"):
        try:
            b0 = pd.Timestamp(base["start"])
            if b0 >= d.index[0]:
                fig.add_vrect(x0=b0, x1=d.index[-1], fillcolor=DIM, opacity=0.06,
                              line_width=0, row=1, col=1)
                fig.add_annotation(
                    x=b0, y=1, yref="y domain", text="베이스 시작",
                    showarrow=False, font=dict(size=9, color=DIM),
                    xanchor="left", yanchor="top", row=1, col=1)
        except Exception:
            pass

    # 좌측 고점 / 저점
    if base and base.get("found"):
        for val, label, color, dash in (
            (base.get("left_high"), "좌측 고점", DIM, "dot"),
            (base.get("low"), "베이스 저점", DOWN, "dot"),
        ):
            if val:
                fig.add_hline(y=val, line=dict(color=color, width=1, dash=dash),
                              annotation_text=label,
                              annotation_font=dict(size=9, color=color),
                              annotation_position="left", row=1, col=1)

    # 매수 구간 밴드 + 피벗 + 손절
    if plan:
        pv = plan.get("피벗(매수기준가)")
        zone = plan.get("매수구간")
        stop = plan.get("손절가")
        t1 = plan.get("1차 목표")
        if pv and zone:
            fig.add_hrect(y0=zone[0], y1=zone[1], fillcolor=PIVOT, opacity=0.14,
                          line_width=0, row=1, col=1)
            fig.add_hline(y=pv, line=dict(color=PIVOT, width=1.6),
                          annotation_text=f"피벗 {pv:,.0f}",
                          annotation_font=dict(size=10, color=PIVOT),
                          annotation_position="right", row=1, col=1)
        if stop:
            fig.add_hline(y=stop, line=dict(color=DOWN, width=1.4, dash="dash"),
                          annotation_text=f"손절 {stop:,.0f}",
                          annotation_font=dict(size=10, color=DOWN),
                          annotation_position="right", row=1, col=1)
        if t1:
            fig.add_hline(y=t1, line=dict(color=UP, width=1, dash="dot"),
                          annotation_text=f"목표 {t1:,.0f}",
                          annotation_font=dict(size=9, color=UP),
                          annotation_position="right", row=1, col=1)

    # 거래량 + 50일 평균 + 돌파 거래량 강조
    chg = d["종가"].pct_change()
    v50 = df["거래량"].rolling(50, min_periods=25).mean().reindex(d.index)
    surge = d["거래량"] > v50 * 1.5
    colors = [UP if (c > 0 and s) else (f"{UP}66" if c > 0 else f"{DOWN}66")
              for c, s in zip(chg.fillna(0), surge.fillna(False))]
    fig.add_trace(go.Bar(x=d.index, y=d["거래량"], marker_color=colors,
                         showlegend=False, name="거래량"), row=2, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=v50, mode="lines",
                             line=dict(color=FAINT, width=1),
                             name="50일 평균거래량", showlegend=False), row=2, col=1)

    fig.update_layout(**LAYOUT, height=460,
                      title=dict(text=title, font=dict(size=13)))
    fig.update_yaxes(gridcolor=LINE, side="right", row=1, col=1)
    fig.update_yaxes(showgrid=False, side="right", row=2, col=1)
    return fig


# ─────────────────────────────────────────────────────────────
# 3. RS Line — 지수 대비 상대강도
# ─────────────────────────────────────────────────────────────
def rs_line_chart(close: pd.Series, bench: pd.Series, days: int = 260) -> go.Figure:
    """
    RS Line = 종가/지수. 값 자체가 아니라 '신고가 갱신 여부'가 핵심입니다.
    주가는 아직 신고가가 아닌데 RS Line이 먼저 신고가면 강력한 선행 신호입니다.
    """
    b = bench.reindex(close.index).ffill()
    rl = (close / b * 100).dropna().tail(days)
    if rl.empty:
        return go.Figure()
    peak = rl.cummax()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=rl.index, y=rl, mode="lines",
                             line=dict(color=OK, width=1.6), name="RS Line"))
    fig.add_trace(go.Scatter(x=peak.index, y=peak, mode="lines",
                             line=dict(color=FAINT, width=1, dash="dot"),
                             name="이전 최고"))
    at_high = rl[rl >= peak * 0.999]
    if len(at_high):
        fig.add_trace(go.Scatter(x=at_high.index, y=at_high, mode="markers",
                                 marker=dict(size=4, color=UP), name="신고가"))
    fig.update_layout(**LAYOUT, height=190,
                      title=dict(text="RS Line (지수 대비 상대강도)", font=dict(size=12)))
    return fig


# ─────────────────────────────────────────────────────────────
# 4. CANSLIM 7항목 막대
# ─────────────────────────────────────────────────────────────
def factor_bars(factors: Dict[str, dict]) -> go.Figure:
    keys = ["C", "A", "N", "S", "L", "I"]
    labels = {"C": "C 분기실적", "A": "A 연간실적", "N": "N 신고가",
              "S": "S 수급물량", "L": "L 주도주", "I": "I 기관수급"}
    vals, cols, txt = [], [], []
    for k in keys:
        s = (factors.get(k) or {}).get("score", -1)
        v = 0 if s is None or s < 0 else s
        vals.append(v)
        cols.append(FAINT if (s is None or s < 0) else
                    (UP if s >= 80 else PIVOT if s >= 60 else DOWN))
        txt.append("없음" if (s is None or s < 0) else f"{s:.0f}")
    fig = go.Figure(go.Bar(
        x=vals, y=[labels[k] for k in keys], orientation="h",
        marker_color=cols, text=txt, textposition="outside",
        textfont=dict(size=11, color=TEXT), showlegend=False,
    ))
    fig.update_layout(**{**LAYOUT, "hovermode": "closest"}, height=250,
                      xaxis=dict(range=[0, 118], showgrid=False,
                                 showticklabels=False, zeroline=False),
                      yaxis=dict(autorange="reversed", showgrid=False))
    fig.add_vline(x=60, line=dict(color=LINE, width=1, dash="dot"))
    fig.add_vline(x=80, line=dict(color=LINE, width=1, dash="dot"))
    return fig


# ─────────────────────────────────────────────────────────────
# 5. 분산일 근거 추출 (표로 보여줄 데이터)
# ─────────────────────────────────────────────────────────────
def distribution_evidence(idx: pd.DataFrame, cfg, window: int = 25) -> pd.DataFrame:
    """
    최근 window 거래일에서 각 날짜가 분산일인지, 왜 그런지 근거를 만듭니다.

    분산일 조건 (오닐)
      · 지수가 전일 대비 0.2% 이상 하락하고
      · 거래량이 전일보다 증가한 날 → 기관이 물량을 던진 흔적

    소멸 조건
      · 25거래일 경과, 또는
      · 이후 종가가 그날 종가보다 5% 이상 상승
    """
    m = cfg.M
    d = idx.tail(window + 3).copy()
    if len(d) < 3:
        return pd.DataFrame()
    vcol = "거래량" if "거래량" in d.columns else ("거래대금" if "거래대금" in d.columns else None)
    d["등락률"] = d["종가"].pct_change()
    d["거래량증가"] = (d[vcol] > d[vcol].shift(1)) if vcol else True
    d["거래량변화"] = (d[vcol] / d[vcol].shift(1) - 1) if vcol else np.nan
    last = float(d["종가"].iloc[-1])

    d = d.tail(window)
    d["분산일"] = (d["등락률"] <= m.distribution_drop_pct) & d["거래량증가"]
    d["소멸"] = d["분산일"] & (last >= d["종가"] * (1 + m.distribution_reset_gain))
    d["활성"] = d["분산일"] & ~d["소멸"]
    out = d[["종가", "등락률", "거래량변화", "분산일", "소멸", "활성"]].copy()
    return out


def ftd_evidence(idx: pd.DataFrame, cfg, is_kosdaq: bool = False,
                 lookback: int = 90) -> pd.DataFrame:
    """
    랠리 저점 이후 각 날의 '후속일 조건 충족 여부'를 표로 만듭니다.
    4일차 미만은 조건에 미달하고, 12일차를 넘어가면 유효성이 떨어집니다.
    """
    m = cfg.M
    d = idx.tail(lookback).copy()
    if len(d) < 10:
        return pd.DataFrame()
    vcol = "거래량" if "거래량" in d.columns else None
    lowsrc = d["저가"] if "저가" in d.columns else d["종가"]
    low_pos = int(np.argmin(lowsrc.values))
    d["등락률"] = d["종가"].pct_change()
    thr = m.ftd_gain_kosdaq if is_kosdaq else m.ftd_gain_kospi

    rows = []
    for i in range(low_pos, min(low_pos + m.ftd_max_day + 3, len(d))):
        r = d.iloc[i]
        n = i - low_pos
        volup = bool(r[vcol] > d.iloc[i - 1][vcol]) if (vcol and i > 0) else None
        gain_ok = bool(r["등락률"] >= thr) if pd.notna(r["등락률"]) else False
        day_ok = m.ftd_min_day <= n <= m.ftd_max_day
        rows.append({
            "날짜": d.index[i], "반등일차": n, "종가": r["종가"],
            "등락률": r["등락률"], "거래량증가": volup,
            "상승폭충족": gain_ok, "일차충족": day_ok,
            "FTD": bool(gain_ok and day_ok and (volup or not m.ftd_require_volume_up)),
        })
    return pd.DataFrame(rows).set_index("날짜")

"""
market_analysis.py — 시장 타임라인 분석 (차트 표시용)

market.py가 "오늘 시장이 어떤 상태인가"만 답한다면,
이 모듈은 "어떻게 그 상태가 됐는가"를 날짜별로 풀어냅니다.

만들어내는 것
  · 분산일 전체 목록 (날짜, 하락률, 거래량 증가율, 소멸 여부)
  · 랠리 저점 → 후속일(FTD) 후보 → 확정/실패 이력
  · 각 판정의 근거 문장 (오닐 원본 규칙을 그대로 인용)
  · 차트에 찍을 마커 좌표

오닐 원본 규칙 (Investor's Business Daily 기준)
  분산일 : 지수가 전일 대비 0.2% 이상 하락 + 거래량은 전일보다 증가
           → 기관이 물량을 던진 날. 25거래일 창에서 카운트.
           소멸: 25거래일 경과 또는 이후 종가가 그날 종가 대비 5% 상승
  후속일 : 랠리 저점 이후 반등 4~12일차에
           지수가 큰 폭(+1.0~1.7%) 상승 + 거래량이 전일보다 증가
           → 새 상승장이 시작됐을 "수 있다"는 신호. 보증이 아님.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any

import numpy as np
import pandas as pd

from .indicators import sma
from .config import CanslimKRConfig, DEFAULT


@dataclass
class DistDay:
    date: str
    close: float
    change: float          # 하락률
    volume_ratio: float    # 전일 대비 거래량 배수
    active: bool           # 아직 유효한가 (소멸 안 됐는가)
    expired_reason: str = ""


@dataclass
class FTDEvent:
    ftd_date: str
    rally_low_date: str
    rally_low: float
    day_number: int        # 반등 몇 일차인가
    gain: float
    volume_ratio: float
    confirmed: bool        # 이후 랠리 저점을 깨지 않고 유지됐는가
    outcome: str           # 성공 / 실패 / 진행중
    fwd_return_20d: Optional[float] = None
    fwd_return_60d: Optional[float] = None


@dataclass
class MarketTimeline:
    index_name: str
    as_of: str
    state: str
    state_label: str
    exposure: float
    close: float
    change_1d: float
    pct_from_high: float
    ma50: Optional[float]
    ma200: Optional[float]
    above_ma50: bool
    above_ma200: bool
    ma50_over_200: bool
    distribution_days: List[DistDay] = field(default_factory=list)
    active_dd_count: int = 0
    ftd_events: List[FTDEvent] = field(default_factory=list)
    latest_ftd: Optional[FTDEvent] = None
    reasons: List[str] = field(default_factory=list)     # 판정 근거
    actions: List[str] = field(default_factory=list)     # 지금 할 일

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


STATE_LABEL = {
    "CONFIRMED_UPTREND": "확인된 상승 추세",
    "UPTREND_UNDER_PRESSURE": "상승 추세 · 압박",
    "RALLY_ATTEMPT": "반등 시도 중",
    "MARKET_IN_CORRECTION": "시장 조정",
}


# ─────────────────────────────────────────────────────────────
def find_distribution_days(idx: pd.DataFrame, cfg: CanslimKRConfig,
                           window: Optional[int] = None) -> List[DistDay]:
    """
    분산일 전체 목록. 소멸된 것도 이유와 함께 남깁니다.
    (차트에서 회색으로 표시해 "예전엔 분산일이었으나 지워졌다"를 보여주기 위함)
    """
    m = cfg.M
    w = window or m.distribution_window
    d = idx.tail(w + 40).copy()
    if len(d) < 5:
        return []

    vcol = "거래량" if "거래량" in d.columns else ("거래대금" if "거래대금" in d.columns else None)
    d["chg"] = d["종가"].pct_change()
    d["vratio"] = (d[vcol] / d[vcol].shift(1)) if vcol else 1.0

    last_close = float(d["종가"].iloc[-1])
    recent = d.tail(w)
    out: List[DistDay] = []
    for dt, r in recent.iterrows():
        if pd.isna(r["chg"]):
            continue
        vol_up = bool(r["vratio"] > 1.0) if vcol else True
        if r["chg"] > m.distribution_drop_pct or not vol_up:
            continue
        active, why = True, ""
        if last_close >= float(r["종가"]) * (1 + m.distribution_reset_gain):
            active, why = False, f"이후 지수가 {m.distribution_reset_gain:.0%} 이상 상승해 소멸"
        out.append(DistDay(
            date=str(pd.Timestamp(dt).date()),
            close=round(float(r["종가"]), 2),
            change=round(float(r["chg"]), 4),
            volume_ratio=round(float(r["vratio"]), 2) if vcol else 1.0,
            active=active, expired_reason=why,
        ))
    return out


def find_ftd_events(idx: pd.DataFrame, cfg: CanslimKRConfig,
                    is_kosdaq: bool = False, lookback_days: int = 750,
                    max_events: int = 8) -> List[FTDEvent]:
    """
    과거 후속일(FTD) 이력을 모두 찾아 성패까지 평가합니다.

    FTD 하나만 보면 "지금이 바닥인가"를 알 수 없습니다.
    과거 FTD가 몇 번 나왔고 그중 몇 번이 실제로 상승장으로 이어졌는지를
    같이 봐야 이번 신호의 신뢰도를 가늠할 수 있습니다.
    """
    m = cfg.M
    d = idx.tail(lookback_days).copy()
    if len(d) < 60:
        return []

    vcol = "거래량" if "거래량" in d.columns else ("거래대금" if "거래대금" in d.columns else None)
    lcol = "저가" if "저가" in d.columns else "종가"
    threshold = m.ftd_gain_kosdaq if is_kosdaq else m.ftd_gain_kospi
    d["chg"] = d["종가"].pct_change()

    # 조정 국면(고점 대비 -7% 이상 하락)에서 형성된 저점만 랠리 저점으로 봅니다.
    roll_max = d["종가"].cummax()
    in_corr = (d["종가"] / roll_max - 1) <= -0.07

    events: List[FTDEvent] = []
    i = 20
    while i < len(d) - 1:
        if not in_corr.iloc[i]:
            i += 1
            continue
        # 국소 저점 탐색 (좌우 5일 최저)
        lo_win = d[lcol].iloc[max(0, i - 5):i + 6]
        if len(lo_win) < 6 or d[lcol].iloc[i] != lo_win.min():
            i += 1
            continue
        low_pos, low_val = i, float(d[lcol].iloc[i])

        found = None
        for j in range(low_pos + m.ftd_min_day, min(low_pos + m.ftd_max_day + 1, len(d))):
            r = d.iloc[j]
            if pd.isna(r["chg"]):
                continue
            vr = float(r[vcol] / d.iloc[j - 1][vcol]) if vcol else 1.0
            if r["chg"] >= threshold and (not m.ftd_require_volume_up or vr > 1.0):
                found = (j, float(r["chg"]), vr)
                break

        if found:
            j, gain, vr = found
            # 성패 평가: 이후 20일 내 랠리 저점을 깨면 실패
            fwd = d.iloc[j + 1:j + 21]
            broke = bool(len(fwd) and fwd[lcol].min() < low_val)
            r20 = (float(d["종가"].iloc[min(j + 20, len(d) - 1)] / d["종가"].iloc[j] - 1)
                   if j + 5 < len(d) else None)
            r60 = (float(d["종가"].iloc[min(j + 60, len(d) - 1)] / d["종가"].iloc[j] - 1)
                   if j + 20 < len(d) else None)
            if j + 20 >= len(d):
                outcome = "진행중"
            elif broke:
                outcome = "실패 (랠리 저점 이탈)"
            elif r20 is not None and r20 > 0:
                outcome = "성공"
            else:
                outcome = "실패 (상승 미지속)"

            events.append(FTDEvent(
                ftd_date=str(pd.Timestamp(d.index[j]).date()),
                rally_low_date=str(pd.Timestamp(d.index[low_pos]).date()),
                rally_low=round(low_val, 2),
                day_number=j - low_pos, gain=round(gain, 4),
                volume_ratio=round(vr, 2), confirmed=not broke, outcome=outcome,
                fwd_return_20d=None if r20 is None else round(r20, 4),
                fwd_return_60d=None if r60 is None else round(r60, 4),
            ))
            i = j + 20
        else:
            i += 10

    return events[-max_events:]


# ─────────────────────────────────────────────────────────────
def analyze_market(idx: pd.DataFrame, cfg: CanslimKRConfig = DEFAULT,
                   index_name: str = "KOSPI") -> MarketTimeline:
    """지수 하나에 대한 완전 분석 + 사람이 읽을 근거."""
    is_kosdaq = "KOSDAQ" in index_name.upper()
    c = idx["종가"]
    last = float(c.iloc[-1])
    chg1 = float(c.iloc[-1] / c.iloc[-2] - 1) if len(c) > 1 else 0.0

    dds = find_distribution_days(idx, cfg)
    active_dd = [x for x in dds if x.active]
    n_dd = len(active_dd)
    ftds = find_ftd_events(idx, cfg, is_kosdaq)
    latest = ftds[-1] if ftds else None

    ma50s, ma200s = sma(c, 50), sma(c, 200)
    ma50 = None if ma50s.dropna().empty else float(ma50s.iloc[-1])
    ma200 = None if ma200s.dropna().empty else float(ma200s.iloc[-1])
    a50 = bool(ma50 and last > ma50)
    a200 = bool(ma200 and last > ma200)
    stack = bool(ma50 and ma200 and ma50 > ma200)
    hi252 = float(c.tail(252).max())
    from_high = float(last / hi252 - 1) if hi252 > 0 else 0.0

    # ── 상태 판정 ──
    reasons, actions = [], []
    if not a50 and from_high < -0.08:
        state = "MARKET_IN_CORRECTION"
        reasons.append(f"지수가 50일선 아래이고 52주 고점 대비 {from_high:.1%}입니다. "
                       "오닐은 이 국면을 '조정'으로 규정하고 신규 매수를 중단합니다.")
        if latest and latest.outcome == "진행중":
            state = "RALLY_ATTEMPT"
            reasons.append(
                f"{latest.rally_low_date}에 랠리 저점({latest.rally_low:,.0f})을 찍고 "
                f"{latest.day_number}일차인 {latest.ftd_date}에 후속일이 나왔습니다 "
                f"({latest.gain:+.2%}, 거래량 전일 대비 {latest.volume_ratio:.2f}배). "
                "새 상승장의 첫 신호일 수 있으나 확정은 아닙니다.")
    elif n_dd >= cfg.M.dd_correction_threshold:
        state = "MARKET_IN_CORRECTION"
        reasons.append(f"최근 25거래일에 분산일이 {n_dd}개 쌓였습니다. "
                       f"{cfg.M.dd_correction_threshold}개 이상은 기관 매도가 "
                       "누적됐다는 뜻이라 오닐 기준으로 조정 국면입니다.")
    elif n_dd >= cfg.M.dd_pressure_threshold:
        state = "UPTREND_UNDER_PRESSURE"
        reasons.append(f"분산일 {n_dd}개. 추세는 살아 있지만 기관이 물량을 내놓고 있어 "
                       "'압박' 상태입니다. 신규 매수를 줄이고 기존 보유분의 손절선을 점검할 시점입니다.")
    else:
        state = "CONFIRMED_UPTREND"
        reasons.append(f"분산일이 {n_dd}개뿐이고 지수가 주요 이평선 위에 있습니다. "
                       "오닐 기준 '확인된 상승 추세' — 돌파 매수의 성공률이 가장 높은 구간입니다.")

    # 이평 근거
    if a50 and a200 and stack:
        reasons.append(f"50일선({ma50:,.0f})·200일선({ma200:,.0f}) 모두 위이고 정배열입니다. "
                       "중장기 추세가 훼손되지 않았습니다.")
    else:
        if not a50 and ma50:
            reasons.append(f"지수가 50일선({ma50:,.0f})을 이탈했습니다. 단기 추세 훼손 신호입니다.")
        if not a200 and ma200:
            reasons.append(f"지수가 200일선({ma200:,.0f}) 아래입니다. 장기 추세가 꺾인 상태로, "
                           "오닐은 이 구간에서 대부분의 돌파가 실패한다고 봤습니다.")
        if ma50 and ma200 and not stack:
            reasons.append("50일선이 200일선 아래(역배열)입니다.")

    # 분산일 근거
    if active_dd:
        recent3 = ", ".join(f"{x.date}({x.change:.1%})" for x in active_dd[-3:])
        reasons.append(f"유효 분산일 최근 3건: {recent3}. "
                       "각 분산일은 25거래일이 지나거나 지수가 그날 종가보다 5% 오르면 소멸합니다.")

    # FTD 이력 근거
    if ftds:
        done = [e for e in ftds if e.outcome != "진행중"]
        if done:
            succ = sum(1 for e in done if e.outcome == "성공")
            reasons.append(f"이 지수의 과거 후속일 {len(done)}건 중 {succ}건이 실제 상승으로 "
                           f"이어졌습니다(적중률 {succ/len(done):.0%}). "
                           "후속일은 확률을 높이는 신호이지 보장이 아닙니다.")

    exposure = cfg.M.exposure_by_state.get(state, 0.0)

    # ── 지금 할 일 ──
    if state == "CONFIRMED_UPTREND":
        actions = ["신규 돌파 매수 가능. 종목당 비중을 규칙대로 채웁니다.",
                   "총 투자비중 100%까지 허용됩니다.",
                   "분산일이 4개로 늘면 즉시 신규 매수를 줄이세요."]
    elif state == "UPTREND_UNDER_PRESSURE":
        actions = ["신규 매수는 최고 등급 종목만, 비중은 절반으로.",
                   "총 투자비중 50% 이내로 관리합니다.",
                   "손실 중인 종목부터 정리해 현금을 확보하세요."]
    elif state == "RALLY_ATTEMPT":
        actions = ["시험 매수만. 총 투자비중 25% 이내.",
                   f"랠리 저점({latest.rally_low:,.0f} / {latest.rally_low_date})이 깨지면 "
                   "후속일은 무효입니다. 즉시 현금화하세요." if latest else "랠리 저점 이탈 시 무효.",
                   "이 구간의 돌파는 실패율이 높습니다. 손절을 더 타이트하게."]
    else:
        actions = ["신규 매수 금지. 오닐 규칙상 예외 없습니다.",
                   "보유 종목의 손절선을 점검하고, 규칙 위반 종목은 정리합니다.",
                   "관심목록을 만들며 다음 후속일을 기다리세요. 조정장의 할 일은 준비입니다."]

    return MarketTimeline(
        index_name=index_name, as_of=str(pd.Timestamp(c.index[-1]).date()),
        state=state, state_label=STATE_LABEL.get(state, state), exposure=exposure,
        close=round(last, 2), change_1d=round(chg1, 4),
        pct_from_high=round(from_high, 4),
        ma50=None if ma50 is None else round(ma50, 2),
        ma200=None if ma200 is None else round(ma200, 2),
        above_ma50=a50, above_ma200=a200, ma50_over_200=stack,
        distribution_days=dds, active_dd_count=n_dd,
        ftd_events=ftds, latest_ftd=latest,
        reasons=reasons, actions=actions,
    )

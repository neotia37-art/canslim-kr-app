"""
market.py — M 지표: 시장 방향 판정

오닐 시스템에서 M은 점수가 아니라 '스위치'입니다.
시장이 조정 국면이면 나머지 CANSLIM이 아무리 좋아도 신규 매수를 하지 않습니다.
오닐 본인이 "돌파 매수의 4건 중 3건은 시장이 조정일 때 실패한다"고 했습니다.

판정 요소
  1) 분산일(Distribution Day) — 지수 하락 + 거래량 증가. 기관 매도의 흔적.
  2) 후속일(Follow-Through Day) — 조정 후 반등 4~12일차의 대량 상승. 새 상승장의 시작.
  3) 지수의 50일/200일 이동평균 위치

KOSPI는 미국 지수보다 일간 변동성이 낮아 FTD 기준을 +1.0%로,
KOSDAQ은 변동성이 높아 +1.4%로 분리 적용합니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional

import numpy as np
import pandas as pd

from .indicators import sma


@dataclass
class MarketState:
    index_name: str
    state: str                      # CONFIRMED_UPTREND / UPTREND_UNDER_PRESSURE /
                                    # MARKET_IN_CORRECTION / RALLY_ATTEMPT
    distribution_days: int
    distribution_dates: List[str] = field(default_factory=list)
    ftd_date: Optional[str] = None
    ftd_gain: Optional[float] = None
    days_since_ftd: Optional[int] = None
    above_ma50: Optional[bool] = None
    above_ma200: Optional[bool] = None
    ma50_above_ma200: Optional[bool] = None
    pct_from_high: Optional[float] = None
    max_exposure: float = 0.0
    gate_multiplier: float = 1.0
    commentary: List[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def count_distribution_days(idx: pd.DataFrame, cfg) -> Dict:
    """
    분산일 = 지수가 전일 대비 0.2% 이상 하락했는데 거래량은 전일보다 늘어난 날.
    기관이 물량을 던졌다는 신호입니다.

    소멸 규칙 두 가지:
      (a) 25거래일 경과
      (b) 이후 지수가 해당일 종가보다 5% 이상 상승
    """
    m = cfg.M
    d = idx.tail(m.distribution_window + 5).copy()
    if len(d) < 5:
        return {"count": 0, "dates": []}

    col_v = "거래량" if "거래량" in d.columns else ("거래대금" if "거래대금" in d.columns else None)
    d["chg"] = d["종가"].pct_change()
    if col_v:
        d["vol_up"] = d[col_v] > d[col_v].shift(1)
    else:
        d["vol_up"] = True   # 거래량 데이터가 없으면 하락일만으로 근사

    last_close = float(d["종가"].iloc[-1])
    hits = []
    window = d.tail(m.distribution_window)
    for dt, row in window.iterrows():
        if row["chg"] is None or pd.isna(row["chg"]):
            continue
        if row["chg"] <= m.distribution_drop_pct and row["vol_up"]:
            # 소멸 규칙 (b)
            if last_close >= float(row["종가"]) * (1 + m.distribution_reset_gain):
                continue
            hits.append(str(pd.Timestamp(dt).date()))
    return {"count": len(hits), "dates": hits}


def find_follow_through_day(idx: pd.DataFrame, cfg, is_kosdaq: bool = False,
                            lookback: int = 90) -> Optional[Dict]:
    """
    후속일(FTD) 탐지.

    절차
      1) 최근 lookback 구간에서 저점(rally low)을 찾는다
      2) 저점 다음 날부터 반등 일수를 센다
      3) 4~12일차 중 지수가 기준치 이상 상승 + 거래량이 전일보다 증가한 날 = FTD

    FTD는 '새 상승장이 시작됐을 수 있다'는 신호이지 보증이 아닙니다.
    오닐도 FTD의 상당수가 실패한다고 했습니다. 그래서 확인 후 분할 진입합니다.
    """
    m = cfg.M
    d = idx.tail(lookback).copy()
    if len(d) < 20:
        return None

    col_v = "거래량" if "거래량" in d.columns else ("거래대금" if "거래대금" in d.columns else None)
    threshold = m.ftd_gain_kosdaq if is_kosdaq else m.ftd_gain_kospi

    low_pos = int(np.argmin(d["저가"].values if "저가" in d.columns else d["종가"].values))
    d["chg"] = d["종가"].pct_change()

    for i in range(low_pos + m.ftd_min_day, min(low_pos + m.ftd_max_day + 1, len(d))):
        row = d.iloc[i]
        if pd.isna(row["chg"]):
            continue
        vol_ok = True
        if m.ftd_require_volume_up and col_v:
            vol_ok = bool(row[col_v] > d.iloc[i - 1][col_v])
        if row["chg"] >= threshold and vol_ok:
            return {
                "date": str(pd.Timestamp(d.index[i]).date()),
                "day_number": i - low_pos,
                "gain": round(float(row["chg"]), 4),
                "rally_low_date": str(pd.Timestamp(d.index[low_pos]).date()),
                "days_since": len(d) - 1 - i,
            }
    return None


def assess_market(idx: pd.DataFrame, cfg, index_name: str = "KOSPI") -> MarketState:
    """지수 하나에 대한 종합 시장 판정."""
    m = cfg.M
    is_kosdaq = "KOSDAQ" in index_name.upper()

    dd = count_distribution_days(idx, cfg)
    ftd = find_follow_through_day(idx, cfg, is_kosdaq=is_kosdaq)

    c = idx["종가"]
    ma50 = sma(c, m.index_ma_short)
    ma200 = sma(c, m.index_ma_long)
    last = float(c.iloc[-1])
    above50 = None if ma50.dropna().empty else bool(last > ma50.iloc[-1])
    above200 = None if ma200.dropna().empty else bool(last > ma200.iloc[-1])
    stack = None
    if not ma50.dropna().empty and not ma200.dropna().empty:
        stack = bool(ma50.iloc[-1] > ma200.iloc[-1])

    hi_252 = float(c.tail(252).max())
    pct_from_high = float(last / hi_252 - 1.0) if hi_252 > 0 else None

    notes = []
    n_dd = dd["count"]

    # ── 상태 결정 ──
    if above50 is False and (pct_from_high is not None and pct_from_high < -0.08):
        state = "MARKET_IN_CORRECTION"
        notes.append(f"지수가 50일선 아래이고 고점 대비 {pct_from_high:.1%} — 조정 국면")
        if ftd and ftd["days_since"] <= 15:
            state = "RALLY_ATTEMPT"
            notes.append(f"{ftd['date']} 후속일 발생({ftd['gain']:+.2%}, "
                         f"반등 {ftd['day_number']}일차) — 반등 시도 확인 중")
    elif n_dd >= m.dd_correction_threshold:
        state = "MARKET_IN_CORRECTION"
        notes.append(f"분산일 {n_dd}개 누적 — 기관 매도 압력이 임계치를 넘음")
    elif n_dd >= m.dd_pressure_threshold:
        state = "UPTREND_UNDER_PRESSURE"
        notes.append(f"분산일 {n_dd}개 — 상승 추세이나 압박 상태. 신규 매수 축소")
    else:
        state = "CONFIRMED_UPTREND"
        notes.append(f"분산일 {n_dd}개 — 확인된 상승 추세")

    if stack is False:
        notes.append("50일선이 200일선 아래 — 중기 추세 훼손")
    if above200 is False:
        notes.append("지수가 200일선 아래 — 장기 추세 이탈")

    exposure = m.exposure_by_state.get(state, 0.0)
    gate = cfg.scoring.market_gate.get(state, 0.5)

    return MarketState(
        index_name=index_name,
        state=state,
        distribution_days=n_dd,
        distribution_dates=dd["dates"],
        ftd_date=ftd["date"] if ftd else None,
        ftd_gain=ftd["gain"] if ftd else None,
        days_since_ftd=ftd["days_since"] if ftd else None,
        above_ma50=above50, above_ma200=above200, ma50_above_ma200=stack,
        pct_from_high=None if pct_from_high is None else round(pct_from_high, 4),
        max_exposure=exposure,
        gate_multiplier=gate,
        commentary=notes,
    )


def assess_both_markets(hub, cfg, start: str = None) -> Dict[str, MarketState]:
    """
    KOSPI와 KOSDAQ을 각각 판정합니다.
    종목이 속한 시장의 판정을 그 종목에 적용하는 것이 맞습니다 —
    코스닥이 조정인데 코스피는 멀쩡한 국면이 한국에서는 자주 발생합니다.
    """
    from .datahub import IDX_KOSPI, IDX_KOSDAQ, last_business_day
    if start is None:
        start = (pd.Timestamp(last_business_day()) - pd.Timedelta(days=500)).strftime("%Y%m%d")
    out = {}
    for name, code in (("KOSPI", IDX_KOSPI), ("KOSDAQ", IDX_KOSDAQ)):
        try:
            idx = hub.index_ohlcv(code, start)
            if idx is not None and not idx.empty:
                out[name] = assess_market(idx, cfg, index_name=name)
        except Exception as e:
            print(f"[market] {name} 조회 실패: {e}")
    return out

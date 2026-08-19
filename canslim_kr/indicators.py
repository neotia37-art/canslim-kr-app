"""
indicators.py — 기술적 지표 & 오닐 베이스 패턴 탐지

핵심 구성
  1. RS Rating (IBD 산식)  : 0.4·ROC(63) + 0.2·ROC(126) + 0.2·ROC(189) + 0.2·ROC(252)
                             → 유니버스 전체 백분위 랭크 1~99
  2. RS Line               : 종가 / 벤치마크지수  (신고가 여부가 주도주 판별의 핵심)
  3. 매집/분산 지표         : 상승일 거래량 vs 하락일 거래량
  4. 베이스 탐지            : 컵앤핸들 / 이중바닥 / 평평한 베이스 / 상승 삼각형
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Tuple

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────
# 기본 지표
# ─────────────────────────────────────────────────────────────
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=max(2, n // 2)).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["고가"], df["저가"], df["종가"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n // 2).mean()


def roc(s: pd.Series, n: int) -> Optional[float]:
    """n거래일 전 대비 수익률. 데이터가 부족하면 None."""
    if len(s) <= n:
        return None
    base = s.iloc[-(n + 1)]
    if base is None or base <= 0 or pd.isna(base):
        return None
    return float(s.iloc[-1] / base - 1.0)


# ─────────────────────────────────────────────────────────────
# RS Rating (IBD 산식)
# ─────────────────────────────────────────────────────────────
def rs_raw(close: pd.Series,
           periods: List[int] = (63, 126, 189, 252),
           weights: List[float] = (0.4, 0.2, 0.2, 0.2)) -> Optional[float]:
    """
    IBD Relative Strength 원점수.
    최근 분기(63일)에 2배 가중을 주는 것이 이 산식의 핵심입니다.
    """
    vals, wts = [], []
    for p, w in zip(periods, weights):
        r = roc(close, p)
        if r is not None:
            vals.append(r)
            wts.append(w)
    if not vals or sum(wts) < 0.5:   # 절반 이상의 기간이 확보돼야 유효
        return None
    return float(np.dot(vals, wts) / sum(wts))


def rs_rating_table(close_matrix: pd.DataFrame,
                    periods=(63, 126, 189, 252),
                    weights=(0.4, 0.2, 0.2, 0.2)) -> pd.DataFrame:
    """
    유니버스 전체의 RS 원점수 → 백분위 랭크(1~99).
    오닐 기준으로 80 이상만 매수 후보, 90 이상이 신규 주도주 영역입니다.

    close_matrix: 행=날짜, 열=종목코드인 종가 행렬
    """
    raws = {}
    for code in close_matrix.columns:
        s = close_matrix[code].dropna()
        r = rs_raw(s, periods, weights)
        if r is not None:
            raws[code] = r
    if not raws:
        return pd.DataFrame(columns=["rs_raw", "rs_rating"])
    ser = pd.Series(raws, name="rs_raw")
    pct = ser.rank(pct=True)
    rating = (pct * 98 + 1).round().astype(int).clip(1, 99)
    return pd.DataFrame({"rs_raw": ser, "rs_rating": rating}).sort_values(
        "rs_rating", ascending=False)


def rs_line(close: pd.Series, bench_close: pd.Series) -> pd.Series:
    """RS Line = 종가 / 벤치마크. 값 자체보다 '신고가 갱신 여부'가 중요합니다."""
    b = bench_close.reindex(close.index).ffill()
    return (close / b) * 100.0


def rs_line_new_high(close: pd.Series, bench: pd.Series, lookback: int = 60) -> bool:
    """
    RS Line 신고가 — 오닐이 가장 신뢰한 선행 신호.
    주가가 아직 신고가가 아닌데 RS Line이 먼저 신고가면 강한 매수 후보입니다.
    """
    rl = rs_line(close, bench).dropna()
    if len(rl) < lookback + 5:
        return False
    return bool(rl.iloc[-1] >= rl.iloc[-lookback:].max() * 0.999)


# ─────────────────────────────────────────────────────────────
# 거래량 / 매집 지표
# ─────────────────────────────────────────────────────────────
def up_down_volume_ratio(df: pd.DataFrame, n: int = 50) -> Optional[float]:
    """
    상승일 거래량 합 / 하락일 거래량 합.
    1.0 초과면 매집(accumulation), 미만이면 분산(distribution) 우위.
    """
    d = df.tail(n)
    if len(d) < n // 2:
        return None
    chg = d["종가"].diff()
    up = d.loc[chg > 0, "거래량"].sum()
    dn = d.loc[chg < 0, "거래량"].sum()
    if dn <= 0:
        return None
    return float(up / dn)


def volume_surge(df: pd.DataFrame, base_n: int = 50) -> Optional[float]:
    """당일 거래량 / 최근 base_n일 평균 거래량."""
    if len(df) < base_n + 1:
        return None
    avg = df["거래량"].iloc[-(base_n + 1):-1].mean()
    if avg <= 0:
        return None
    return float(df["거래량"].iloc[-1] / avg)


def tightness(close: pd.Series, weeks: int = 5) -> Optional[float]:
    """
    주간 종가 변동성. 오닐이 말한 '타이트한 구간(tight closes)'.
    베이스 후반부가 타이트할수록 돌파 성공률이 높습니다.
    """
    w = close.resample("W").last().dropna().tail(weeks)
    if len(w) < 3 or w.mean() <= 0:
        return None
    return float(w.std() / w.mean())


# ─────────────────────────────────────────────────────────────
# 베이스(Base) 패턴 탐지
# ─────────────────────────────────────────────────────────────
@dataclass
class BaseResult:
    found: bool
    pattern: str = "NONE"          # CUP_HANDLE / DOUBLE_BOTTOM / FLAT_BASE / ASCENDING / NONE
    start: Optional[str] = None
    end: Optional[str] = None
    weeks: Optional[float] = None
    depth: Optional[float] = None          # 베이스 깊이 (좌측고점→저점)
    left_high: Optional[float] = None
    low: Optional[float] = None
    pivot: Optional[float] = None          # 매수 기준가
    handle_depth: Optional[float] = None
    prior_uptrend: Optional[float] = None  # 베이스 직전 상승폭
    tightness: Optional[float] = None
    stage: int = 1                         # 베이스 차수 (1~2차 성공률 높음)
    status: str = ""                       # BUILDING / HANDLE / BREAKOUT / EXTENDED / FAILED
    notes: List[str] = None

    def to_dict(self):
        d = asdict(self)
        d["notes"] = self.notes or []
        return d


def _swing_points(s: pd.Series, order: int = 5) -> Tuple[np.ndarray, np.ndarray]:
    """
    국소 고점/저점 인덱스.
    왼쪽은 '이상/이하', 오른쪽은 '초과/미만'으로 비대칭 판정합니다.
    실제 차트에는 같은 값이 연속되는 평평한 고점이 흔한데,
    좌우 모두 엄격히 비교하면 그런 고점을 통째로 놓칩니다.
    """
    v = np.asarray(s.values, dtype=float)
    n = len(v)
    highs, lows = [], []
    for i in range(order, n - order):
        left, right = v[i - order:i], v[i + 1:i + order + 1]
        if v[i] >= left.max() and v[i] > right.max():
            highs.append(i)
        if v[i] <= left.min() and v[i] < right.min():
            lows.append(i)
    return np.array(highs, dtype=int), np.array(lows, dtype=int)


def detect_base(df: pd.DataFrame, cfg) -> BaseResult:
    """
    오닐 베이스 자동 탐지.

    로직
      1) 최근 구간에서 좌측 고점(left high) 후보를 찾는다
      2) 좌측 고점 이후의 최저점을 찾아 깊이/기간을 검증한다
      3) 저점 이후 반등에서 손잡이(handle) 형성 여부를 본다
      4) 손잡이 고점(또는 좌측 고점) = 피벗(매수 기준가)
      5) 베이스 직전에 충분한 상승(prior uptrend)이 있었는지 확인한다

    주의: 자동 탐지는 후보를 좁히는 용도입니다.
          최종 판단은 반드시 주봉 차트를 눈으로 확인하세요.
    """
    notes = []
    b = cfg.base
    if df is None or len(df) < 120:
        return BaseResult(False, notes=["데이터 부족(120일 미만)"])

    close = df["종가"]
    high, low = df["고가"], df["저가"]
    max_bars = int(b.max_weeks * 5)
    win = df.tail(min(max_bars + 40, len(df)))
    c, h, l = win["종가"], win["고가"], win["저가"]
    idx = win.index

    hi_i, lo_i = _swing_points(c, order=5)
    if len(hi_i) == 0 or len(lo_i) == 0:
        return BaseResult(False, notes=["스윙 포인트 없음 — 추세가 불분명"])

    best: Optional[BaseResult] = None

    for lh in reversed(hi_i):                       # 최근 고점부터 역순 탐색
        after_lows = lo_i[lo_i > lh]
        if len(after_lows) == 0:
            continue
        seg_end = len(c) - 1
        bars = seg_end - lh
        if bars < b.min_weeks * 5 or bars > max_bars:
            continue

        left_high = float(h.iloc[lh])
        trough_pos = int(np.argmin(l.iloc[lh:seg_end + 1].values)) + lh
        base_low = float(l.iloc[trough_pos])
        if left_high <= 0:
            continue
        depth = (left_high - base_low) / left_high

        # 깊이 검증 — 패턴 유형 결정
        if depth <= b.flat_base_depth_max:
            pattern = "FLAT_BASE"
        elif b.cup_depth_min <= depth <= b.cup_depth_max:
            pattern = "CUP_HANDLE"
        else:
            notes.append(f"깊이 {depth:.1%} — 허용범위 밖")
            continue

        # 이중바닥 판정: 저점 이후 반등했다가 다시 첫 저점 근처(또는 이하)로 하락
        post = l.iloc[trough_pos:seg_end + 1]
        second_low = None
        if len(post) > 15:
            rebound_pos = int(np.argmax(c.iloc[trough_pos:seg_end + 1].values)) + trough_pos
            if rebound_pos < seg_end - 5:
                tail_low = float(l.iloc[rebound_pos:seg_end + 1].min())
                mid_high = float(c.iloc[rebound_pos])
                if (mid_high - base_low) / max(base_low, 1e-9) > 0.05 and \
                   tail_low <= base_low * 1.03:
                    pattern = "DOUBLE_BOTTOM"
                    second_low = tail_low

        # 손잡이(handle) 탐지 — 저점 이후 우측 고점에서 형성되는 얕은 조정
        #
        # 주의: 저점 이후 구간의 단순 최고가를 쓰면 이미 돌파가 난 종목에서
        # '돌파봉' 자체가 최고가로 잡혀 손잡이를 놓칩니다.
        # 그래서 스윙 고점 중 뒤에 최소 handle_min_days 이상 남아 있는 것을 씁니다.
        handle_depth = None
        pivot = left_high
        post_hi, _ = _swing_points(c.iloc[trough_pos:seg_end + 1], order=4)
        cands = [int(p) + trough_pos for p in post_hi
                 if seg_end - (int(p) + trough_pos) >= b.handle_min_days]
        if cands:
            rp = max(cands)                       # 가장 최근의 유효 스윙 고점
            handle_high = float(h.iloc[rp])
            handle_low = float(l.iloc[rp:seg_end + 1].min())
            hd = (handle_high - handle_low) / max(handle_high, 1e-9)
            upper_half_ok = (not b.handle_must_be_upper_half) or \
                            (handle_low >= base_low + (left_high - base_low) * 0.45)
            if hd <= b.handle_max_depth and upper_half_ok and handle_high >= left_high * 0.85:
                handle_depth = hd
                pivot = max(handle_high, left_high * 0.98)
                if pattern == "FLAT_BASE" and hd > 0.03:
                    pattern = "CUP_HANDLE"
            elif hd > b.handle_max_depth:
                notes.append(f"손잡이 조정폭 {hd:.1%} 과다 — 좌측 고점을 피벗으로 사용")

        # 베이스 직전 상승폭 검증 — 이게 없으면 그냥 하락 후 횡보일 뿐
        pre_start = max(0, lh - b.prior_uptrend_lookback)
        pre_low = float(l.iloc[pre_start:lh + 1].min()) if lh > pre_start else np.nan
        prior_up = (left_high - pre_low) / max(pre_low, 1e-9) if pre_low == pre_low else None
        if prior_up is not None and prior_up < b.prior_uptrend_min:
            notes.append(f"사전 상승 {prior_up:.1%} 부족")
            continue

        pivot = pivot * (1 + cfg.N.pivot_buffer)
        last = float(c.iloc[-1])
        if last > pivot * (1 + cfg.N.extended_pct):
            status = "EXTENDED"
        elif last >= pivot:
            status = "BREAKOUT"
        elif handle_depth is not None:
            status = "HANDLE"
        else:
            status = "BUILDING"

        tg = tightness(c.tail(30))
        res = BaseResult(
            found=True, pattern=pattern,
            start=str(idx[lh].date()), end=str(idx[-1].date()),
            weeks=round(bars / 5, 1), depth=round(depth, 4),
            left_high=round(left_high, 2), low=round(base_low, 2),
            pivot=round(pivot, 2),
            handle_depth=None if handle_depth is None else round(handle_depth, 4),
            prior_uptrend=None if prior_up is None else round(prior_up, 4),
            tightness=None if tg is None else round(tg, 4),
            status=status, notes=list(notes),
        )
        if second_low:
            res.notes.append(f"두 번째 저점 {second_low:,.0f} — 이중바닥 구조")
        best = res
        break

    if best is None:
        return BaseResult(False, notes=notes or ["유효 베이스 미검출"])

    # 품질 코멘트
    if best.tightness is not None and best.tightness <= cfg.base.tightness_max:
        best.notes.append("베이스 후반 종가 타이트 — 돌파 신뢰도 상승")
    if best.handle_depth is None and best.pattern == "CUP_HANDLE":
        best.notes.append("손잡이 미형성 — 좌측 고점을 임시 피벗으로 사용")
    if best.depth and best.depth > 0.33:
        best.notes.append("깊이 33% 초과 — 오닐 원본 기준으로는 과도한 베이스")
    return best


def base_stage(close: pd.Series, cfg, lookback_days: int = 500) -> int:
    """
    베이스 차수(stage) 추정.
    1~2차 베이스의 돌파 성공률이 높고, 4차 이상은 실패율이 급증합니다.
    직전 신고가 돌파 횟수로 근사합니다.
    """
    s = close.tail(lookback_days).dropna()
    if len(s) < 120:
        return 1
    rolling_max = s.rolling(120, min_periods=60).max()
    breakouts = ((s >= rolling_max * 0.999) & (s.shift(1) < rolling_max.shift(1) * 0.999))
    grouped = breakouts.astype(int).groupby((~breakouts).cumsum()).sum()
    return int(min(max((grouped > 0).sum(), 1), 5))


def pct_of_52w_high(df: pd.DataFrame) -> Optional[float]:
    d = df.tail(252)
    if len(d) < 60:
        return None
    hi = float(d["고가"].max())
    return None if hi <= 0 else float(d["종가"].iloc[-1] / hi)


def off_52w_low(df: pd.DataFrame) -> Optional[float]:
    d = df.tail(252)
    if len(d) < 60:
        return None
    lo = float(d["저가"].min())
    return None if lo <= 0 else float(d["종가"].iloc[-1] / lo - 1.0)


def ma_stack(df: pd.DataFrame) -> Dict[str, Optional[float]]:
    """이평선 정배열 여부 — 오닐은 50일선 위, 50일선 > 200일선을 요구합니다."""
    c = df["종가"]
    out = {}
    for n in (10, 21, 50, 150, 200):
        v = sma(c, n)
        out[f"ma{n}"] = None if v.dropna().empty else float(v.iloc[-1])
    last = float(c.iloc[-1])
    out["above_ma50"] = None if out["ma50"] is None else bool(last > out["ma50"])
    out["above_ma200"] = None if out["ma200"] is None else bool(last > out["ma200"])
    out["ma50_above_ma200"] = (
        None if (out["ma50"] is None or out["ma200"] is None)
        else bool(out["ma50"] > out["ma200"])
    )
    ma200 = sma(c, 200).dropna()
    out["ma200_rising"] = bool(len(ma200) > 22 and ma200.iloc[-1] > ma200.iloc[-22])
    return out

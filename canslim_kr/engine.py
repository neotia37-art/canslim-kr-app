"""
engine.py — CANSLIM 항목별 채점 + 종합 판정 + 매매 계획

각 항목은 0~100점으로 산출됩니다.
점수는 '통과선에서 50점, 오닐 원본 기준에서 90점' 이 되도록 선형 보간했습니다.
따라서 60점 미만 항목은 오닐 기준으로는 사실상 미달입니다.

M(시장)은 점수가 아니라 곱셈 게이트로 작동합니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Dict, List, Any

import numpy as np
import pandas as pd

from . import indicators as ind
from .datahub import (KRDataHub, build_quarterly_series, classify_disclosures,
                      last_business_day)


# ─────────────────────────────────────────────────────────────
def _interp(value: Optional[float], pass_at: float, full_at: float,
            floor: float = 0.0) -> float:
    """
    통과선=50점, 만점기준=90점으로 선형 매핑.
    통과선 미달이면 0~50 사이로 비례 감점됩니다.
    """
    if value is None:
        return -1.0
    if full_at == pass_at:
        return 90.0 if value >= pass_at else 30.0
    if value >= pass_at:
        r = (value - pass_at) / (full_at - pass_at)
        return float(np.clip(50 + r * 40, 50, 100))
    span = max(pass_at - floor, 1e-9)
    r = (value - floor) / span
    return float(np.clip(r * 50, 0, 50))


@dataclass
class FactorScore:
    key: str
    score: float
    detail: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    data_ok: bool = True

    def to_dict(self):
        return asdict(self)


# ─────────────────────────────────────────────────────────────
# C — 최근 분기 실적
# ─────────────────────────────────────────────────────────────
def score_C(q: pd.DataFrame, cfg) -> FactorScore:
    """
    q: build_quarterly_series() 결과 (단일 분기 시계열)
    전년 동기 대비를 씁니다. 한국은 계절성이 강해 직전 분기 비교는 왜곡됩니다.
    """
    c = cfg.C
    notes, detail = [], {}
    if q is None or q.empty or len(q) < 5:
        return FactorScore("C", -1, {}, ["분기 재무데이터 부족 — DART 조회 확인 필요"], False)

    q = q.dropna(subset=["net_income"], how="all")
    if len(q) < 5:
        return FactorScore("C", -1, {}, ["순이익 시계열 부족"], False)

    cur = q.iloc[-1]
    prev_mask = (q["year"] == cur["year"] - 1) & (q["quarter"] == cur["quarter"])
    if not prev_mask.any():
        return FactorScore("C", -1, {}, ["전년 동기 데이터 없음"], False)
    prev = q[prev_mask].iloc[0]

    detail["기준분기"] = f"{int(cur['year'])}년 {int(cur['quarter'])}분기"
    detail["재무제표"] = cur.get("fs_div")

    ni_now, ni_prev = cur["net_income"], prev["net_income"]
    rv_now, rv_prev = cur["revenue"], prev["revenue"]
    op_now, op_prev = cur["operating_income"], prev["operating_income"]

    # ── 순이익 성장 ──
    turnaround = False
    if ni_prev is None or ni_now is None:
        ni_yoy = None
    elif ni_prev <= 0 < ni_now:
        turnaround = True
        ni_yoy = None
        notes.append("흑자전환 — 성장률 산출 불가, 별도 점수 적용")
    elif ni_prev <= 0:
        ni_yoy = None
        notes.append("전년·당기 모두 적자 — C 항목 미달")
    else:
        ni_yoy = ni_now / ni_prev - 1.0

    detail["순이익 YoY"] = None if ni_yoy is None else round(ni_yoy, 4)
    detail["흑자전환"] = turnaround

    if turnaround:
        s_eps = c.turnaround_score
    elif ni_yoy is None:
        s_eps = 0.0
    else:
        s_eps = _interp(ni_yoy, c.eps_yoy_min, c.eps_yoy_strong, floor=-0.5)
        if ni_yoy >= c.eps_yoy_excellent:
            s_eps = min(100, s_eps + 8)
            notes.append(f"순이익 +{ni_yoy:.0%} — 오닐이 선호한 폭발적 증가 구간")

    # ── 매출 성장 ──
    if rv_now and rv_prev and rv_prev > 0:
        rv_yoy = rv_now / rv_prev - 1.0
        s_rev = _interp(rv_yoy, c.revenue_yoy_min, c.revenue_yoy_strong, floor=-0.3)
        detail["매출 YoY"] = round(rv_yoy, 4)
        if ni_yoy is not None and ni_yoy > 0.3 and rv_yoy < 0.05:
            notes.append("매출 정체 상태의 이익 증가 — 일회성 이익 가능성 점검")
    else:
        rv_yoy, s_rev = None, 50.0
        detail["매출 YoY"] = None

    # ── 영업이익률 ──
    if all(v is not None and v == v for v in (op_now, op_prev, rv_now, rv_prev)) \
            and rv_now > 0 and rv_prev > 0:
        m_now, m_prev = op_now / rv_now, op_prev / rv_prev
        detail["영업이익률"] = round(m_now, 4)
        detail["영업이익률 변화(%p)"] = round((m_now - m_prev) * 100, 2)
        margin_bonus = 6 if m_now > m_prev else -4
    else:
        margin_bonus = 0

    # ── 가속(acceleration) ──
    accel_bonus = 0.0
    if len(q) >= 9:
        prev_q = q.iloc[-2]
        pp_mask = (q["year"] == prev_q["year"] - 1) & (q["quarter"] == prev_q["quarter"])
        if pp_mask.any() and ni_yoy is not None:
            pq_prev = q[pp_mask].iloc[0]["net_income"]
            if pq_prev and pq_prev > 0 and prev_q["net_income"] is not None:
                prev_yoy = prev_q["net_income"] / pq_prev - 1.0
                detail["직전분기 YoY"] = round(prev_yoy, 4)
                if ni_yoy > prev_yoy:
                    accel_bonus = c.acceleration_bonus
                    notes.append("이익 증가율 가속 — 오닐이 가장 중시한 신호")
                else:
                    notes.append("이익 증가율 둔화")

    score = 0.70 * s_eps + 0.30 * s_rev + margin_bonus + accel_bonus
    score = float(np.clip(score, 0, 100))

    # ── 데이터 신선도 ──
    q_end_month = int(cur["quarter"]) * 3
    q_end = datetime(int(cur["year"]), q_end_month, 1)
    age = (datetime.now() - q_end).days
    detail["데이터 경과일"] = age
    if age > c.stale_after_days:
        notes.append(f"최신 분기 데이터가 {age}일 경과 — 미공시 또는 수집 실패 점검")
        score *= 0.9

    return FactorScore("C", round(score, 1), detail, notes)


# ─────────────────────────────────────────────────────────────
# A — 연간 실적
# ─────────────────────────────────────────────────────────────
def score_A(q: pd.DataFrame, cfg) -> FactorScore:
    a = cfg.A
    notes, detail = [], {}
    if q is None or q.empty:
        return FactorScore("A", -1, {}, ["연간 재무데이터 없음"], False)

    ann = q.groupby("year").agg(
        revenue=("revenue", "sum"),
        operating_income=("operating_income", "sum"),
        net_income=("net_income", "sum"),
        equity=("equity", "last"),
        n=("quarter", "count"),
    ).reset_index()
    full = ann[ann["n"] == 4]
    if len(full) < 2:
        return FactorScore("A", -1, {}, ["완전한 회계연도가 2개 미만"], False)

    detail["연도"] = full["year"].tolist()
    detail["순이익"] = [None if pd.isna(v) else float(v) for v in full["net_income"]]

    # ── EPS(순이익) CAGR ──
    yrs = min(a.eps_cagr_years, len(full) - 1)
    first, last = full["net_income"].iloc[-(yrs + 1)], full["net_income"].iloc[-1]
    if first and last and first > 0 and last > 0:
        cagr = (last / first) ** (1 / yrs) - 1
        s_cagr = _interp(cagr, a.eps_cagr_min, a.eps_cagr_strong, floor=-0.2)
        detail[f"순이익 {yrs}년 CAGR"] = round(cagr, 4)
    elif last and last > 0 and (first is None or first <= 0):
        cagr = None
        s_cagr = 65.0
        notes.append("과거 적자 → 현재 흑자 구조 전환")
    else:
        cagr, s_cagr = None, 10.0
        notes.append("연간 순이익 적자 — A 항목 미달")

    # ── 매출 CAGR ──
    rf, rl = full["revenue"].iloc[-(yrs + 1)], full["revenue"].iloc[-1]
    if rf and rl and rf > 0:
        rcagr = (rl / rf) ** (1 / yrs) - 1
        s_rev = _interp(rcagr, a.revenue_cagr_min, a.revenue_cagr_strong, floor=-0.15)
        detail[f"매출 {yrs}년 CAGR"] = round(rcagr, 4)
    else:
        s_rev = 50.0

    # ── ROE ──
    eq, ni = full["equity"].iloc[-1], full["net_income"].iloc[-1]
    if eq and ni and eq > 0:
        roe = ni / eq
        s_roe = _interp(roe, a.roe_min, a.roe_strong, floor=-0.05)
        detail["ROE"] = round(roe, 4)
        if roe >= a.roe_strong:
            notes.append(f"ROE {roe:.1%} — 오닐 기준(17%) 충족")
    else:
        s_roe = 45.0
        notes.append("ROE 산출 불가")

    # ── 연속 흑자 ──
    profitable = int((full["net_income"] > 0).tail(a.require_profitable_years).sum())
    detail["연속 흑자 연수"] = profitable
    consistency = 8 if profitable >= a.require_profitable_years else -12
    if consistency < 0:
        notes.append("최근 연도 중 적자 이력 존재")

    score = float(np.clip(0.45 * s_cagr + 0.25 * s_rev + 0.30 * s_roe + consistency, 0, 100))
    return FactorScore("A", round(score, 1), detail, notes)


# ─────────────────────────────────────────────────────────────
# N — 신고가 / 베이스
# ─────────────────────────────────────────────────────────────
def score_N(df: pd.DataFrame, cfg, base: ind.BaseResult) -> FactorScore:
    n = cfg.N
    notes, detail = [], {}
    pct_hi = ind.pct_of_52w_high(df)
    off_lo = ind.off_52w_low(df)
    if pct_hi is None:
        return FactorScore("N", -1, {}, ["시세 데이터 부족"], False)

    detail["52주 고점 대비"] = round(pct_hi, 4)
    detail["52주 저점 대비 상승"] = None if off_lo is None else round(off_lo, 4)

    s_hi = _interp(pct_hi, n.pct_of_52w_high_min, n.pct_of_52w_high_strong, floor=0.4)
    s_lo = 50.0 if off_lo is None else _interp(off_lo, n.min_off_52w_low, n.min_off_52w_low * 3, floor=-0.2)

    # 최근 신고가 갱신
    recent = df.tail(n.recent_high_lookback)
    made_high = bool(len(df) > 252 and recent["고가"].max() >= df.tail(252)["고가"].max() * 0.999)
    detail["최근 신고가 갱신"] = made_high
    if made_high:
        notes.append(f"최근 {n.recent_high_lookback}거래일 내 52주 신고가 갱신")

    # 이평선 정배열
    stack = ind.ma_stack(df)
    detail.update({k: v for k, v in stack.items() if k.startswith("above") or k.startswith("ma50_") or k == "ma200_rising"})
    ma_bonus = 0
    for key, pts, msg in (
        ("above_ma50", 5, "50일선 이탈 — 단기 추세 훼손"),
        ("above_ma200", 6, "200일선 아래 — 장기 추세 미확립"),
        ("ma50_above_ma200", 5, "정배열 미형성"),
    ):
        if stack.get(key) is True:
            ma_bonus += pts
        elif stack.get(key) is False:
            ma_bonus -= pts
            notes.append(msg)
    if stack.get("ma200_rising"):
        ma_bonus += 3

    # 베이스 상태
    base_bonus = 0
    if base.found:
        detail["베이스"] = {
            "패턴": base.pattern, "기간(주)": base.weeks,
            "깊이": base.depth, "피벗": base.pivot, "상태": base.status,
        }
        base_bonus = {"BREAKOUT": 14, "HANDLE": 10, "BUILDING": 5,
                      "EXTENDED": -8, "FAILED": -15}.get(base.status, 0)
        if base.stage >= cfg.base.late_stage_warn:
            base_bonus -= 6
            notes.append(f"{base.stage}차 베이스 — 후기 베이스는 실패율이 높음")
        notes.extend(base.notes or [])
    else:
        notes.append("유효 베이스 미형성 — 매수 기준가(피벗) 없음")
        base_bonus = -8

    # 신고가 근접도 60% + 저점대비 상승 20% + 기준점 20%, 여기에 이평·베이스 가감점
    score = float(np.clip(0.60 * s_hi + 0.20 * s_lo + 10 + ma_bonus + base_bonus, 0, 100))
    return FactorScore("N", round(score, 1), detail, notes)


# ─────────────────────────────────────────────────────────────
# S — 수급 (물량)
# ─────────────────────────────────────────────────────────────
def score_S(df: pd.DataFrame, cfg, shares_out: Optional[float] = None,
            disclosures: Optional[Dict[str, int]] = None,
            short_ratio: Optional[float] = None) -> FactorScore:
    s = cfg.S
    notes, detail = [], {}

    vsurge = ind.volume_surge(df, 50)
    udv = ind.up_down_volume_ratio(df, 50)
    detail["당일 거래량/50일평균"] = None if vsurge is None else round(vsurge, 2)
    detail["상승일/하락일 거래량비"] = None if udv is None else round(udv, 2)

    s_vol = 50.0 if vsurge is None else _interp(vsurge, s.breakout_volume_ratio,
                                                s.breakout_volume_strong, floor=0.3)
    s_udv = 50.0 if udv is None else _interp(udv, s.up_down_volume_ratio_min,
                                             s.up_down_volume_ratio_strong, floor=0.5)
    if udv is not None and udv < 1.0:
        notes.append("하락일 거래량 우위 — 분산(매도) 국면")

    score = 0.45 * s_vol + 0.55 * s_udv

    # ★ 한국 특유 이벤트 — 여기가 미국판과 가장 다른 부분
    if disclosures:
        detail["공시이벤트"] = {k: v for k, v in disclosures.items() if v}
        if disclosures.get("dilution", 0) > 0:
            score -= s.dilution_penalty
            notes.append(f"최근 유상증자 공시 {disclosures['dilution']}건 — 물량 희석 (강한 감점)")
        if disclosures.get("cb_bw", 0) > 0:
            score -= s.cb_bw_penalty
            notes.append(f"CB/BW 관련 공시 {disclosures['cb_bw']}건 — 잠재 물량 부담")
        if disclosures.get("treasury_buy", 0) > 0:
            score += s.treasury_buy_bonus
            notes.append("자사주 취득 공시 — 오닐이 선호한 신호")
        if disclosures.get("treasury_cancel", 0) > 0:
            score += s.treasury_cancel_bonus
            notes.append("자사주 소각 공시 — 주당가치 직접 증가")
        if disclosures.get("split_off", 0) > 0:
            score -= 10
            notes.append("분할 공시 — 한국 시장 특유의 주주가치 훼손 리스크 점검")

    if short_ratio is not None:
        detail["공매도 잔고비율"] = round(short_ratio, 4)
        if short_ratio > s.short_balance_warn:
            score -= 8
            notes.append(f"공매도 잔고 {short_ratio:.2%} — 상단 저항 요인")

    if shares_out:
        detail["상장주식수"] = int(shares_out)
        if shares_out > 500_000_000:
            score -= 5
            notes.append("상장주식수 과다 — 주가 탄력 저하 (오닐: 유통물량 적은 종목 선호)")

    return FactorScore("S", round(float(np.clip(score, 0, 100)), 1), detail, notes)


# ─────────────────────────────────────────────────────────────
# L — 주도주
# ─────────────────────────────────────────────────────────────
def score_L(cfg, rs_rating: Optional[int] = None,
            sector_pct: Optional[float] = None,
            sector_strength: Optional[float] = None,
            rs_line_high: Optional[bool] = None) -> FactorScore:
    l = cfg.L
    notes, detail = [], {}
    if rs_rating is None:
        return FactorScore("L", -1, {}, ["RS Rating 산출 불가 — 유니버스 시세 수집 필요"], False)

    detail["RS Rating"] = int(rs_rating)
    s_rs = _interp(float(rs_rating), float(l.rs_rating_min), float(l.rs_rating_strong), floor=1.0)
    if rs_rating >= l.rs_rating_strong:
        notes.append(f"RS {rs_rating} — 상위 10% 주도주 영역")
    elif rs_rating < l.rs_rating_min:
        notes.append(f"RS {rs_rating} — 오닐 기준(80) 미달. 후발주일 가능성")

    score = s_rs
    if sector_pct is not None:
        detail["업종 내 백분위"] = round(sector_pct, 3)
        score += 10 if sector_pct >= l.sector_rank_min else -8
        if sector_pct >= l.sector_rank_min:
            notes.append("업종 내 상위 20% — 업종 대표주 위치")
    if sector_strength is not None:
        detail["업종 강도 백분위"] = round(sector_strength, 3)
        score += 6 if sector_strength >= l.sector_strength_min else -6
    if rs_line_high:
        score += l.rs_line_new_high_bonus
        notes.append("RS Line 신고가 — 주가보다 먼저 나오는 선행 신호")
        detail["RS Line 신고가"] = True

    return FactorScore("L", round(float(np.clip(score, 0, 100)), 1), detail, notes)


# ─────────────────────────────────────────────────────────────
# I — 기관 수급  ★한국 시장의 최대 강점 지표★
# ─────────────────────────────────────────────────────────────
def score_I(flow: pd.DataFrame, cfg, market_cap: Optional[float] = None,
            foreign: Optional[pd.DataFrame] = None) -> FactorScore:
    i = cfg.I
    notes, detail = [], {}
    if flow is None or flow.empty or not market_cap:
        return FactorScore("I", -1, {}, ["투자자별 수급 데이터 없음"], False)

    def col(*cands):
        for c in cands:
            if c in flow.columns:
                return c
        return None

    c_inst = col("기관합계", "기관")
    c_for = col("외국인합계", "외국인")
    c_ret = col("개인")
    if c_inst is None and c_for is None:
        return FactorScore("I", -1, {}, ["기관/외국인 컬럼 없음"], False)

    score = 50.0
    for w in i.windows:
        tail = flow.tail(w)
        if c_inst:
            r = float(tail[c_inst].sum()) / market_cap
            detail[f"기관 {w}일 순매수/시총"] = round(r, 5)
        if c_for:
            rf = float(tail[c_for].sum()) / market_cap
            detail[f"외국인 {w}일 순매수/시총"] = round(rf, 5)

    inst20 = detail.get("기관 20일 순매수/시총")
    for20 = detail.get("외국인 20일 순매수/시총")

    s_inst = 50.0 if inst20 is None else _interp(
        inst20, i.inst_net_buy_ratio_min, i.inst_net_buy_ratio_strong, floor=-0.01)
    s_for = 50.0 if for20 is None else _interp(
        for20, i.foreign_net_buy_ratio_min, i.foreign_net_buy_ratio_strong, floor=-0.01)
    score = 0.5 * s_inst + 0.5 * s_for

    # 쌍끌이 매수
    if inst20 and for20 and inst20 > 0 and for20 > 0:
        score += i.dual_accumulation_bonus
        notes.append("기관·외국인 동시 순매수 — 가장 강한 수급 신호")
    elif (inst20 is not None and inst20 < 0) and (for20 is not None and for20 < 0):
        notes.append("기관·외국인 동시 순매도 — 매수 보류")

    # 개인만 사는 종목 = 역신호
    if c_ret:
        ret20 = float(flow.tail(20)[c_ret].sum()) / market_cap
        detail["개인 20일 순매수/시총"] = round(ret20, 5)
        if ret20 > 0 and (inst20 or 0) < 0 and (for20 or 0) < 0:
            score -= i.retail_dominant_penalty
            notes.append("개인만 순매수 중 — 오닐 관점에서 회피 대상")

    # 연속 순매수일
    if c_for:
        f = flow[c_for].tail(20)
        streak = 0
        for v in reversed(f.tolist()):
            if v > 0:
                streak += 1
            else:
                break
        detail["외국인 연속 순매수일"] = streak
        if streak >= i.consecutive_buy_days_bonus:
            score += 8
            notes.append(f"외국인 {streak}일 연속 순매수")

    # 외국인 지분율 추세
    if foreign is not None and not foreign.empty:
        rc = None
        for c in ("지분율", "보유율", "외국인보유율"):
            if c in foreign.columns:
                rc = c
                break
        if rc and len(foreign) > 60:
            d = float(foreign[rc].iloc[-1] - foreign[rc].iloc[-60]) / 100.0
            detail["외국인 지분율 60일 변화(%p)"] = round(d * 100, 2)
            if d >= i.foreign_holding_delta_min:
                score += 7
                notes.append("외국인 지분율 상승 추세")
            elif d < -0.005:
                score -= 7
                notes.append("외국인 지분율 하락 추세")

    return FactorScore("I", round(float(np.clip(score, 0, 100)), 1), detail, notes)


# ─────────────────────────────────────────────────────────────
# 매매 계획
# ─────────────────────────────────────────────────────────────
def build_trade_plan(df: pd.DataFrame, base: ind.BaseResult, cfg,
                     market_state: Optional[str] = None,
                     account_size: Optional[float] = None) -> Dict[str, Any]:
    t = cfg.trade
    last = float(df["종가"].iloc[-1])
    plan: Dict[str, Any] = {"현재가": last}

    if not base.found or not base.pivot:
        plan["상태"] = "대기"
        plan["사유"] = "매수 기준가(피벗)를 정의할 베이스가 없습니다. 베이스 형성까지 관찰."
        return plan

    pivot = float(base.pivot)
    lo, hi = pivot, pivot * (1 + cfg.N.buy_zone_pct)
    plan.update({
        "피벗(매수기준가)": round(pivot, 0),
        "매수구간": [round(lo, 0), round(hi, 0)],
        "베이스패턴": base.pattern,
        "베이스상태": base.status,
    })

    if last < lo:
        plan["상태"] = "돌파 대기"
        plan["사유"] = f"피벗까지 {(pivot/last - 1):.1%} 남았습니다. 대량 거래 동반 돌파를 기다리세요."
    elif last <= hi:
        plan["상태"] = "매수 가능 구간"
        plan["사유"] = "피벗 돌파 후 5% 이내 — 오닐이 정의한 유일한 매수 구간입니다."
    else:
        plan["상태"] = "확장(추격 금지)"
        plan["사유"] = f"피벗 대비 {(last/pivot - 1):.1%} 확장. 추격 매수 시 손절폭이 커집니다. 다음 베이스 대기."

    entry = last if lo <= last <= hi else pivot
    stop = entry * (1 - t.stop_loss_pct)
    plan["손절가"] = round(stop, 0)
    plan["손절폭"] = f"-{t.stop_loss_pct:.0%}"
    plan["1차 목표"] = round(entry * (1 + t.take_profit_1), 0)
    plan["2차 목표"] = round(entry * (1 + t.take_profit_2), 0)
    plan["손익비"] = round(t.take_profit_1 / t.stop_loss_pct, 2)

    plan["분할매수"] = [
        {"차수": k + 1,
         "비중": f"{w:.0%}",
         "트리거": f"피벗 {trig:+.1%}",
         "가격": round(pivot * (1 + trig), 0)}
        for k, (w, trig) in enumerate(zip(t.pyramid_steps, t.pyramid_triggers))
    ]

    plan["보유규칙"] = [
        f"매수 후 3주 내 +{t.eight_week_rule_gain:.0%} 이상 상승 시 8주 보유 규칙 적용",
        f"{t.sell_below_ma}일선을 대량 거래와 함께 이탈하면 비중 축소",
        "손절선은 절대 아래로 내리지 않습니다 (오닐의 단일 최우선 원칙)",
    ]

    exposure = cfg.M.exposure_by_state.get(market_state or "", 1.0)
    max_pct = t.max_position_pct * exposure
    plan["시장상태"] = market_state
    plan["허용비중"] = f"{max_pct:.0%} (시장상태 반영)"
    if exposure == 0:
        plan["상태"] = "신규 매수 금지"
        plan["사유"] = "시장이 조정 국면입니다. 오닐 규칙상 신규 진입을 하지 않습니다."

    if account_size:
        risk_amt = account_size * t.risk_per_trade
        per_share_risk = entry - stop
        if per_share_risk > 0:
            qty_risk = int(risk_amt / per_share_risk)
            qty_cap = int(account_size * max_pct / entry)
            qty = max(0, min(qty_risk, qty_cap))
            plan["권장수량"] = qty
            plan["투입금액"] = round(qty * entry, 0)
            plan["최대손실"] = round(qty * per_share_risk, 0)
    return plan


# ─────────────────────────────────────────────────────────────
# 종합
# ─────────────────────────────────────────────────────────────
@dataclass
class CanslimReport:
    code: str
    name: str
    market: str
    date: str
    total_score: float
    grade: str
    verdict: str
    factors: Dict[str, dict]
    base: dict
    trade_plan: dict
    market_state: dict
    warnings: List[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def combine(factors: List[FactorScore], cfg, market_state: str) -> Dict[str, Any]:
    w = dict(cfg.scoring.weights)
    got, used_w = 0.0, 0.0
    missing = []
    for f in factors:
        wt = w.get(f.key, 0)
        if f.score < 0 or not f.data_ok:
            missing.append(f.key)
            if cfg.scoring.missing_policy == "neutral":
                got += 50 * wt
                used_w += wt
            elif cfg.scoring.missing_policy == "penalize":
                used_w += wt
            continue
        got += f.score * wt
        used_w += wt
    raw = got / used_w if used_w > 0 else 0.0
    gate = cfg.scoring.market_gate.get(market_state, 0.5)
    total = raw * gate

    grade = "D"
    for g, cut in cfg.scoring.grade_cuts.items():
        if total >= cut:
            grade = g
            break

    if total >= cfg.scoring.actionable_min_score:
        verdict = "매수 후보 (베이스·수급 확인 후 진입)"
    elif total >= cfg.scoring.watchlist_min_score:
        verdict = "관찰 대상"
    else:
        verdict = "제외"

    return {"raw_score": round(raw, 1), "gate": gate,
            "total_score": round(total, 1), "grade": grade,
            "verdict": verdict, "missing": missing}

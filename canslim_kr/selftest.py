"""
selftest.py — 네트워크 없이 엔진 로직을 검증합니다.

합성 데이터로 확인하는 항목
  1) 컵앤핸들 베이스를 실제로 잡아내는가
  2) RS Rating 백분위가 강한 종목에 높게 나오는가
  3) 분산일/FTD 판정이 의도대로 동작하는가
  4) 누적 재무제표 → 단일 분기 차분이 정확한가
  5) 채점·매매계획이 합리적인 값을 내는가

실행: python -m canslim_kr.selftest
"""

import numpy as np
import pandas as pd

from .config import DEFAULT
from . import indicators as ind
from . import market as mkt
from . import engine as eng
from .datahub import quarterly_from_cumulative


def _ohlcv(close: np.ndarray, start="2024-01-01", vol=None) -> pd.DataFrame:
    n = len(close)
    idx = pd.bdate_range(start, periods=n)
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 0.004, n)
    high = close * (1 + np.abs(noise) + 0.004)
    low = close * (1 - np.abs(noise) - 0.004)
    if vol is None:
        vol = rng.integers(800_000, 1_200_000, n).astype(float)
    return pd.DataFrame({
        "시가": close * (1 + noise / 2), "고가": high, "저가": low,
        "종가": close, "거래량": vol,
        "거래대금": close * vol,
    }, index=idx)


def make_cup_handle() -> pd.DataFrame:
    """사전 상승 → 컵 → 손잡이 → 돌파 구조를 합성합니다."""
    prior = np.linspace(10_000, 20_000, 130)                  # +100% 사전 상승
    cup_l = np.linspace(20_000, 15_000, 45)                   # 좌측 하락 (-25%)
    cup_r = np.linspace(15_000, 19_800, 50)                   # 우측 회복
    handle = np.linspace(19_800, 18_300, 14)                  # 손잡이 -7.5%
    breakout = np.linspace(18_300, 20_600, 6)                 # 피벗 돌파
    close = np.concatenate([prior, cup_l, cup_r, handle, breakout])
    n = len(close)
    rng = np.random.default_rng(3)
    vol = rng.integers(700_000, 1_000_000, n).astype(float)
    vol[-6:] *= 2.4                                            # 돌파일 대량 거래
    vol[len(prior)+len(cup_l)+len(cup_r):-6] *= 0.55           # 손잡이 거래량 감소
    return _ohlcv(close, vol=vol)


def make_laggard() -> pd.DataFrame:
    close = np.concatenate([
        np.linspace(20_000, 12_000, 160),
        np.linspace(12_000, 13_000, 85),
    ])
    return _ohlcv(close)


def make_index(kind="uptrend") -> pd.DataFrame:
    rng = np.random.default_rng(11)
    if kind == "uptrend":
        base = np.linspace(2_400, 2_900, 260) + rng.normal(0, 12, 260)
        vol = rng.integers(400, 600, 260).astype(float)
    elif kind == "distribution":
        base = np.linspace(2_900, 2_820, 260) + rng.normal(0, 14, 260)
        vol = rng.integers(400, 600, 260).astype(float)
        for i in range(240, 258, 2):          # 인위적 분산일 삽입
            base[i] = base[i - 1] * 0.992
            vol[i] = vol[i - 1] * 1.3
    else:  # ftd
        base = np.concatenate([
            np.linspace(2_900, 2_500, 220),
            np.linspace(2_500, 2_520, 4),
            np.array([2_520 * 1.015]),
            np.linspace(2_558, 2_620, 15),
        ])
        vol = rng.integers(400, 600, len(base)).astype(float)
        vol[224] = vol[223] * 1.6
    idx = pd.bdate_range("2024-01-01", periods=len(base))
    return pd.DataFrame({
        "시가": base, "고가": base * 1.004, "저가": base * 0.996,
        "종가": base, "거래량": vol,
    }, index=idx)


def run():
    cfg = DEFAULT
    ok = lambda b: "PASS" if b else "FAIL"
    print("=" * 62)
    print("CANSLIM-KR 자체 테스트")
    print("=" * 62)

    # 1. 베이스 탐지
    cup = make_cup_handle()
    base = ind.detect_base(cup, cfg)
    base.stage = ind.base_stage(cup["종가"], cfg)
    print(f"\n[1] 베이스 탐지            {ok(base.found)}")
    print(f"    패턴 {base.pattern} | {base.weeks}주 | 깊이 "
          f"{(base.depth or 0):.1%} | 피벗 {base.pivot:,.0f} | 상태 {base.status}")
    print(f"    손잡이 깊이 {base.handle_depth} | 사전상승 {base.prior_uptrend} | {base.stage}차")
    for n in base.notes or []:
        print(f"    · {n}")

    lag = make_laggard()
    lag_base = ind.detect_base(lag, cfg)
    print(f"    후발주 오탐 방지        {ok(not lag_base.found or lag_base.status != 'BREAKOUT')}")

    # 2. RS Rating
    rng = np.random.default_rng(5)
    cm = {}
    for i in range(200):
        drift = rng.normal(0.0002, 0.0006)
        cm[f"{i:06d}"] = 10000 * np.exp(np.cumsum(rng.normal(drift, 0.015, 300)))
    cm["999990"] = np.linspace(10_000, 26_000, 300)   # 강한 종목
    cm["999980"] = np.linspace(10_000, 6_000, 300)    # 약한 종목
    cmdf = pd.DataFrame(cm, index=pd.bdate_range("2024-01-01", periods=300))
    rs = ind.rs_rating_table(cmdf)
    strong = int(rs.loc["999990", "rs_rating"])
    weak = int(rs.loc["999980", "rs_rating"])
    print(f"\n[2] RS Rating             {ok(strong >= 90 and weak <= 10)}")
    print(f"    강세종목 RS {strong} / 약세종목 RS {weak} (기대: ≥90 / ≤10)")

    # 3. 시장 판정
    up = mkt.assess_market(make_index("uptrend"), cfg, "KOSPI")
    dist = mkt.assess_market(make_index("distribution"), cfg, "KOSPI")
    ftd_idx = make_index("ftd")
    ftd = mkt.find_follow_through_day(ftd_idx, cfg, is_kosdaq=False)
    print(f"\n[3] 시장 판정 (M)")
    print(f"    상승장     → {up.state} (분산일 {up.distribution_days})  {ok(up.state=='CONFIRMED_UPTREND')}")
    print(f"    분산장     → {dist.state} (분산일 {dist.distribution_days})  "
          f"{ok(dist.distribution_days >= cfg.M.dd_pressure_threshold)}")
    print(f"    FTD 탐지   → {ftd}  {ok(ftd is not None)}")

    # 4. 누적 → 단일분기 차분  (한국 재무데이터의 핵심 함정)
    cum = {1: 100, 2: 260, 3: 450, 4: 700}
    q = quarterly_from_cumulative(cum)
    expect = {1: 100, 2: 160, 3: 190, 4: 250}
    print(f"\n[4] 누적→분기 차분        {ok(q == expect)}")
    print(f"    누적 {cum}")
    print(f"    분기 {q}  (기대 {expect})")

    # 5. 채점
    qdf = pd.DataFrame([
        {"year": 2023, "quarter": q_, "revenue": 1000 + q_*30, "operating_income": 120 + q_*6,
         "net_income": 100 + q_*5, "equity": 3000, "fs_div": "CFS"} for q_ in (1,2,3,4)
    ] + [
        {"year": 2024, "quarter": q_, "revenue": 1300 + q_*45, "operating_income": 190 + q_*12,
         "net_income": 160 + q_*11, "equity": 3400, "fs_div": "CFS"} for q_ in (1,2,3,4)
    ] + [
        {"year": 2025, "quarter": q_, "revenue": 1750 + q_*60, "operating_income": 290 + q_*18,
         "net_income": 250 + q_*17, "equity": 3900, "fs_div": "CFS"} for q_ in (1,2,3,4)
    ] + [
        {"year": 2026, "quarter": 1, "revenue": 2400, "operating_income": 400,
         "net_income": 360, "equity": 4200, "fs_div": "CFS"},
    ])
    fC = eng.score_C(qdf, cfg)
    fA = eng.score_A(qdf, cfg)
    fN = eng.score_N(cup, cfg, base)
    fS = eng.score_S(cup, cfg, shares_out=60_000_000,
                     disclosures={"treasury_buy": 1, "dilution": 0, "cb_bw": 0})
    fL = eng.score_L(cfg, rs_rating=93, sector_pct=0.91, sector_strength=0.72, rs_line_high=True)

    flow = pd.DataFrame({
        "기관합계": np.r_[np.full(40, 3e8), np.full(20, 8e8)],
        "외국인합계": np.r_[np.full(40, 2e8), np.full(20, 9e8)],
        "개인": np.r_[np.full(40, -5e8), np.full(20, -17e8)],
    }, index=pd.bdate_range("2026-03-01", periods=60))
    fI = eng.score_I(flow, cfg, market_cap=1.2e12)

    print(f"\n[5] 항목별 채점")
    for f in (fC, fA, fN, fS, fL, fI):
        head = (f.notes or ["—"])[0]
        print(f"    {f.key}  {f.score:>6}   {head[:52]}")

    summary = eng.combine([fC, fA, fN, fS, fL, fI], cfg, "CONFIRMED_UPTREND")
    print(f"\n    종합 {summary['total_score']}점 · {summary['grade']} · {summary['verdict']}")
    print(f"    게이트 배수 {summary['gate']} | 결측 {summary['missing'] or '없음'}")
    print(f"    합성 우량주 판정 정상   {ok(summary['total_score'] >= 70)}")

    # 시장 게이트 효과
    bear = eng.combine([fC, fA, fN, fS, fL, fI], cfg, "MARKET_IN_CORRECTION")
    print(f"    조정장 적용 시 {bear['total_score']}점 → {bear['verdict']}"
          f"   {ok(bear['total_score'] < summary['total_score'])}")

    # 6. 매매 계획
    plan = eng.build_trade_plan(cup, base, cfg, "CONFIRMED_UPTREND", account_size=50_000_000)
    print(f"\n[6] 매매 계획")
    for k in ("현재가", "피벗(매수기준가)", "매수구간", "상태", "손절가",
              "1차 목표", "손익비", "권장수량", "투입금액", "최대손실", "허용비중"):
        if k in plan:
            print(f"    {k}: {plan[k]}")
    print(f"    사유: {plan.get('사유')}")

    plan_bear = eng.build_trade_plan(cup, base, cfg, "MARKET_IN_CORRECTION", 50_000_000)
    print(f"\n    조정장 시 상태: {plan_bear['상태']}   {ok(plan_bear['상태']=='신규 매수 금지')}")
    print("\n" + "=" * 62)


if __name__ == "__main__":
    run()

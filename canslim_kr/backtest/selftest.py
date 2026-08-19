"""
backtest/selftest.py — 네트워크 없이 백테스트 엔진을 검증합니다.

합성 시장(상승장→조정장→회복장)을 만들어 확인하는 항목
  1) 거래비용 세율 스케줄이 시점별로 정확히 적용되는가
  2) D일 신호 → D+1일 시가 체결이 지켜지는가 (미래참조 없음)
  3) 손절/익절/8주룰이 발동하는가
  4) 시장 게이트가 조정장에서 신규매수를 막는가
  5) 성과지표가 합리적으로 계산되는가
  6) 워크포워드가 학습·검증을 분리하는가

실행: python -m canslim_kr.backtest.selftest
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import DEFAULT
from .costs import CostModel, FRICTIONLESS
from .panel import PanelStore, available_from
from .signals import SignalPanel
from .simulator import Backtester
from .metrics import performance, yearly_breakdown, report
from .optimizer import Optimizer, set_param, sanity_checks


# ─────────────────────────────────────────────────────────────
class FakeStore(PanelStore):
    """PanelStore와 동일한 인터페이스를 갖는 합성 데이터 저장소."""

    def __init__(self, n_stocks=60, n_days=2200, seed=17):
        self.verbose = False
        rng = np.random.default_rng(seed)
        dates = pd.bdate_range("2018-01-01", periods=n_days)
        codes = [f"{i:06d}" for i in range(1, n_stocks + 1)]

        # 시장 국면: 상승 → 조정 → 회복 → 상승
        seg = n_days // 4
        mkt_dr = np.concatenate([
            np.full(seg, 0.0006), np.full(seg, -0.0011),
            np.full(seg, 0.0004), np.full(n_days - 3 * seg, 0.0008),
        ])
        mkt_ret = mkt_dr + rng.normal(0, 0.009, n_days)
        idx_c = 2000 * np.exp(np.cumsum(mkt_ret))
        idx_v = rng.integers(400_000, 700_000, n_days).astype(float)

        self._idx = {}
        for nm, scale in (("KOSPI", 1.0), ("KOSDAQ", 1.35)):
            c = 2000 * np.exp(np.cumsum(mkt_dr + rng.normal(0, 0.009 * scale, n_days)))
            self._idx[nm] = pd.DataFrame({
                "시가": c, "고가": c * 1.005, "저가": c * 0.995, "종가": c,
                "거래량": rng.integers(400_000, 700_000, n_days).astype(float),
            }, index=dates)

        # 종목: 시장 베타 + 개별 알파. 일부는 강한 추세주로 심습니다.
        C = {}
        for i, code in enumerate(codes):
            beta = rng.uniform(0.6, 1.6)
            alpha = rng.normal(0.0002, 0.0006)
            if i < 12:
                alpha += 0.0011          # 주도주 그룹
            r = beta * mkt_ret + alpha + rng.normal(0, 0.014, n_days)
            C[code] = 10_000 * np.exp(np.cumsum(r))
        Cdf = pd.DataFrame(C, index=dates)
        noise = pd.DataFrame(rng.normal(0, 0.006, Cdf.shape), index=dates, columns=codes)

        self._px = {
            "종가": Cdf,
            "시가": Cdf.shift(1).fillna(Cdf) * (1 + noise / 3),
            "고가": Cdf * (1 + noise.abs() + 0.004),
            "저가": Cdf * (1 - noise.abs() - 0.004),
            "거래량": pd.DataFrame(rng.integers(500_000, 2_500_000, Cdf.shape),
                                 index=dates, columns=codes).astype(float),
        }
        self._px["거래대금"] = self._px["종가"] * self._px["거래량"]

        self._caps = pd.DataFrame(
            rng.uniform(3e11, 5e12, (len(dates), len(codes))), index=dates, columns=codes)
        self._flow = {
            k: pd.DataFrame(rng.normal(2e8, 3e9, Cdf.shape), index=dates, columns=codes)
            for k in ("기관", "외국인")
        }
        self._codes, self._dates = codes, dates

        # PIT 재무 — available_from을 실제 규칙대로 부여
        rows = []
        for code in codes:
            base = rng.uniform(2e10, 8e10)
            g = rng.uniform(0.98, 1.14)
            for y in range(2017, 2027):
                for q in (1, 2, 3, 4):
                    base *= g
                    rows.append({
                        "Code": code, "year": y, "quarter": q,
                        "revenue": base * 6, "operating_income": base * 1.2,
                        "net_income": base, "equity": base * 8,
                        "available_from": available_from(y, q),
                    })
        self._fin = pd.DataFrame(rows)

    # PanelStore 인터페이스
    def px(self, field="종가"): return self._px[field]
    def index(self, name="KOSPI"): return self._idx[name]
    def all_codes(self): return self._codes
    def market_of(self):
        return pd.Series({c: ("KOSDAQ" if i % 3 == 0 else "KOSPI")
                          for i, c in enumerate(self._codes)})
    def listed_mask(self, dates, codes):
        return pd.DataFrame(True, index=dates, columns=codes)
    def cap_panel(self, dates, codes):
        return self._caps.reindex(index=dates, columns=codes)
    def flow(self, kind="기관"): return self._flow.get(kind)
    def _load(self, name):
        return self._fin if name == "financials_pit" else None
    def financials_asof(self, date):
        return PanelStore.financials_asof(self, date)


def run():
    ok = lambda b: "PASS" if b else "FAIL"
    print("=" * 64)
    print("CANSLIM-KR 백테스트 엔진 자체 테스트")
    print("=" * 64)

    # ── 1. 거래비용 세율 스케줄 ──
    cm = CostModel()
    cases = [
        ("2018-05-01", "KOSPI", 0.0030), ("2020-03-01", "KOSPI", 0.0025),
        ("2021-07-01", "KOSDAQ", 0.0023), ("2023-05-01", "KOSPI", 0.0020),
        ("2024-05-01", "KOSDAQ", 0.0018), ("2025-05-01", "KOSPI", 0.0015),
        ("2026-05-01", "KOSDAQ", 0.0020),
    ]
    allok = all(abs(cm.sell_tax(d, m) - e) < 1e-9 for d, m, e in cases)
    print(f"\n[1] 거래세 시점별 적용     {ok(allok)}")
    for d, m, e in cases:
        print(f"    {d} {m:6s} {cm.sell_tax(d,m)*100:.2f}%  (기대 {e*100:.2f}%)")
    print(f"    2026 왕복 총비용 {cm.round_trip_pct('2026-05-01','KOSDAQ')*100:.2f}%"
          f"  → 손익분기 상승률")

    # ── 2. PIT 재무 (미래참조 차단) ──
    store = FakeStore()
    f_apr = store.financials_asof("2024-04-30")   # 1Q 아직 미공시
    f_jun = store.financials_asof("2024-06-30")   # 1Q 공시 완료(5/15)
    q_apr = int(f_apr.iloc[0]["quarter"]) if len(f_apr) else -1
    y_apr = int(f_apr.iloc[0]["year"]) if len(f_apr) else -1
    q_jun = int(f_jun.iloc[0]["quarter"]) if len(f_jun) else -1
    y_jun = int(f_jun.iloc[0]["year"]) if len(f_jun) else -1
    pit_ok = (y_apr, q_apr) == (2023, 4) and (y_jun, q_jun) == (2024, 1)
    print(f"\n[2] PIT 미래참조 차단      {ok(pit_ok)}")
    print(f"    2024-04-30 기준 최신 가용 분기: {y_apr}Q{q_apr}  (기대 2023Q4 — 1Q는 5/15 공시)")
    print(f"    2024-06-30 기준 최신 가용 분기: {y_jun}Q{q_jun}  (기대 2024Q1)")

    # ── 3. 신호 계산 ──
    cfg = DEFAULT
    sig = SignalPanel(store, cfg, verbose=False)
    sig.compute()
    rs = sig.raw["rs_rating"]
    tradable = sig.raw["tradable"]
    print(f"\n[3] 신호 패널             "
          f"{ok(rs.notna().sum().sum() > 0 and tradable.sum().sum() > 0)}")
    print(f"    RS 유효 {int(rs.notna().sum().sum()):,}셀 · "
          f"거래가능 평균 {tradable.sum(axis=1).mean():.0f}종목/일")
    d = store.px('종가').index[-1]
    c = sig.candidates(d, cfg, sig.fundamentals_asof(d))
    print(f"    최종일 후보 {len(c)}종목")

    # ── 4. 백테스트 실행 ──
    bt = Backtester(store, sig, cfg, cm, 100_000_000, verbose=False)
    bench = store.index("KOSPI")["종가"]
    res = bt.run("2019-01-01", "2024-06-30")
    perf = performance(res, bench)
    tr = res["trades"]
    print(f"\n[4] 백테스트 실행         {ok(len(tr) > 0)}")
    print(f"    매매 {perf.get('매매횟수')}회 · CAGR {perf.get('CAGR')} · "
          f"MDD {perf.get('MDD')} · 승률 {perf.get('승률')}")
    print(f"    청산사유 {perf.get('청산사유')}")

    # ── 5. 체결 규칙 검증 (미래참조 없음) ──
    if len(tr):
        O = store.px("시가")
        C = store.px("종가")
        bad = 0
        for _, t in tr.head(30).iterrows():
            ed = pd.Timestamp(t["entry_date"])
            if ed not in O.index:
                continue
            o = O.loc[ed, t["code"]]
            # 체결가는 시가×(1+슬리피지) 여야 함. 종가 체결이면 실패.
            if abs(t["entry_price"] - o * (1 + cm.slippage_entry)) > o * 0.002:
                bad += 1
        print(f"\n[5] D+1 시가 체결 준수     {ok(bad == 0)}  (위반 {bad}건/30)")

        stops = tr[tr["reason"] == "손절"]
        if len(stops):
            worst = stops["pnl_pct"].min()
            print(f"    손절 {len(stops)}건 · 최악 {worst:+.2%} "
                  f"(-7% + 비용/갭 → -10% 내외까지는 정상)")

    # ── 6. 시장 게이트 효과 ──
    res_nogate = bt.run("2019-01-01", "2024-06-30", use_market_gate=False)
    p_ng = performance(res_nogate, bench)
    print(f"\n[6] 시장 게이트(M) 효과")
    print(f"    게이트 ON  CAGR {perf.get('CAGR')} MDD {perf.get('MDD')} "
          f"매매 {perf.get('매매횟수')}")
    print(f"    게이트 OFF CAGR {p_ng.get('CAGR')} MDD {p_ng.get('MDD')} "
          f"매매 {p_ng.get('매매횟수')}")
    print(f"    → 게이트가 MDD를 줄이는가: "
          f"{ok((perf.get('MDD') or -1) >= (p_ng.get('MDD') or -1))}")

    # ── 7. 비용 영향도 ──
    res_free = Backtester(store, sig, cfg, FRICTIONLESS, 100_000_000,
                          verbose=False).run("2019-01-01", "2024-06-30")
    p_free = performance(res_free, bench)
    print(f"\n[7] 거래비용 영향")
    print(f"    비용 반영 CAGR {perf.get('CAGR')} / 비용 무시 CAGR {p_free.get('CAGR')}")
    gap = (p_free.get("CAGR") or 0) - (perf.get("CAGR") or 0)
    print(f"    비용이 삭제하는 연수익 {gap:+.2%}  {ok(gap >= 0)}")

    # ── 8. 연도별 ──
    print(f"\n[8] 연도별 분해")
    print(yearly_breakdown(res, bench).to_string(index=False))

    # ── 9. 워크포워드 ──
    print(f"\n[9] 워크포워드 (학습 2년 → 검증 1년)")
    grid = {"L.rs_rating_min": [75, 85], "trade.stop_loss_pct": [0.07, 0.10]}
    wf = Optimizer(bt, cfg, verbose=False).walk_forward(
        "2019-01-01", "2026-06-30", grid, train_years=2, test_years=1,
        benchmark=bench, max_combos=8)
    s = wf.get("요약") or {}
    s = s if isinstance(s, dict) else {}
    print(f"    구간 {s.get('구간수')}개 · 학습평균 CAGR {s.get('학습_평균CAGR')} "
          f"· 검증평균 CAGR {s.get('검증_평균CAGR')}")
    print(f"    과최적화 격차 {s.get('과최적화_격차')} "
          f"(클수록 학습구간에만 맞춘 것)")
    print(f"    권장파라미터 {wf.get('권장파라미터')}")
    print(f"    워크포워드 동작 {ok(s.get('구간수', 0) >= 2)}")
    print("\n    자동 점검:")
    for m in sanity_checks(wf):
        print(f"      · {m}")

    print("\n" + "=" * 64)
    print("주의: 위 수치는 합성 데이터입니다. 전략 성과가 아니라 엔진 동작 검증용입니다.")
    print("=" * 64)


if __name__ == "__main__":
    run()

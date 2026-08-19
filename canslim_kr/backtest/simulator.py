"""
simulator.py — 이벤트 드리븐 매매 시뮬레이터

체결 규칙 (여기서 대충 하면 백테스트가 거짓말을 합니다)
  · 신호는 D일 종가로 판정하고, 체결은 D+1일 시가에 합니다.
    D일 종가로 신호를 내고 D일 종가에 체결하면 그 자체가 미래 참조입니다.
  · 손절은 장중 저가가 손절선을 뚫으면 그 가격에 체결된 것으로 봅니다.
    단, 시초가가 이미 손절선 아래면 시초가로 체결 (갭하락 반영).
  · 상한가·하한가에서는 체결되지 않는다고 가정합니다 (한국 ±30%).
  · 거래대금 대비 주문이 과도하면 부분 체결시킵니다 (시장충격 반영).

매도 규칙 (오닐 원본)
  · 손절 -7~8%: 절대 원칙. 예외 없음.
  · 익절 +20~25%
  · 8주 보유 룰: 3주 내 +20% 이상이면 최소 8주 보유 (대박 종목을 일찍 팔지 않기)
  · 50일선 대량 거래 이탈 시 청산
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any

import numpy as np
import pandas as pd

from ..config import CanslimKRConfig, DEFAULT
from .. import market as mkt
from .costs import CostModel
from .panel import PanelStore
from .signals import SignalPanel


@dataclass
class Position:
    code: str
    market: str
    entry_date: pd.Timestamp
    entry_price: float          # 비용 포함 실체결가
    qty: int
    stop: float
    target1: float
    pivot: float
    score: float
    peak: float = 0.0
    eight_week_until: Optional[pd.Timestamp] = None

    def value(self, px: float) -> float:
        return px * self.qty


@dataclass
class Trade:
    code: str
    market: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    qty: int
    pnl: float
    pnl_pct: float
    hold_days: int
    reason: str
    score: float
    mae: float = 0.0            # 보유 중 최대 미실현 손실
    mfe: float = 0.0            # 보유 중 최대 미실현 이익


class Backtester:
    def __init__(self, store: PanelStore, sig: SignalPanel,
                 cfg: CanslimKRConfig = DEFAULT,
                 costs: Optional[CostModel] = None,
                 initial_capital: float = 100_000_000,
                 verbose: bool = True):
        self.store = store
        self.sig = sig
        self.cfg = cfg
        self.costs = costs or CostModel()
        self.capital0 = initial_capital
        self.verbose = verbose
        self._market_cache: Dict[pd.Timestamp, Dict[str, str]] = {}

    def log(self, *a):
        if self.verbose:
            print(*a, flush=True)

    # ─────────────────────────────────────────────────────────
    def _market_states(self, dates: pd.DatetimeIndex, step: int = 5) -> pd.DataFrame:
        """
        지수별 시장 상태를 step 거래일마다 재판정합니다.
        매일 재판정해도 상태는 거의 안 바뀌는데 계산은 5배 늘어납니다.
        """
        out = {}
        for name in ("KOSPI", "KOSDAQ"):
            try:
                idx = self.store.index(name)
            except Exception:
                continue
            states, exposure = {}, {}
            cur, curexp = "CONFIRMED_UPTREND", 1.0
            for i, d in enumerate(dates):
                if i % step == 0:
                    hist = idx.loc[:d]
                    if len(hist) > 210:
                        ms = mkt.assess_market(hist, self.cfg, name)
                        cur, curexp = ms.state, ms.max_exposure
                states[d], exposure[d] = cur, curexp
            out[f"{name}_state"] = pd.Series(states)
            out[f"{name}_exposure"] = pd.Series(exposure)
        return pd.DataFrame(out).reindex(dates).ffill()

    # ─────────────────────────────────────────────────────────
    def run(self, start: str, end: str, cfg: Optional[CanslimKRConfig] = None,
            rebalance_every: int = 1, use_market_gate: bool = True,
            use_fundamentals: bool = True) -> Dict[str, Any]:
        cfg = cfg or self.cfg
        t = cfg.trade
        C = self.store.px("종가")
        O = self.store.px("시가")
        H = self.store.px("고가")
        L = self.store.px("저가")
        TV = self.store.px("거래대금")

        dates = C.loc[start:end].index
        if len(dates) < 60:
            raise ValueError("기간이 너무 짧습니다")
        mkt_of = self.store.market_of()

        self.log(f"■ 백테스트 {dates[0].date()} ~ {dates[-1].date()} ({len(dates):,}거래일)")
        states = self._market_states(dates) if use_market_gate else None

        cash = self.capital0
        positions: Dict[str, Position] = {}
        trades: List[Trade] = []
        equity_curve, exposure_curve = [], []
        pending: List[dict] = []            # D일 신호 → D+1일 시가 체결 대기열
        fund_cache: Dict[str, pd.DataFrame] = {}
        last_fund_key = None

        for di, d in enumerate(dates):
            px_c = C.loc[d]
            px_o = O.loc[d]
            px_h = H.loc[d]
            px_l = L.loc[d]

            # ── 1. 대기 주문 체결 (전일 신호 → 오늘 시가) ──
            for order in pending:
                code = order["code"]
                op = px_o.get(code)
                if op is None or not np.isfinite(op) or op <= 0:
                    continue
                # 시초가가 매수구간을 이미 벗어났으면 추격하지 않습니다
                if op > order["pivot"] * (1 + cfg.N.buy_zone_pct):
                    continue
                fill = self.costs.buy_price(op)
                qty = order["qty"]
                # 시장충격: 당일 거래대금의 1%를 넘는 주문은 잘라냅니다
                tv = TV.loc[d].get(code, np.nan)
                if np.isfinite(tv) and tv > 0:
                    qty = min(qty, int(tv * 0.01 / fill))
                cost = fill * qty * (1 + self.costs.commission)
                if qty <= 0 or cost > cash:
                    continue
                cash -= cost
                positions[code] = Position(
                    code=code, market=str(mkt_of.get(code, "KOSPI")),
                    entry_date=d, entry_price=fill, qty=qty,
                    stop=fill * (1 - t.stop_loss_pct),
                    target1=fill * (1 + t.take_profit_1),
                    pivot=order["pivot"], score=order["score"], peak=fill,
                )
            pending = []

            # ── 2. 청산 판정 ──
            for code in list(positions.keys()):
                p = positions[code]
                o, h, l, c = (px_o.get(code), px_h.get(code),
                              px_l.get(code), px_c.get(code))
                if c is None or not np.isfinite(c):
                    continue
                p.peak = max(p.peak, h if np.isfinite(h) else c)
                held = (d - p.entry_date).days

                exit_px, reason, is_stop = None, None, False

                # 손절 — 갭하락이면 시초가로 체결
                if np.isfinite(l) and l <= p.stop:
                    exit_px = o if (np.isfinite(o) and o < p.stop) else p.stop
                    reason, is_stop = "손절", True

                # 8주 보유 룰: 3주 내 +20%면 익절을 미룹니다
                if exit_px is None and p.eight_week_until is None:
                    if held <= t.eight_week_rule_days * 1.5 and \
                       np.isfinite(h) and h >= p.entry_price * (1 + t.eight_week_rule_gain):
                        p.eight_week_until = d + pd.Timedelta(days=56)

                if exit_px is None and np.isfinite(h) and h >= p.target1:
                    if p.eight_week_until is None or d >= p.eight_week_until:
                        exit_px, reason = p.target1, "목표달성"

                # 8주 룰 종료 후 추적 청산
                if exit_px is None and p.eight_week_until and d >= p.eight_week_until:
                    ma50 = self.sig.raw["ma50"].loc[d].get(code, np.nan)
                    if np.isfinite(ma50) and c < ma50:
                        exit_px, reason = c, "8주룰 종료·50일선 이탈"

                # 50일선 이탈 (대량 거래 동반)
                if exit_px is None:
                    ma50 = self.sig.raw["ma50"].loc[d].get(code, np.nan)
                    vr = self.sig.raw["vol_ratio"].loc[d].get(code, np.nan)
                    if np.isfinite(ma50) and c < ma50 * 0.98 and \
                       np.isfinite(vr) and vr > 1.3 and held > 10:
                        exit_px, reason = c, "50일선 대량이탈"

                # 상장폐지·거래정지 방어
                if exit_px is None and not self.sig.raw["listed"].loc[d].get(code, True):
                    exit_px, reason = c, "상장폐지"

                if exit_px is not None:
                    proceeds = self.costs.sell_proceeds(exit_px, p.qty, d, p.market, is_stop)
                    cost_basis = p.entry_price * p.qty * (1 + self.costs.commission)
                    pnl = proceeds - cost_basis
                    cash += proceeds
                    trades.append(Trade(
                        code=code, market=p.market,
                        entry_date=str(p.entry_date.date()), exit_date=str(d.date()),
                        entry_price=round(p.entry_price, 1),
                        exit_price=round(self.costs.sell_price(exit_px, is_stop), 1),
                        qty=p.qty, pnl=round(pnl, 0),
                        pnl_pct=round(pnl / cost_basis, 4),
                        hold_days=(d - p.entry_date).days, reason=reason,
                        score=round(p.score, 1),
                        mae=round(min(0.0, (p.stop / p.entry_price - 1)), 4),
                        mfe=round(p.peak / p.entry_price - 1, 4),
                    ))
                    del positions[code]

            # ── 3. 신규 진입 신호 (오늘 종가 기준 → 내일 시가 체결) ──
            equity = cash + sum(p.value(px_c.get(p.code, p.entry_price))
                                for p in positions.values())
            equity_curve.append({"date": d, "equity": equity, "cash": cash,
                                 "positions": len(positions)})
            exposure_curve.append(1 - cash / equity if equity > 0 else 0)

            if di % rebalance_every != 0 or di >= len(dates) - 1:
                continue
            if len(positions) >= t.max_positions:
                continue

            gate_exp = 1.0
            if states is not None:
                st = states.loc[d]
                gate_exp = float(st.get("KOSPI_exposure", 1.0))
                if gate_exp <= 0:
                    continue    # 시장 조정 → 신규 매수 없음

            fund = None
            if use_fundamentals:
                key = f"{d.year}Q{(d.month-1)//3+1}"
                if key != last_fund_key:
                    fund_cache[key] = self.sig.fundamentals_asof(d)
                    last_fund_key = key
                fund = fund_cache.get(key)

            try:
                cand = self.sig.candidates(d, cfg, fund)
            except Exception:
                continue
            if cand is None or cand.empty:
                continue

            # 돌파 당일 + 거래량 확인 종목만
            cand = cand[cand["breakout"].fillna(False)
                        & (cand["vol_ratio"] >= cfg.S.breakout_volume_ratio)]
            cand = cand[~cand.index.isin(positions.keys())]
            if cand.empty:
                continue

            slots = t.max_positions - len(positions)
            for code, r in cand.head(slots).iterrows():
                pos_pct = t.max_position_pct * gate_exp
                budget = min(equity * pos_pct, cash * 0.95)
                px = px_c.get(code)
                if not np.isfinite(px) or px <= 0 or budget < px:
                    continue
                # 리스크 기준 수량과 비중 기준 수량 중 작은 쪽
                risk_qty = int((equity * t.risk_per_trade) / (px * t.stop_loss_pct))
                qty = max(0, min(int(budget / px), risk_qty))
                if qty <= 0:
                    continue
                pending.append({"code": code, "qty": qty,
                                "pivot": float(r["pivot"]) if np.isfinite(r["pivot"]) else px,
                                "score": float(r["score"])})

        # ── 종료: 잔여 포지션 청산 ──
        d = dates[-1]
        for code, p in list(positions.items()):
            c = C.loc[d].get(code, p.entry_price)
            proceeds = self.costs.sell_proceeds(c, p.qty, d, p.market)
            cost_basis = p.entry_price * p.qty * (1 + self.costs.commission)
            cash += proceeds
            trades.append(Trade(
                code=code, market=p.market, entry_date=str(p.entry_date.date()),
                exit_date=str(d.date()), entry_price=round(p.entry_price, 1),
                exit_price=round(c, 1), qty=p.qty,
                pnl=round(proceeds - cost_basis, 0),
                pnl_pct=round((proceeds - cost_basis) / cost_basis, 4),
                hold_days=(d - p.entry_date).days, reason="기간종료",
                score=round(p.score, 1), mfe=round(p.peak / p.entry_price - 1, 4),
            ))

        eq = pd.DataFrame(equity_curve).set_index("date")
        eq["exposure"] = exposure_curve
        return {
            "equity": eq,
            "trades": pd.DataFrame([asdict(x) for x in trades]),
            "start": str(dates[0].date()), "end": str(dates[-1].date()),
            "initial_capital": self.capital0,
            "final_equity": float(eq["equity"].iloc[-1]),
        }

"""
signals.py — 과거 전 구간 CANSLIM 신호의 벡터 계산

2,500 거래일 × 2,000 종목을 하루씩 돌면서 종목마다 detect_base()를 부르면
수십억 번 연산이라 며칠이 걸립니다. 그래서 백테스트에서는
날짜×종목 행렬 전체를 한 번에 계산합니다.

engine.py와의 차이 (중요)
  · engine.py는 오늘 기준 1종목을 '정밀하게' 봅니다 — 손잡이 형태, 이중바닥 등
  · signals.py는 과거 전 구간을 '빠르게' 봅니다 — 피벗 돌파를 근사식으로 판정
  근사식: 최근 N일 고점(직전 구간 제외)을 상향 돌파 + 그 구간이 베이스 깊이 조건 충족

  이 근사는 컵/이중바닥/평평한 베이스를 구분하지 않습니다.
  구분이 필요하면 exact_base=True로 두면 후보에만 정밀 탐지를 겁니다(느림).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from ..config import CanslimKRConfig
from .panel import PanelStore


def _roc(px: pd.DataFrame, n: int) -> pd.DataFrame:
    return px / px.shift(n) - 1.0


class SignalPanel:
    """
    날짜×종목 신호 행렬 묶음.
    한 번 계산해두면 파라미터 최적화 시 재활용됩니다
    (임계값만 바뀌고 원지표는 그대로이므로).
    """

    def __init__(self, store: PanelStore, cfg: CanslimKRConfig, verbose: bool = True):
        self.store = store
        self.cfg = cfg
        self.verbose = verbose
        self.raw: Dict[str, pd.DataFrame] = {}

    def log(self, *a):
        if self.verbose:
            print(*a, flush=True)

    # ─────────────────────────────────────────────────────────
    def compute(self, min_history: int = 260) -> Dict[str, pd.DataFrame]:
        """원지표를 전부 계산합니다. 임계값은 아직 적용하지 않습니다."""
        C = self.store.px("종가")
        H = self.store.px("고가")
        L = self.store.px("저가")
        V = self.store.px("거래량")
        TV = self.store.px("거래대금")
        dates, codes = C.index, C.columns.tolist()

        self.log(f"■ 신호 계산: {len(dates):,}일 × {len(codes):,}종목")

        # ── L: RS Rating (IBD 산식, 유니버스 백분위) ──
        w = self.cfg.L.rs_weights
        p = self.cfg.L.rs_periods
        rs_raw = sum(wi * _roc(C, pi) for wi, pi in zip(w, p)) / sum(w)
        listed = self.store.listed_mask(dates, codes)
        rs_raw = rs_raw.where(listed)                       # 미상장 구간 제외
        rs_rating = rs_raw.rank(axis=1, pct=True) * 98 + 1
        self.raw["rs_raw"] = rs_raw
        self.raw["rs_rating"] = rs_rating
        self.log("  RS Rating 완료")

        # ── N: 신고가·이평 ──
        hi252 = H.rolling(252, min_periods=120).max()
        lo252 = L.rolling(252, min_periods=120).min()
        self.raw["pct_of_high"] = C / hi252
        self.raw["off_low"] = C / lo252 - 1.0
        for n in (50, 150, 200):
            self.raw[f"ma{n}"] = C.rolling(n, min_periods=n // 2).mean()
        self.raw["above_ma50"] = C > self.raw["ma50"]
        self.raw["above_ma200"] = C > self.raw["ma200"]
        self.raw["ma50_over_200"] = self.raw["ma50"] > self.raw["ma200"]
        self.log("  신고가·이평 완료")

        # ── 피벗 돌파 근사 ──
        # 피벗 = 직전 base_lookback일 고점 (최근 handle_gap일 제외).
        # 최근 며칠을 빼는 이유: 돌파 당일 자신의 고가가 피벗이 되는 자기참조를 막기 위함.
        bl = int(self.cfg.base.min_weeks * 5 * 2)          # 기본 50거래일
        gap = self.cfg.base.handle_min_days
        pivot = H.shift(gap).rolling(bl, min_periods=bl // 2).max()
        base_low = L.shift(gap).rolling(bl, min_periods=bl // 2).min()
        depth = 1 - base_low / pivot
        self.raw["pivot"] = pivot
        self.raw["base_depth"] = depth
        self.raw["depth_ok"] = (depth >= self.cfg.base.cup_depth_min * 0.5) & \
                               (depth <= self.cfg.base.cup_depth_max)
        # 사전 상승: 베이스 시작 이전 120일 저점 대비 피벗 상승률
        pre_low = L.shift(bl + gap).rolling(self.cfg.base.prior_uptrend_lookback,
                                            min_periods=60).min()
        self.raw["prior_up"] = pivot / pre_low - 1.0

        crossed = (C > pivot) & (C.shift(1) <= pivot.shift(1))
        self.raw["breakout"] = crossed
        self.raw["above_pivot_pct"] = C / pivot - 1.0
        self.log("  피벗·돌파 완료")

        # ── S: 거래량 ──
        v50 = V.rolling(50, min_periods=25).mean()
        self.raw["vol_ratio"] = V / v50.shift(1)
        chg = C.pct_change()
        upv = V.where(chg > 0, 0).rolling(50, min_periods=25).sum()
        dnv = V.where(chg < 0, 0).rolling(50, min_periods=25).sum()
        self.raw["ud_vol"] = upv / dnv.replace(0, np.nan)
        self.raw["turnover20"] = TV.rolling(20, min_periods=10).mean()
        self.log("  거래량 완료")

        # ── I: 기관·외국인 수급 ──
        cap = self.store.cap_panel(dates, codes)
        self.raw["market_cap"] = cap
        for kind in ("기관", "외국인"):
            fl = self.store.flow(kind)
            if fl is None:
                continue
            fl = fl.reindex(index=dates, columns=codes)
            self.raw[f"{kind}_20d"] = fl.rolling(20, min_periods=10).sum() / cap
            self.raw[f"{kind}_60d"] = fl.rolling(60, min_periods=30).sum() / cap
        self.log("  수급 완료")

        # ── 유동성·상장 마스크 ──
        self.raw["listed"] = listed
        self.raw["tradable"] = (
            listed
            & (C >= self.cfg.universe.min_price)
            & (self.raw["turnover20"] >= self.cfg.universe.min_avg_turnover_20d)
            & (cap >= self.cfg.universe.min_market_cap)
            & C.notna()
            & (C.rolling(min_history, min_periods=min_history).count() >= min_history)
        )
        self.log(f"  거래가능 평균 {self.raw['tradable'].sum(axis=1).mean():.0f}종목/일")
        return self.raw

    # ─────────────────────────────────────────────────────────
    def fundamentals_asof(self, date) -> pd.DataFrame:
        """PIT 재무 (미래 참조 차단은 PanelStore가 담당)."""
        return self.store.financials_asof(date)

    # ─────────────────────────────────────────────────────────
    def candidates(self, date, cfg: Optional[CanslimKRConfig] = None,
                   fund: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        """
        특정일의 매수 후보를 점수 순으로 반환합니다.
        cfg를 따로 주면 같은 원지표로 다른 임계값을 시험할 수 있습니다
        (최적화 루프가 이걸 이용해 원지표 재계산 없이 빠르게 돕니다).
        """
        cfg = cfg or self.cfg
        r = self.raw
        try:
            row = {k: v.loc[date] for k, v in r.items() if isinstance(v, pd.DataFrame)}
        except KeyError:
            return pd.DataFrame()

        m = row["tradable"].fillna(False)
        if not m.any():
            return pd.DataFrame()

        df = pd.DataFrame({
            "rs": row["rs_rating"], "pct_high": row["pct_of_high"],
            "off_low": row["off_low"], "pivot": row["pivot"],
            "depth": row["base_depth"], "depth_ok": row["depth_ok"],
            "prior_up": row["prior_up"], "breakout": row["breakout"],
            "above_pivot": row["above_pivot_pct"],
            "vol_ratio": row["vol_ratio"], "ud_vol": row["ud_vol"],
            "above_ma50": row["above_ma50"], "ma50_over_200": row["ma50_over_200"],
            "inst20": row.get("기관_20d", pd.Series(np.nan, index=m.index)),
            "for20": row.get("외국인_20d", pd.Series(np.nan, index=m.index)),
        })[m]

        # ── 하드 필터 (오닐의 '반드시') ──
        f = (
            (df["rs"] >= cfg.L.rs_rating_min)
            & (df["pct_high"] >= cfg.N.pct_of_52w_high_min)
            & (df["off_low"] >= cfg.N.min_off_52w_low)
            & df["above_ma50"].fillna(False)
            & df["ma50_over_200"].fillna(False)
            & df["depth_ok"].fillna(False)
            & (df["prior_up"] >= cfg.base.prior_uptrend_min)
        )
        df = df[f]
        if df.empty:
            return df

        # ── 재무 병합 (있으면) ──
        if fund is not None and not fund.empty:
            df = df.join(fund[["ni_yoy", "rev_yoy", "turnaround", "roe_ttm"]], how="left")
            keep = (
                (df["ni_yoy"] >= cfg.C.eps_yoy_min)
                | df["turnaround"].fillna(False)
                | df["ni_yoy"].isna()          # 미공시는 통과시키되 아래에서 감점
            )
            df = df[keep]
        else:
            for c in ("ni_yoy", "rev_yoy", "turnaround", "roe_ttm"):
                df[c] = np.nan

        if df.empty:
            return df

        # ── 점수 (engine.py의 가중치를 축약 적용) ──
        def norm(s, lo, hi):
            return ((s - lo) / (hi - lo)).clip(0, 1).fillna(0.5)

        w = cfg.scoring.weights
        score = (
            w["L"] * norm(df["rs"], cfg.L.rs_rating_min, 99)
            + w["N"] * norm(df["pct_high"], cfg.N.pct_of_52w_high_min, 1.0)
            + w["S"] * (0.5 * norm(df["ud_vol"], 0.8, cfg.S.up_down_volume_ratio_strong)
                        + 0.5 * norm(df["vol_ratio"], 0.8, cfg.S.breakout_volume_strong))
            + w["I"] * (0.5 * norm(df["inst20"], 0, cfg.I.inst_net_buy_ratio_strong)
                        + 0.5 * norm(df["for20"], 0, cfg.I.foreign_net_buy_ratio_strong))
            + w["C"] * norm(df["ni_yoy"], cfg.C.eps_yoy_min, cfg.C.eps_yoy_strong)
            + w["A"] * norm(df["roe_ttm"], cfg.A.roe_min, cfg.A.roe_strong)
        ) / sum(w.values()) * 100

        df["score"] = score
        df.loc[df["ni_yoy"].isna() & ~df["turnaround"].fillna(False), "score"] *= 0.92
        return df.sort_values("score", ascending=False)

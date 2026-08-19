"""
optimizer.py — 임계값 최적화

여기가 백테스트에서 가장 위험한 부분입니다.
전 구간에 대해 그리드 서치를 돌려 최고 CAGR을 고르면
그건 '과거를 가장 잘 설명하는 값'이지 '미래에 통하는 값'이 아닙니다.
(파라미터 6개 × 각 5수준 = 15,625 조합 중 최고를 고르면
 순전히 우연으로도 훌륭한 성과가 나옵니다.)

그래서 두 가지 장치를 씁니다.

1. 워크포워드 (walk-forward)
   학습구간에서 최적화 → 그 다음 검증구간에서 그대로 실행 → 창을 밀며 반복.
   검증구간 성과만 이어붙인 것이 '실제로 기대할 수 있는 성과'입니다.
   학습구간 성과와 검증구간 성과의 격차가 곧 과최적화 정도입니다.

2. 안정성 우선 선택
   최고점 파라미터가 아니라, 이웃 파라미터들도 함께 좋은 '고원(plateau)'의
   중심을 고릅니다. 뾰족한 최고점은 대개 우연입니다.
"""

from __future__ import annotations

import copy
import itertools
import json
from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..config import CanslimKRConfig, DEFAULT
from .metrics import performance
from .simulator import Backtester


# 최적화 대상 파라미터 — 경로와 후보값
# 경로는 config의 중첩 속성을 점으로 표기합니다.
DEFAULT_GRID: Dict[str, List] = {
    "L.rs_rating_min":            [70, 75, 80, 85, 90],
    "N.pct_of_52w_high_min":      [0.80, 0.85, 0.90, 0.95],
    "trade.stop_loss_pct":        [0.05, 0.07, 0.08, 0.10],
    "trade.take_profit_1":        [0.15, 0.20, 0.25, 0.30],
    "S.breakout_volume_ratio":    [1.2, 1.5, 1.8],
    "trade.max_positions":        [4, 6, 8, 10],
    "C.eps_yoy_min":              [0.10, 0.20, 0.25],
    "base.prior_uptrend_min":     [0.15, 0.25, 0.35],
}

# 빠른 탐색용 축소 그리드
QUICK_GRID: Dict[str, List] = {
    "L.rs_rating_min":       [75, 80, 87],
    "trade.stop_loss_pct":   [0.07, 0.08],
    "trade.take_profit_1":   [0.20, 0.25],
    "trade.max_positions":   [5, 8],
}


def set_param(cfg: CanslimKRConfig, path: str, value) -> CanslimKRConfig:
    """'trade.stop_loss_pct' 같은 경로로 중첩 설정을 바꿉니다."""
    c = copy.deepcopy(cfg)
    obj = c
    parts = path.split(".")
    for p in parts[:-1]:
        obj = getattr(obj, p)
    setattr(obj, parts[-1], value)
    return c


def make_objective(min_trades: int = 20, full_confidence_trades: int = 60) -> Callable:
    """
    목적함수 생성기.

    CAGR만 쓰면 MDD 60%짜리 전략이 뽑힙니다.
    칼마 비율(CAGR/MDD)을 축으로 하되, 표본이 적은 결과는 깎습니다.

    min_trades는 백테스트 창 길이에 맞춰 조정하세요.
    10년 구간이면 20회가 적당하지만, 워크포워드 학습창이 2년이면
    20회는 과도해서 모든 조합이 탈락합니다 (창 1년당 5회 정도가 기준선).
    """
    def _obj(perf: Dict[str, Any]) -> float:
        n = perf.get("매매횟수", 0)
        if n < min_trades:
            return -99.0                   # 표본 부족 — 통계적으로 의미 없음
        cagr = perf.get("CAGR", 0) or 0
        mdd = abs(perf.get("MDD", 0) or 1e-9)
        exp = perf.get("기대값", 0) or 0
        if exp <= 0:
            return -50.0                   # 기대값 음수 = 전략이 죽음
        calmar = cagr / max(mdd, 0.05)
        penalty = min(1.0, n / full_confidence_trades) ** 0.5
        return float(calmar * penalty)
    return _obj


# 기본 목적함수 (10년 내외 구간 기준)
objective_default = make_objective(min_trades=20, full_confidence_trades=60)


class Optimizer:
    def __init__(self, bt: Backtester, base_cfg: CanslimKRConfig = DEFAULT,
                 objective: Callable = objective_default, verbose: bool = True):
        self.bt = bt
        self.base = base_cfg
        self.obj = objective
        self.verbose = verbose

    def log(self, *a):
        if self.verbose:
            print(*a, flush=True)

    # ─────────────────────────────────────────────────────────
    def grid_search(self, start: str, end: str, grid: Dict[str, List],
                    benchmark: Optional[pd.Series] = None,
                    max_combos: int = 400,
                    **run_kwargs) -> pd.DataFrame:
        keys = list(grid.keys())
        combos = list(itertools.product(*[grid[k] for k in keys]))
        if len(combos) > max_combos:
            rng = np.random.default_rng(42)
            idx = rng.choice(len(combos), max_combos, replace=False)
            combos = [combos[i] for i in sorted(idx)]
            self.log(f"  조합 {len(combos):,}개로 무작위 축소 (전체 대비 일부)")

        rows = []
        for i, combo in enumerate(combos):
            cfg = self.base
            for k, v in zip(keys, combo):
                cfg = set_param(cfg, k, v)
            try:
                res = self.bt.run(start, end, cfg=cfg, **run_kwargs)
                perf = performance(res, benchmark)
                rows.append({**dict(zip(keys, combo)),
                             "objective": self.obj(perf),
                             "CAGR": perf.get("CAGR"), "MDD": perf.get("MDD"),
                             "승률": perf.get("승률"), "기대값": perf.get("기대값"),
                             "매매횟수": perf.get("매매횟수"),
                             "샤프": perf.get("샤프")})
            except Exception as e:
                rows.append({**dict(zip(keys, combo)), "objective": -99.0,
                             "error": str(e)[:60]})
            if self.verbose and (i + 1) % 20 == 0:
                self.log(f"  {i+1}/{len(combos)}")
        return pd.DataFrame(rows).sort_values("objective", ascending=False)

    # ─────────────────────────────────────────────────────────
    @staticmethod
    def pick_plateau(df: pd.DataFrame, keys: List[str],
                     top_frac: float = 0.15) -> Dict[str, Any]:
        """
        최고점이 아니라 '좋은 결과가 몰려 있는 구역의 중심'을 고릅니다.

        상위 top_frac 결과들에서 각 파라미터의 최빈값(수치형은 중앙값)을 취합니다.
        최고점 하나만 잡으면 그 값에서 살짝만 벗어나도 성과가 무너지는,
        즉 우연히 좋았던 값을 고르게 됩니다.
        """
        ok = df[df["objective"] > -50]
        if ok.empty:
            return {}
        n = max(3, int(len(ok) * top_frac))
        top = ok.head(n)
        out = {}
        for k in keys:
            vals = top[k].dropna()
            if vals.empty:
                continue
            if pd.api.types.is_numeric_dtype(vals):
                cand = sorted(df[k].dropna().unique())
                med = float(vals.median())
                out[k] = min(cand, key=lambda x: abs(x - med))   # 격자에 스냅
            else:
                out[k] = vals.mode().iloc[0]
        return out

    # ─────────────────────────────────────────────────────────
    def walk_forward(self, start: str, end: str, grid: Dict[str, List],
                     train_years: int = 3, test_years: int = 1,
                     benchmark: Optional[pd.Series] = None,
                     max_combos: int = 200, **run_kwargs) -> Dict[str, Any]:
        """
        학습 train_years → 검증 test_years 를 밀면서 반복합니다.

        반환의 '검증구간 성과'가 실제로 기대할 수 있는 수치입니다.
        학습구간 성과는 참고용이며, 둘의 격차가 과최적화 정도를 보여줍니다.
        """
        keys = list(grid.keys())
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        folds, cursor = [], s
        while cursor + pd.DateOffset(years=train_years + test_years) <= e:
            tr_s = cursor
            tr_e = cursor + pd.DateOffset(years=train_years)
            te_e = min(tr_e + pd.DateOffset(years=test_years), e)
            folds.append((tr_s, tr_e, te_e))
            cursor = cursor + pd.DateOffset(years=test_years)

        if not folds:
            raise ValueError("기간이 짧아 워크포워드 구간을 만들 수 없습니다")

        # 학습창이 짧으면 매매 표본도 적으므로 최소 표본 기준을 창 길이에 맞춥니다.
        # (연 5회 기준, 하한 8회) 이걸 안 하면 짧은 창에서 모든 조합이 탈락합니다.
        prev_obj = self.obj
        if self.obj is objective_default:
            self.obj = make_objective(min_trades=max(8, train_years * 5),
                                      full_confidence_trades=max(20, train_years * 15))
            self.log(f"  학습창 {train_years}년 → 최소 표본 {max(8, train_years*5)}회로 조정")

        self.log(f"■ 워크포워드 {len(folds)}개 구간 "
                 f"(학습 {train_years}년 → 검증 {test_years}년)")

        results = []
        for i, (tr_s, tr_e, te_e) in enumerate(folds):
            self.log(f"\n[{i+1}/{len(folds)}] 학습 {tr_s.date()}~{tr_e.date()} "
                     f"→ 검증 {tr_e.date()}~{te_e.date()}")
            gs = self.grid_search(str(tr_s.date()), str(tr_e.date()), grid,
                                  benchmark, max_combos, **run_kwargs)
            best = self.pick_plateau(gs, keys)
            if not best:
                self.log("  학습구간에서 유효한 파라미터 없음 — 건너뜀")
                continue
            self.log(f"  선택: {best}")

            cfg = self.base
            for k, v in best.items():
                cfg = set_param(cfg, k, v)

            tr_top = gs.iloc[0]
            try:
                te_res = self.bt.run(str(tr_e.date()), str(te_e.date()),
                                     cfg=cfg, **run_kwargs)
                te_perf = performance(te_res, benchmark)
            except Exception as ex:
                self.log(f"  검증 실패: {ex}")
                continue

            results.append({
                "fold": i + 1,
                "학습구간": f"{tr_s.date()}~{tr_e.date()}",
                "검증구간": f"{tr_e.date()}~{te_e.date()}",
                "선택파라미터": best,
                "학습_최고CAGR": tr_top.get("CAGR"),
                "검증_CAGR": te_perf.get("CAGR"),
                "검증_MDD": te_perf.get("MDD"),
                "검증_승률": te_perf.get("승률"),
                "검증_매매수": te_perf.get("매매횟수"),
                "_equity": te_res["equity"]["equity"],
                "_trades": te_res["trades"],
            })
            self.log(f"  학습 최고 CAGR {tr_top.get('CAGR')} → "
                     f"검증 CAGR {te_perf.get('CAGR')} "
                     f"(MDD {te_perf.get('MDD')})")

        self.obj = prev_obj
        if not results:
            return {"folds": [], "요약": {"구간수": 0, "비고": "유효 구간 없음 — "
                    "학습창을 늘리거나 그리드를 완화하세요"}}

        # 검증구간 자산곡선 이어붙이기
        curves = []
        base_val = 1.0
        for r in results:
            c = r["_equity"]
            if len(c) < 2:
                continue
            norm = c / c.iloc[0] * base_val
            curves.append(norm)
            base_val = float(norm.iloc[-1])
        stitched = pd.concat(curves) if curves else pd.Series(dtype=float)
        stitched = stitched[~stitched.index.duplicated(keep="first")].sort_index()

        oos_cagr = np.nan
        if len(stitched) > 2:
            yrs = len(stitched) / 252
            oos_cagr = float(stitched.iloc[-1] ** (1 / yrs) - 1)

        is_cagrs = [r["학습_최고CAGR"] for r in results if r["학습_최고CAGR"] is not None]
        oos_cagrs = [r["검증_CAGR"] for r in results if r["검증_CAGR"] is not None]

        # 파라미터 안정성 — 구간마다 크게 흔들리면 신뢰할 수 없는 신호입니다
        stability = {}
        for k in keys:
            vals = [r["선택파라미터"].get(k) for r in results if k in r["선택파라미터"]]
            if vals and all(isinstance(v, (int, float)) for v in vals):
                stability[k] = {
                    "값들": vals,
                    "중앙값": float(np.median(vals)),
                    "변동계수": round(float(np.std(vals) / (np.mean(vals) + 1e-9)), 3),
                }

        summary = {
            "구간수": len(results),
            "학습_평균CAGR": round(float(np.mean(is_cagrs)), 4) if is_cagrs else None,
            "검증_평균CAGR": round(float(np.mean(oos_cagrs)), 4) if oos_cagrs else None,
            "검증_연결CAGR": round(oos_cagr, 4) if oos_cagr == oos_cagr else None,
            "검증_MDD": round(float((stitched / stitched.cummax() - 1).min()), 4)
                        if len(stitched) > 2 else None,
            "과최적화_격차": round(float(np.mean(is_cagrs) - np.mean(oos_cagrs)), 4)
                             if (is_cagrs and oos_cagrs) else None,
            "검증_양수구간비율": round(float(np.mean([c > 0 for c in oos_cagrs])), 3)
                                if oos_cagrs else None,
        }

        return {
            "folds": [{k: v for k, v in r.items() if not k.startswith("_")}
                      for r in results],
            "요약": summary,
            "파라미터_안정성": stability,
            "검증_자산곡선": stitched,
            "권장파라미터": self._consensus(results, keys),
        }

    @staticmethod
    def _consensus(results: List[dict], keys: List[str]) -> Dict[str, Any]:
        """전 구간에서 반복적으로 선택된 값 = 가장 신뢰할 만한 파라미터."""
        out = {}
        for k in keys:
            vals = [r["선택파라미터"].get(k) for r in results if k in r["선택파라미터"]]
            vals = [v for v in vals if v is not None]
            if not vals:
                continue
            out[k] = (float(np.median(vals)) if isinstance(vals[0], (int, float))
                      else pd.Series(vals).mode().iloc[0])
            if isinstance(vals[0], int):
                out[k] = int(round(out[k]))
        return out

    @staticmethod
    def apply(cfg: CanslimKRConfig, params: Dict[str, Any]) -> CanslimKRConfig:
        for k, v in params.items():
            cfg = set_param(cfg, k, v)
        return cfg

    @staticmethod
    def save(wf: Dict[str, Any], path: str = "walkforward.json"):
        out = {k: v for k, v in wf.items() if k != "검증_자산곡선"}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2, default=str)
        print(f"저장: {path}")


def sanity_checks(wf: Dict[str, Any]) -> List[str]:
    """
    워크포워드 결과가 믿을 만한지 자동 점검합니다.
    아래 경고가 뜨면 파라미터를 실전에 쓰기 전에 다시 봐야 합니다.
    """
    s = wf.get("요약", {})
    msgs = []
    gap = s.get("과최적화_격차")
    if gap is not None and gap > 0.15:
        msgs.append(f"학습-검증 CAGR 격차 {gap:.1%} — 과최적화 의심. 그리드를 줄이세요.")
    oos = s.get("검증_평균CAGR")
    if oos is not None and oos <= 0:
        msgs.append("검증구간 평균 CAGR이 0 이하 — 이 전략은 아직 작동하지 않습니다.")
    pos = s.get("검증_양수구간비율")
    if pos is not None and pos < 0.5:
        msgs.append(f"검증구간 중 수익 구간이 {pos:.0%}뿐 — 특정 국면에만 통하는 전략입니다.")
    mdd = s.get("검증_MDD")
    if mdd is not None and mdd < -0.35:
        msgs.append(f"검증 MDD {mdd:.1%} — 실전에서 규칙을 지키기 어려운 수준입니다.")
    for k, v in (wf.get("파라미터_안정성") or {}).items():
        if v.get("변동계수", 0) > 0.25:
            msgs.append(f"{k} 값이 구간마다 크게 흔들림({v['값들']}) — 신호가 아니라 잡음일 수 있습니다.")
    if not msgs:
        msgs.append("자동 점검 통과. 다만 검증구간 성과가 실전 성과를 보장하지는 않습니다.")
    return msgs

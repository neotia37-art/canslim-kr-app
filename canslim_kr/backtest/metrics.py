"""
metrics.py — 성과 지표

CAGR만 보면 안 됩니다. 오닐 전략은 손절이 잦아서
승률은 낮은데 손익비로 버는 구조라, 지표 세 개를 같이 봐야 합니다.

  · 기대값(expectancy) — 1회 매매당 평균 기대수익률. 이게 음수면 전략이 죽은 겁니다.
  · MDD — 오닐 본인도 "감당 못 할 낙폭이면 규칙을 못 지킨다"고 했습니다.
  · 벤치마크 초과 — KOSPI 대비. 그냥 지수 사는 것보다 못하면 의미가 없습니다.
"""

from __future__ import annotations

from typing import Dict, Optional, Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _cagr(series: pd.Series) -> float:
    if len(series) < 2 or series.iloc[0] <= 0:
        return 0.0
    years = len(series) / TRADING_DAYS
    if years <= 0:
        return 0.0
    return float((series.iloc[-1] / series.iloc[0]) ** (1 / years) - 1)


def _mdd(series: pd.Series) -> Dict[str, Any]:
    roll = series.cummax()
    dd = series / roll - 1
    i = dd.idxmin()
    peak_i = series.loc[:i].idxmax()
    rec = series.loc[i:]
    recovered = rec[rec >= series.loc[peak_i]]
    return {
        "mdd": float(dd.min()),
        "mdd_date": str(pd.Timestamp(i).date()),
        "peak_date": str(pd.Timestamp(peak_i).date()),
        "recovery_days": int((recovered.index[0] - i).days) if len(recovered) else None,
        "dd_series": dd,
    }


def performance(result: Dict[str, Any], benchmark: Optional[pd.Series] = None,
                rf: float = 0.03) -> Dict[str, Any]:
    eq = result["equity"]["equity"]
    tr = result["trades"]
    ret = eq.pct_change().dropna()

    cagr = _cagr(eq)
    dd = _mdd(eq)
    vol = float(ret.std() * np.sqrt(TRADING_DAYS)) if len(ret) > 1 else 0.0
    downside = ret[ret < 0]
    dvol = float(downside.std() * np.sqrt(TRADING_DAYS)) if len(downside) > 1 else 0.0

    out = {
        "기간": f"{result['start']} ~ {result['end']}",
        "초기자본": result["initial_capital"],
        "최종자산": round(result["final_equity"], 0),
        "총수익률": round(result["final_equity"] / result["initial_capital"] - 1, 4),
        "CAGR": round(cagr, 4),
        "변동성": round(vol, 4),
        "MDD": round(dd["mdd"], 4),
        "MDD시점": dd["mdd_date"],
        "MDD회복일수": dd["recovery_days"],
        "샤프": round((cagr - rf) / vol, 2) if vol > 0 else None,
        "소르티노": round((cagr - rf) / dvol, 2) if dvol > 0 else None,
        "칼마": round(cagr / abs(dd["mdd"]), 2) if dd["mdd"] < 0 else None,
        "평균투자비중": round(float(result["equity"]["exposure"].mean()), 3),
    }

    if tr is not None and len(tr) > 0:
        wins = tr[tr["pnl"] > 0]
        losses = tr[tr["pnl"] <= 0]
        wr = len(wins) / len(tr)
        aw = float(wins["pnl_pct"].mean()) if len(wins) else 0.0
        al = float(losses["pnl_pct"].mean()) if len(losses) else 0.0
        gp = float(wins["pnl"].sum())
        gl = float(abs(losses["pnl"].sum()))
        out.update({
            "매매횟수": len(tr),
            "승률": round(wr, 4),
            "평균수익": round(aw, 4),
            "평균손실": round(al, 4),
            "손익비": round(abs(aw / al), 2) if al != 0 else None,
            "기대값": round(wr * aw + (1 - wr) * al, 4),
            "profit_factor": round(gp / gl, 2) if gl > 0 else None,
            "평균보유일": round(float(tr["hold_days"].mean()), 1),
            "최대수익": round(float(tr["pnl_pct"].max()), 4),
            "최대손실": round(float(tr["pnl_pct"].min()), 4),
            "연간매매수": round(len(tr) / (len(eq) / TRADING_DAYS), 1),
        })
        out["청산사유"] = tr["reason"].value_counts().to_dict()
    else:
        out["매매횟수"] = 0

    if benchmark is not None and len(benchmark) > 1:
        b = benchmark.reindex(eq.index).ffill().dropna()
        if len(b) > 1:
            bn = b / b.iloc[0] * result["initial_capital"]
            bcagr = _cagr(bn)
            bdd = _mdd(bn)
            out["벤치마크"] = {
                "CAGR": round(bcagr, 4),
                "MDD": round(bdd["mdd"], 4),
                "총수익률": round(float(bn.iloc[-1] / bn.iloc[0] - 1), 4),
            }
            out["초과CAGR"] = round(cagr - bcagr, 4)
            br = bn.pct_change().dropna()
            common = ret.index.intersection(br.index)
            if len(common) > 30:
                excess = ret.loc[common] - br.loc[common]
                te = float(excess.std() * np.sqrt(TRADING_DAYS))
                out["정보비율"] = round(float(excess.mean() * TRADING_DAYS / te), 2) if te > 0 else None
    return out


def yearly_breakdown(result: Dict[str, Any],
                     benchmark: Optional[pd.Series] = None) -> pd.DataFrame:
    """연도별 성과. 특정 연도에만 몰린 수익인지 확인합니다."""
    eq = result["equity"]["equity"]
    tr = result["trades"]
    rows = []
    for y, g in eq.groupby(eq.index.year):
        r = float(g.iloc[-1] / g.iloc[0] - 1)
        dd = float((g / g.cummax() - 1).min())
        n = 0
        wr = np.nan
        if tr is not None and len(tr):
            yt = tr[pd.to_datetime(tr["exit_date"]).dt.year == y]
            n = len(yt)
            wr = float((yt["pnl"] > 0).mean()) if n else np.nan
        row = {"연도": y, "수익률": round(r, 4), "MDD": round(dd, 4),
               "매매수": n, "승률": None if np.isnan(wr) else round(wr, 3)}
        if benchmark is not None:
            b = benchmark.reindex(g.index).ffill().dropna()
            if len(b) > 1:
                br = float(b.iloc[-1] / b.iloc[0] - 1)
                row["벤치마크"] = round(br, 4)
                row["초과"] = round(r - br, 4)
        rows.append(row)
    return pd.DataFrame(rows)


def trade_attribution(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    어디서 돈을 벌고 잃었는지 분해합니다.
    오닐 전략이 정상 작동하면 '소수의 큰 수익 + 다수의 작은 손실' 형태여야 합니다.
    """
    tr = result["trades"]
    if tr is None or len(tr) == 0:
        return {}
    s = tr.sort_values("pnl", ascending=False)
    total = float(tr["pnl"].sum())
    top5 = float(s.head(max(1, len(s) // 20))["pnl"].sum())
    return {
        "총손익": round(total, 0),
        "상위5%_기여": round(top5, 0),
        "상위5%_비중": round(top5 / total, 3) if total != 0 else None,
        "수익거래_평균보유일": round(float(tr[tr.pnl > 0]["hold_days"].mean()), 1)
                              if (tr.pnl > 0).any() else None,
        "손실거래_평균보유일": round(float(tr[tr.pnl <= 0]["hold_days"].mean()), 1)
                              if (tr.pnl <= 0).any() else None,
        "점수구간별": (tr.assign(bin=pd.cut(tr["score"], [0, 60, 70, 80, 90, 100]))
                       .groupby("bin", observed=True)
                       .agg(건수=("pnl", "size"), 승률=("pnl", lambda x: round((x > 0).mean(), 3)),
                            평균수익률=("pnl_pct", lambda x: round(x.mean(), 4)))
                       .to_dict("index")),
    }


def report(result: Dict[str, Any], benchmark: Optional[pd.Series] = None) -> str:
    p = performance(result, benchmark)
    y = yearly_breakdown(result, benchmark)
    a = trade_attribution(result)

    L = ["=" * 60, f"백테스트 결과  {p['기간']}", "=" * 60]
    L.append(f"CAGR {p['CAGR']:+.2%}   MDD {p['MDD']:.2%}   샤프 {p.get('샤프')}   칼마 {p.get('칼마')}")
    if "벤치마크" in p:
        L.append(f"벤치마크 CAGR {p['벤치마크']['CAGR']:+.2%}  MDD {p['벤치마크']['MDD']:.2%}"
                 f"  →  초과 {p['초과CAGR']:+.2%}")
    L.append("")
    if p.get("매매횟수"):
        L.append(f"매매 {p['매매횟수']}회 (연 {p['연간매매수']}회) · 승률 {p['승률']:.1%} "
                 f"· 손익비 {p['손익비']} · 기대값 {p['기대값']:+.2%}")
        L.append(f"평균수익 {p['평균수익']:+.2%} / 평균손실 {p['평균손실']:+.2%} "
                 f"· 평균보유 {p['평균보유일']}일 · 평균비중 {p['평균투자비중']:.1%}")
        L.append(f"청산사유: {p['청산사유']}")
        if a.get("상위5%_비중") is not None:
            L.append(f"상위 5% 거래가 총손익의 {a['상위5%_비중']:.1%} 차지")
    L.append("")
    L.append("연도별")
    L.append(y.to_string(index=False))
    return "\n".join(L)

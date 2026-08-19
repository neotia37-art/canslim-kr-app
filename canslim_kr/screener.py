"""
screener.py — 유니버스 스크리닝 & 단일 종목 정밀 분석

사용 흐름
    1) MarketGate  : 오늘 시장이 매수 가능한 국면인가?          (M)
    2) Prefilter   : 2,600여 종목 → 300~600 종목                (유동성/시총/가격)
    3) RS Rating   : 전체 시세 수집 → 백분위 랭크 → RS≥80만 통과 (L)
    4) Technical   : 신고가·이평·베이스 판정                     (N, S)
    5) Fundamental : DART 재무 조회 (여기가 가장 느림 → 마지막)  (C, A)
    6) Flow        : 기관/외국인 수급                            (I)
    7) Rank        : 종합 점수 → 매매 계획

가장 비싼 단계(DART 재무)를 마지막에 두는 것이 핵심입니다.
3단계에서 이미 대상이 수십 개로 줄어 API 호출이 실용적인 수준이 됩니다.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import numpy as np
import pandas as pd

from .config import CanslimKRConfig, DEFAULT
from .datahub import (KRDataHub, last_business_day, build_quarterly_series,
                      classify_disclosures, IDX_KOSPI, IDX_KOSDAQ)
from . import indicators as ind
from . import market as mkt
from . import engine as eng


class CanslimKR:
    """CANSLIM-KR 메인 엔진."""

    def __init__(self, cfg: CanslimKRConfig = DEFAULT,
                 hub: Optional[KRDataHub] = None, verbose: bool = True):
        self.cfg = cfg
        self.hub = hub or KRDataHub(verbose=verbose)
        self.verbose = verbose
        self._markets: Optional[Dict[str, mkt.MarketState]] = None
        self._rs: Optional[pd.DataFrame] = None
        self._listing: Optional[pd.DataFrame] = None
        self._bench: Dict[str, pd.Series] = {}

    def log(self, *a):
        if self.verbose:
            print(*a, flush=True)

    # ─────────────────────────────────────────────────────────
    # M — 시장 게이트
    # ─────────────────────────────────────────────────────────
    def market_gate(self, refresh: bool = False) -> Dict[str, mkt.MarketState]:
        if self._markets is None or refresh:
            self.log("■ 시장 판정 (M)")
            self._markets = mkt.assess_both_markets(self.hub, self.cfg)
            for k, v in self._markets.items():
                self.log(f"  {k}: {v.state} | 분산일 {v.distribution_days}개 "
                         f"| 허용비중 {v.max_exposure:.0%}")
                for c in v.commentary:
                    self.log(f"    - {c}")
        return self._markets

    def market_of(self, market_name: str) -> mkt.MarketState:
        ms = self.market_gate()
        key = "KOSDAQ" if "KOSDAQ" in str(market_name).upper() else "KOSPI"
        return ms.get(key) or ms.get("KOSPI")

    # ─────────────────────────────────────────────────────────
    # 유니버스 사전 필터
    # ─────────────────────────────────────────────────────────
    def prefilter(self, date: Optional[str] = None) -> pd.DataFrame:
        u = self.cfg.universe
        d = date or last_business_day()
        self.log(f"■ 유니버스 사전 필터 ({d})")

        snap = self.hub.market_snapshot(d)
        lst = self.hub.listing()
        df = snap.merge(lst[["Code", "Name"] + [c for c in ("Sector", "Industry") if c in lst.columns]],
                        on="Code", how="left")
        n0 = len(df)

        df = df[df["Market"].isin(u.markets)]
        df = df[df["종가"] >= u.min_price]
        df = df[df["시가총액"] >= u.min_market_cap]

        if u.exclude_preferred:
            df = df[df["Code"].str.endswith("0")]
        if u.exclude_name_keywords:
            pat = "|".join(u.exclude_name_keywords)
            df = df[~df["Name"].fillna("").str.contains(pat, na=False)]
        if u.exclude_holdings:
            pat = "|".join(u.holdings_keywords)
            df = df[~df["Name"].fillna("").str.contains(pat, na=False)]

        # 20일 평균 거래대금은 스냅샷에 없으므로 당일 거래대금으로 1차 근사 후
        # 이후 시세 수집 단계에서 정밀 필터를 겁니다.
        if "거래대금" in df.columns:
            df = df[df["거래대금"] >= u.min_avg_turnover_20d * 0.5]

        df = df.reset_index(drop=True)
        self.log(f"  {n0:,}종목 → {len(df):,}종목")
        return df

    # ─────────────────────────────────────────────────────────
    # L — RS Rating 테이블 (유니버스 전체)
    # ─────────────────────────────────────────────────────────
    def build_rs_table(self, codes: List[str], days: int = 400,
                       refresh: bool = False) -> pd.DataFrame:
        if self._rs is not None and not refresh:
            return self._rs
        end = last_business_day()
        start = (pd.Timestamp(end) - pd.Timedelta(days=int(days * 1.6))).strftime("%Y%m%d")
        self.log(f"■ RS Rating 산출 — {len(codes):,}종목 시세 수집 (최초 1회는 시간이 걸립니다)")
        cm = self.hub.close_matrix(codes, start, end)
        self._rs = ind.rs_rating_table(
            cm, tuple(self.cfg.L.rs_periods), tuple(self.cfg.L.rs_weights))
        self.log(f"  RS 산출 완료: {len(self._rs):,}종목 | "
                 f"RS≥{self.cfg.L.rs_rating_min}: "
                 f"{(self._rs['rs_rating'] >= self.cfg.L.rs_rating_min).sum():,}종목")
        return self._rs

    def sector_ranks(self, uni: pd.DataFrame) -> pd.DataFrame:
        """업종 내 RS 백분위 + 업종 자체 강도."""
        if self._rs is None or "Sector" not in uni.columns:
            return pd.DataFrame()
        df = uni.merge(self._rs, left_on="Code", right_index=True, how="inner")
        df["sector_pct"] = df.groupby("Sector")["rs_raw"].rank(pct=True)
        sec = df.groupby("Sector")["rs_raw"].median().rank(pct=True).rename("sector_strength")
        return df.merge(sec, on="Sector", how="left")

    # ─────────────────────────────────────────────────────────
    # 벤치마크 종가
    # ─────────────────────────────────────────────────────────
    def bench(self, market_name: str) -> pd.Series:
        key = "KOSDAQ" if "KOSDAQ" in str(market_name).upper() else "KOSPI"
        if key not in self._bench:
            code = IDX_KOSDAQ if key == "KOSDAQ" else IDX_KOSPI
            end = last_business_day()
            start = (pd.Timestamp(end) - pd.Timedelta(days=700)).strftime("%Y%m%d")
            idx = self.hub.index_ohlcv(code, start, end)
            self._bench[key] = idx["종가"] if not idx.empty else pd.Series(dtype=float)
        return self._bench[key]

    # ─────────────────────────────────────────────────────────
    # 단일 종목 정밀 분석
    # ─────────────────────────────────────────────────────────
    def analyze(self, code: str, name: Optional[str] = None,
                market_name: Optional[str] = None,
                account_size: Optional[float] = None,
                with_fundamentals: bool = True,
                rs_rating: Optional[int] = None,
                sector_pct: Optional[float] = None,
                sector_strength: Optional[float] = None) -> eng.CanslimReport:
        """
        종목 하나를 CANSLIM 7항목으로 완전 분석합니다.
        with_fundamentals=False면 DART 호출을 건너뛰어 훨씬 빠릅니다(기술적 분석만).
        """
        code = str(code).zfill(6)
        end = last_business_day()
        start = (pd.Timestamp(end) - pd.Timedelta(days=760)).strftime("%Y%m%d")

        # 기본 정보
        lst = self.hub.listing()
        row = lst[lst["Code"] == code]
        if name is None:
            name = row["Name"].iloc[0] if len(row) else code
        if market_name is None:
            market_name = row["Market"].iloc[0] if len(row) else "KOSPI"

        df = self.hub.ohlcv(code, start, end)
        if df is None or df.empty or len(df) < 120:
            raise ValueError(f"{code} 시세 데이터 부족")

        warnings_: List[str] = []
        ms = self.market_of(market_name)

        # ── 베이스 ──
        base = ind.detect_base(df, self.cfg)
        base.stage = ind.base_stage(df["종가"], self.cfg)

        # ── L ──
        if rs_rating is None and self._rs is not None and code in self._rs.index:
            rs_rating = int(self._rs.loc[code, "rs_rating"])
        b = self.bench(market_name)
        rsl_high = ind.rs_line_new_high(df["종가"], b, self.cfg.L.rs_line_new_high_lookback) \
            if len(b) > 0 else None
        f_L = eng.score_L(self.cfg, rs_rating, sector_pct, sector_strength, rsl_high)

        # ── N ──
        f_N = eng.score_N(df, self.cfg, base)

        # ── S ──
        snap = self.hub.market_snapshot(end)
        srow = snap[snap["Code"] == code]
        shares = float(srow["상장주식수"].iloc[0]) if len(srow) and "상장주식수" in srow else None
        mcap = float(srow["시가총액"].iloc[0]) if len(srow) else None

        disc = None
        if with_fundamentals and self.hub.dart_key:
            try:
                dd = self.hub.disclosures(code, self.cfg.S.dilution_lookback_days)
                disc = classify_disclosures(dd)
            except Exception as e:
                warnings_.append(f"공시 조회 실패: {e}")

        short_ratio = None
        try:
            sb = self.hub.shorting_balance(code, (pd.Timestamp(end) - pd.Timedelta(days=30)).strftime("%Y%m%d"), end)
            if not sb.empty and shares:
                col = "공매도잔고수량" if "공매도잔고수량" in sb.columns else sb.columns[0]
                short_ratio = float(sb[col].iloc[-1]) / shares
        except Exception:
            pass

        f_S = eng.score_S(df, self.cfg, shares, disc, short_ratio)

        # ── I ──
        flow_start = (pd.Timestamp(end) - pd.Timedelta(days=140)).strftime("%Y%m%d")
        try:
            flow = self.hub.investor_flow(code, flow_start, end)
        except Exception as e:
            flow, warnings_ = pd.DataFrame(), warnings_ + [f"수급 조회 실패: {e}"]
        try:
            fh = self.hub.foreign_holding(code, flow_start, end)
        except Exception:
            fh = pd.DataFrame()
        f_I = eng.score_I(flow, self.cfg, mcap, fh)

        # ── C, A ──
        if with_fundamentals and self.hub.dart_key:
            try:
                q = build_quarterly_series(self.hub, code, years=4,
                                           prefer_consolidated=self.cfg.C.prefer_consolidated)
            except Exception as e:
                q, warnings_ = pd.DataFrame(), warnings_ + [f"DART 재무 조회 실패: {e}"]
            f_C = eng.score_C(q, self.cfg)
            f_A = eng.score_A(q, self.cfg)
        else:
            f_C = eng.FactorScore("C", -1, {}, ["재무 분석 생략 (DART 키 없음 또는 옵션 off)"], False)
            f_A = eng.FactorScore("A", -1, {}, ["재무 분석 생략"], False)
            if not self.hub.dart_key:
                warnings_.append("DART_API_KEY 미설정 — C·A 항목이 중립(50점)으로 처리됩니다")

        factors = [f_C, f_A, f_N, f_S, f_L, f_I]
        summary = eng.combine(factors, self.cfg, ms.state)
        plan = eng.build_trade_plan(df, base, self.cfg, ms.state, account_size)

        return eng.CanslimReport(
            code=code, name=name, market=market_name, date=end,
            total_score=summary["total_score"], grade=summary["grade"],
            verdict=summary["verdict"],
            factors={f.key: f.to_dict() for f in factors},
            base=base.to_dict(), trade_plan=plan,
            market_state=ms.to_dict(),
            warnings=warnings_ + ([f"데이터 결측 항목: {', '.join(summary['missing'])}"]
                                  if summary["missing"] else []),
        )

    # ─────────────────────────────────────────────────────────
    # 전 종목 스크리닝
    # ─────────────────────────────────────────────────────────
    def screen(self, top_n: int = 30, deep_n: int = 40,
               with_fundamentals: bool = True,
               account_size: Optional[float] = None) -> Dict[str, Any]:
        """
        전체 파이프라인 실행.
        deep_n: RS 통과 종목 중 상위 몇 개를 재무·수급까지 정밀 분석할지
        """
        t0 = time.time()
        gates = self.market_gate()
        uni = self.prefilter()

        rs = self.build_rs_table(uni["Code"].tolist())
        merged = self.sector_ranks(uni)
        if merged.empty:
            merged = uni.merge(rs, left_on="Code", right_index=True, how="inner")
            merged["sector_pct"] = np.nan
            merged["sector_strength"] = np.nan

        cand = merged[merged["rs_rating"] >= self.cfg.L.rs_rating_min].copy()
        cand = cand.sort_values("rs_rating", ascending=False)
        self.log(f"■ RS {self.cfg.L.rs_rating_min}+ 통과: {len(cand):,}종목")

        # 신고가 근접 필터 (N 1차)
        keep = []
        for _, r in cand.iterrows():
            try:
                d = self.hub.ohlcv(r["Code"],
                                   (pd.Timestamp(last_business_day()) - pd.Timedelta(days=420)).strftime("%Y%m%d"))
                p = ind.pct_of_52w_high(d)
                if p is not None and p >= self.cfg.N.pct_of_52w_high_min:
                    keep.append((r["Code"], p))
            except Exception:
                continue
        cand = cand[cand["Code"].isin([k for k, _ in keep])]
        self.log(f"■ 52주 신고가 {self.cfg.N.pct_of_52w_high_min:.0%} 이상: {len(cand):,}종목")

        # 정밀 분석
        reports = []
        for i, (_, r) in enumerate(cand.head(deep_n).iterrows()):
            try:
                rep = self.analyze(
                    r["Code"], r.get("Name"), r.get("Market"),
                    account_size=account_size,
                    with_fundamentals=with_fundamentals,
                    rs_rating=int(r["rs_rating"]),
                    sector_pct=None if pd.isna(r.get("sector_pct")) else float(r["sector_pct"]),
                    sector_strength=None if pd.isna(r.get("sector_strength")) else float(r["sector_strength"]),
                )
                reports.append(rep)
                self.log(f"  [{i+1}/{min(deep_n, len(cand))}] {rep.name}({rep.code}) "
                         f"{rep.total_score}점 {rep.grade} — {rep.verdict}")
            except Exception as e:
                self.log(f"  [{i+1}] {r['Code']} 분석 실패: {e}")

        reports.sort(key=lambda x: x.total_score, reverse=True)
        out = {
            "생성시각": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "기준일": last_business_day(),
            "소요시간(초)": round(time.time() - t0, 1),
            "시장판정": {k: v.to_dict() for k, v in gates.items()},
            "유니버스": len(uni),
            "RS통과": int(len(cand)),
            "정밀분석": len(reports),
            "종목": [r.to_dict() for r in reports[:top_n]],
        }
        return out

    # ─────────────────────────────────────────────────────────
    # 출력
    # ─────────────────────────────────────────────────────────
    @staticmethod
    def save_json(result: Dict[str, Any], path: str = "canslim_result.json"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        print(f"저장: {path}")
        return path

    @staticmethod
    def to_markdown(rep: eng.CanslimReport) -> str:
        L = []
        L.append(f"# {rep.name} ({rep.code}) · {rep.market}")
        L.append(f"**{rep.total_score}점 / {rep.grade}** — {rep.verdict}  ")
        L.append(f"기준일 {rep.date}\n")

        ms = rep.market_state
        L.append(f"## M · 시장 — {ms['state']}")
        L.append(f"분산일 {ms['distribution_days']}개 | 허용비중 {ms['max_exposure']:.0%}")
        for c in ms.get("commentary", []):
            L.append(f"- {c}")
        L.append("")

        labels = {"C": "최근 분기 실적", "A": "연간 실적", "N": "신고가·베이스",
                  "S": "수급(물량)", "L": "주도주(RS)", "I": "기관 수급"}
        L.append("## CANSLIM 항목별")
        L.append("| 항목 | 점수 | 핵심 |")
        L.append("|---|---|---|")
        for k in ("C", "A", "N", "S", "L", "I"):
            f = rep.factors.get(k, {})
            sc = f.get("score", -1)
            head = (f.get("notes") or ["—"])[0]
            L.append(f"| **{k}** {labels[k]} | {'데이터없음' if sc < 0 else f'{sc:.0f}'} | {head} |")
        L.append("")

        for k in ("C", "A", "N", "S", "L", "I"):
            f = rep.factors.get(k, {})
            if not f.get("detail") and not f.get("notes"):
                continue
            L.append(f"### {k} · {labels[k]} — {f.get('score')}")
            for dk, dv in (f.get("detail") or {}).items():
                L.append(f"- {dk}: {dv}")
            for n in (f.get("notes") or [])[1:]:
                L.append(f"- {n}")
            L.append("")

        b = rep.base
        L.append("## 베이스")
        if b.get("found"):
            L.append(f"{b['pattern']} · {b['weeks']}주 · 깊이 {b['depth']:.1%} · "
                     f"{b['stage']}차 · 상태 {b['status']}")
            L.append(f"피벗 {b['pivot']:,.0f}원")
        else:
            L.append("유효 베이스 없음")
        L.append("")

        L.append("## 매매 계획")
        for k, v in rep.trade_plan.items():
            L.append(f"- {k}: {v}")
        if rep.warnings:
            L.append("\n## 주의")
            for w in rep.warnings:
                L.append(f"- {w}")
        return "\n".join(L)

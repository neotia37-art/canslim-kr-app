"""
panel.py — 시점정합(Point-in-Time) 데이터 패널

백테스트 결과를 망치는 두 가지 편향을 여기서 막습니다.

1. 미래 참조 편향 (look-ahead bias)
   2020년 3월 시점의 백테스트가 2020년 1분기 실적을 알고 있으면 안 됩니다.
   한국 분기보고서는 분기 종료 후 최대 45일(사업보고서는 90일) 뒤에 제출됩니다.
   따라서 각 재무 레코드에 '언제부터 알 수 있었는가'(available_from)를 붙이고,
   신호 계산 시 available_from <= 오늘 인 레코드만 씁니다.

2. 생존 편향 (survivorship bias)
   오늘 상장된 종목만으로 과거를 테스트하면, 그동안 상장폐지된 종목이
   전부 빠져서 수익률이 부풀려집니다.
   pykrx의 get_market_ticker_list(과거일자)는 그 시점의 실제 상장 종목을
   돌려주므로, 연도별 스냅샷을 모아 유니버스 이력을 만듭니다.

패널은 parquet으로 캐시되며 중단 후 재개(resume)가 가능합니다.
전 종목 10년치 수집은 몇 시간~며칠 걸립니다. 한 번 만들어두면 재사용됩니다.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

import numpy as np
import pandas as pd

from ..datahub import KRDataHub, IDX_KOSPI, IDX_KOSDAQ, build_quarterly_series


# 분기별 법정 제출기한 → 데이터를 알 수 있게 되는 최초 시점 (보수적)
#   1·3분기 보고서: 분기 종료 후 45일
#   반기 보고서   : 반기 종료 후 45일
#   사업 보고서   : 사업연도 종료 후 90일
FILING_LAG = {1: 45, 2: 45, 3: 45, 4: 90}
QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def available_from(year: int, quarter: int, extra_days: int = 0) -> pd.Timestamp:
    m, d = QUARTER_END[quarter]
    return pd.Timestamp(year, m, d) + pd.Timedelta(days=FILING_LAG[quarter] + extra_days)


class PanelStore:
    """백테스트용 데이터 패널 저장소."""

    def __init__(self, hub: KRDataHub, root: str = "./.canslim_panel",
                 verbose: bool = True):
        self.hub = hub
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose
        self._cache: Dict[str, pd.DataFrame] = {}

    def log(self, *a):
        if self.verbose:
            print(*a, flush=True)

    def _path(self, name: str) -> Path:
        return self.root / f"{name}.parquet"

    def _save(self, name: str, df: pd.DataFrame):
        try:
            df.to_parquet(self._path(name))
        except Exception:
            df.to_pickle(self.root / f"{name}.pkl")
        self._cache[name] = df

    def _load(self, name: str) -> Optional[pd.DataFrame]:
        if name in self._cache:
            return self._cache[name]
        p = self._path(name)
        if p.exists():
            df = pd.read_parquet(p)
            self._cache[name] = df
            return df
        pk = self.root / f"{name}.pkl"
        if pk.exists():
            df = pd.read_pickle(pk)
            self._cache[name] = df
            return df
        return None

    def has(self, name: str) -> bool:
        return self._path(name).exists() or (self.root / f"{name}.pkl").exists()

    # ─────────────────────────────────────────────────────────
    # 1. 유니버스 이력 — 생존 편향 차단
    # ─────────────────────────────────────────────────────────
    def build_universe_history(self, start_year: int, end_year: int,
                               freq_months: int = 3) -> pd.DataFrame:
        """
        분기 스냅샷마다 '그 시점에 실제로 상장돼 있던 종목'을 기록합니다.
        상장폐지된 종목도 남으므로 생존 편향이 사라집니다.

        반환: snapshot_date, Code, Market
        """
        name = "universe_history"
        cached = self._load(name)
        if cached is not None:
            return cached

        dates = pd.date_range(f"{start_year}-01-01", f"{end_year}-12-31",
                              freq=f"{freq_months}MS")
        rows = []
        for i, d in enumerate(dates):
            ds = d.strftime("%Y%m%d")
            for mkt in ("KOSPI", "KOSDAQ"):
                try:
                    ticks = self.hub.stock.get_market_ticker_list(ds, market=mkt)
                    rows += [{"snapshot_date": d, "Code": t, "Market": mkt} for t in ticks]
                except Exception as e:
                    self.log(f"  {ds} {mkt} 실패: {e}")
                time.sleep(0.15)
            self.log(f"  유니버스 스냅샷 {i+1}/{len(dates)} ({ds}) 누적 {len(rows):,}행")
        df = pd.DataFrame(rows)
        self._save(name, df)
        return df

    def all_codes(self) -> List[str]:
        """이력에 한 번이라도 등장한 전체 종목코드 (상장폐지 포함)."""
        uh = self._load("universe_history")
        if uh is None:
            raise RuntimeError("build_universe_history()를 먼저 실행하세요")
        return sorted(uh["Code"].unique().tolist())

    def listed_mask(self, dates: pd.DatetimeIndex, codes: List[str]) -> pd.DataFrame:
        """날짜×종목 상장 여부 마스크. 스냅샷 사이는 직전 값을 유지합니다."""
        uh = self._load("universe_history")
        piv = (uh.assign(v=True)
                 .pivot_table(index="snapshot_date", columns="Code", values="v",
                              aggfunc="first", fill_value=False))
        piv = piv.reindex(columns=codes, fill_value=False)
        return piv.reindex(dates, method="ffill").fillna(False).astype(bool)

    def market_of(self) -> pd.Series:
        uh = self._load("universe_history")
        return uh.groupby("Code")["Market"].last()

    # ─────────────────────────────────────────────────────────
    # 2. 가격 패널
    # ─────────────────────────────────────────────────────────
    def build_price_panels(self, codes: List[str], start: str, end: str,
                           batch_save: int = 200, sleep: float = 0.12) -> Dict[str, pd.DataFrame]:
        """
        종가·고가·저가·거래량·거래대금 패널 (행=날짜, 열=종목).
        중단되면 진행분까지 저장하고, 재실행 시 남은 종목만 이어서 받습니다.
        """
        fields = ["종가", "고가", "저가", "시가", "거래량", "거래대금"]
        acc = {f: {} for f in fields}
        done: Set[str] = set()

        for f in fields:
            ex = self._load(f"px_{f}")
            if ex is not None:
                acc[f] = {c: ex[c] for c in ex.columns}
                done |= set(ex.columns)

        todo = [c for c in codes if c not in done]
        self.log(f"■ 가격 패널: 전체 {len(codes):,} · 완료 {len(done):,} · 남음 {len(todo):,}")

        for i, code in enumerate(todo):
            try:
                df = self.hub.ohlcv(code, start, end)
                if df is not None and not df.empty:
                    for f in fields:
                        if f in df.columns:
                            acc[f][code] = df[f]
            except Exception:
                pass
            time.sleep(sleep)
            if (i + 1) % batch_save == 0:
                for f in fields:
                    self._save(f"px_{f}", pd.DataFrame(acc[f]).sort_index())
                self.log(f"  {i+1}/{len(todo)} 저장")

        out = {}
        for f in fields:
            df = pd.DataFrame(acc[f]).sort_index()
            self._save(f"px_{f}", df)
            out[f] = df
        self.log(f"  완료: {out['종가'].shape[1]:,}종목 × {out['종가'].shape[0]:,}일")
        return out

    def px(self, field: str = "종가") -> pd.DataFrame:
        df = self._load(f"px_{field}")
        if df is None:
            raise RuntimeError("build_price_panels()를 먼저 실행하세요")
        return df

    # ─────────────────────────────────────────────────────────
    # 3. 시가총액 패널
    # ─────────────────────────────────────────────────────────
    def build_cap_panel(self, start_year: int, end_year: int,
                        freq: str = "ME") -> pd.DataFrame:
        """
        월말 시가총액 스냅샷. 일별로 다 받으면 호출 수가 과도해서
        월말 기준으로 받고 일별로는 직전값을 유지합니다.
        (I 지표 정규화와 유동성 필터에만 쓰이므로 이 해상도로 충분합니다.)
        """
        name = "cap_monthly"
        cached = self._load(name)
        if cached is not None:
            return cached

        dates = pd.date_range(f"{start_year}-01-01", f"{end_year}-12-31", freq=freq)
        rows = []
        for i, d in enumerate(dates):
            ds = d.strftime("%Y%m%d")
            for mkt in ("KOSPI", "KOSDAQ"):
                try:
                    c = self.hub.stock.get_market_cap_by_ticker(ds, market=mkt)
                    if c is not None and not c.empty:
                        c = c.reset_index()
                        c.columns = [str(x) for x in c.columns]
                        key = c.columns[0]
                        for _, r in c.iterrows():
                            rows.append({"date": d, "Code": str(r[key]).zfill(6),
                                         "시가총액": r.get("시가총액"),
                                         "상장주식수": r.get("상장주식수")})
                except Exception:
                    pass
                time.sleep(0.15)
            if (i + 1) % 12 == 0:
                self.log(f"  시총 {i+1}/{len(dates)}개월")
        df = pd.DataFrame(rows)
        self._save(name, df)
        return df

    def cap_panel(self, dates: pd.DatetimeIndex, codes: List[str]) -> pd.DataFrame:
        m = self._load("cap_monthly")
        if m is None:
            return pd.DataFrame(index=dates, columns=codes, dtype=float)
        piv = m.pivot_table(index="date", columns="Code", values="시가총액", aggfunc="last")
        return piv.reindex(columns=codes).reindex(dates, method="ffill")

    # ─────────────────────────────────────────────────────────
    # 4. PIT 재무 패널  ★미래 참조 차단의 핵심★
    # ─────────────────────────────────────────────────────────
    def build_financial_panel(self, codes: List[str], years: int = 12,
                              batch_save: int = 50) -> pd.DataFrame:
        """
        종목별 단일 분기 재무 시계열에 available_from을 붙여 저장합니다.

        DART 일일 한도가 20,000건이고 종목당 연 4회 × years년 호출이 필요합니다.
        500종목 × 12년이면 24,000건 → 이틀치 한도.
        중단 시 진행분이 저장되고 다음 실행에서 이어집니다.
        """
        name = "financials_pit"
        acc = self._load(name)
        done = set(acc["Code"].unique()) if acc is not None else set()
        todo = [c for c in codes if c not in done]
        self.log(f"■ 재무 패널: 전체 {len(codes):,} · 완료 {len(done):,} · 남음 {len(todo):,}")
        if not todo:
            return acc

        chunks = [acc] if acc is not None else []
        buf = []
        for i, code in enumerate(todo):
            try:
                q = build_quarterly_series(self.hub, code, years=years)
                if q is not None and not q.empty:
                    q = q.copy()
                    q["Code"] = code
                    q["available_from"] = [
                        available_from(int(r.year), int(r.quarter))
                        for r in q.itertuples()
                    ]
                    buf.append(q)
            except Exception:
                pass
            if (i + 1) % batch_save == 0:
                if buf:
                    chunks.append(pd.concat(buf, ignore_index=True))
                    buf = []
                self._save(name, pd.concat(chunks, ignore_index=True))
                self.log(f"  {i+1}/{len(todo)} 저장")
        if buf:
            chunks.append(pd.concat(buf, ignore_index=True))
        out = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
        self._save(name, out)
        return out

    def financials_asof(self, date) -> pd.DataFrame:
        """
        기준일 시점에 '실제로 공시돼 있던' 최신 분기 실적과 전년 동기를 반환합니다.
        여기서 available_from 필터를 걸지 않으면 백테스트가 통째로 무의미해집니다.

        반환: Code, year, quarter, net_income, revenue, ni_yoy, rev_yoy, roe_ttm
        """
        f = self._load("financials_pit")
        if f is None or f.empty:
            return pd.DataFrame()
        d = pd.Timestamp(date)
        vis = f[f["available_from"] <= d].copy()
        if vis.empty:
            return pd.DataFrame()

        vis["period"] = vis["year"] * 4 + vis["quarter"]
        vis = vis.sort_values(["Code", "period"])
        latest = vis.groupby("Code").tail(1).set_index("Code")

        # 전년 동기 (period - 4)
        prev_key = latest["period"] - 4
        pm = vis.set_index(["Code", "period"])
        prev = []
        for code, p in prev_key.items():
            try:
                prev.append(pm.loc[(code, p)])
            except KeyError:
                prev.append(None)

        rows = []
        for (code, cur), pv in zip(latest.iterrows(), prev):
            ni, rv = cur.get("net_income"), cur.get("revenue")
            pni = pv.get("net_income") if pv is not None else None
            prv = pv.get("revenue") if pv is not None else None
            ni_yoy = (ni / pni - 1) if (ni is not None and pni and pni > 0) else np.nan
            rev_yoy = (rv / prv - 1) if (rv is not None and prv and prv > 0) else np.nan
            turn = bool(pni is not None and ni is not None and pni <= 0 < ni)

            hist = vis[vis["Code"] == code].tail(4)
            ttm_ni = hist["net_income"].sum() if len(hist) == 4 else np.nan
            eq = cur.get("equity")
            roe = (ttm_ni / eq) if (eq and eq > 0 and ttm_ni == ttm_ni) else np.nan

            rows.append({"Code": code, "year": cur["year"], "quarter": cur["quarter"],
                         "net_income": ni, "revenue": rv, "ni_yoy": ni_yoy,
                         "rev_yoy": rev_yoy, "turnaround": turn, "roe_ttm": roe,
                         "available_from": cur["available_from"]})
        return pd.DataFrame(rows).set_index("Code")

    # ─────────────────────────────────────────────────────────
    # 5. 수급 패널
    # ─────────────────────────────────────────────────────────
    def build_flow_panel(self, codes: List[str], start: str, end: str,
                         batch_save: int = 100, sleep: float = 0.15) -> Dict[str, pd.DataFrame]:
        """기관·외국인 일별 순매수 거래대금 패널."""
        acc = {"기관": {}, "외국인": {}}
        done: Set[str] = set()
        for k in acc:
            ex = self._load(f"flow_{k}")
            if ex is not None:
                acc[k] = {c: ex[c] for c in ex.columns}
                done |= set(ex.columns)

        todo = [c for c in codes if c not in done]
        self.log(f"■ 수급 패널: 남음 {len(todo):,}")
        for i, code in enumerate(todo):
            try:
                fl = self.hub.investor_flow(code, start, end)
                if fl is not None and not fl.empty:
                    for k, cands in (("기관", ["기관합계", "기관"]),
                                     ("외국인", ["외국인합계", "외국인"])):
                        for c in cands:
                            if c in fl.columns:
                                acc[k][code] = fl[c]
                                break
            except Exception:
                pass
            time.sleep(sleep)
            if (i + 1) % batch_save == 0:
                for k in acc:
                    self._save(f"flow_{k}", pd.DataFrame(acc[k]).sort_index())
                self.log(f"  {i+1}/{len(todo)} 저장")

        out = {}
        for k in acc:
            df = pd.DataFrame(acc[k]).sort_index()
            self._save(f"flow_{k}", df)
            out[k] = df
        return out

    def flow(self, kind: str = "기관") -> Optional[pd.DataFrame]:
        return self._load(f"flow_{kind}")

    # ─────────────────────────────────────────────────────────
    # 6. 지수
    # ─────────────────────────────────────────────────────────
    def build_index_panel(self, start: str, end: str) -> Dict[str, pd.DataFrame]:
        out = {}
        for name, code in (("KOSPI", IDX_KOSPI), ("KOSDAQ", IDX_KOSDAQ)):
            cached = self._load(f"idx_{name}")
            if cached is not None:
                out[name] = cached
                continue
            df = self.hub.index_ohlcv(code, start, end)
            self._save(f"idx_{name}", df)
            out[name] = df
        return out

    def index(self, name: str = "KOSPI") -> pd.DataFrame:
        df = self._load(f"idx_{name}")
        if df is None:
            raise RuntimeError("build_index_panel()를 먼저 실행하세요")
        return df

    # ─────────────────────────────────────────────────────────
    # 전체 구축
    # ─────────────────────────────────────────────────────────
    def build_all(self, start: str = "2014-01-01", end: str = "2026-08-18",
                  max_codes: Optional[int] = None, with_financials: bool = True,
                  with_flow: bool = True):
        """
        전체 패널을 순서대로 구축합니다.
        max_codes를 주면 시총 상위 N종목만 받아 시험 실행할 수 있습니다.
        """
        sy, ey = int(start[:4]), int(end[:4])
        self.log("■ 1/5 유니버스 이력 (생존 편향 차단)")
        self.build_universe_history(sy, ey)
        codes = self.all_codes()

        if max_codes:
            self.log(f"■ 2/5 시가총액 (상위 {max_codes}종목 선별용)")
            self.build_cap_panel(sy, ey)
            cap = self._load("cap_monthly")
            top = (cap.groupby("Code")["시가총액"].median()
                      .sort_values(ascending=False).head(max_codes).index.tolist())
            codes = [c for c in codes if c in set(top)]
            self.log(f"  대상 {len(codes):,}종목으로 축소")
        else:
            self.log("■ 2/5 시가총액")
            self.build_cap_panel(sy, ey)

        self.log("■ 3/5 지수")
        self.build_index_panel(start, end)

        self.log("■ 4/5 가격 패널")
        self.build_price_panels(codes, start, end)

        if with_flow:
            self.log("■ 5/5 수급 패널")
            self.build_flow_panel(codes, start, end)

        if with_financials:
            self.log("■ + 재무 패널 (PIT)")
            self.build_financial_panel(codes, years=ey - sy + 1)

        self.log("완료. 패널 위치: " + str(self.root.resolve()))

    def summary(self) -> dict:
        out = {}
        for f in ("종가", "거래량"):
            df = self._load(f"px_{f}")
            if df is not None:
                out[f"px_{f}"] = f"{df.shape[0]:,}일 × {df.shape[1]:,}종목"
        for n in ("universe_history", "financials_pit", "cap_monthly",
                  "flow_기관", "flow_외국인"):
            df = self._load(n)
            if df is not None:
                out[n] = f"{len(df):,}행"
        return out

"""
datahub.py — 한국 시장 데이터 수집 계층

데이터 원천
  1) pykrx        : 시세, 시가총액, 투자자별 수급, 공매도, 지수  (무료, KRX 스크래핑)
  2) FinanceDataReader : 종목 마스터(업종/섹터), 보조 시세
  3) OpenDART API : 재무제표(정확한 분기 실적), 주요 공시(유상증자·자사주·CB)
  4) KRX OpenAPI  : (선택) 공식 API. 인증키 필요, 2010년 이후 데이터 제공

설치:
    pip install pykrx finance-datareader requests pandas numpy

DART 키 발급: https://opendart.fss.or.kr  (무료, 일 20,000건)
KRX 키 발급 : https://openapi.krx.co.kr   (무료, 1년 단위 갱신)

주의: pykrx는 KRX/네이버 스크래핑입니다. 과도한 호출은 자제하고
      반드시 로컬 캐시를 사용하세요 (이 모듈이 자동 처리합니다).
"""

from __future__ import annotations

import os
import io
import json
import time
import zipfile
import hashlib
import warnings
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional, Dict, List

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

CACHE_DIR = Path(os.environ.get("CANSLIM_CACHE", "./.canslim_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DART_KEY = os.environ.get("DART_API_KEY", "")
KRX_KEY = os.environ.get("KRX_OPENAPI_KEY", "")

# 지수 코드
IDX_KOSPI = "1001"
IDX_KOSDAQ = "2001"

# DART 보고서 코드
REPRT = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}  # 1Q, 반기, 3Q, 사업보고서


# ─────────────────────────────────────────────────────────────
# 캐시 유틸
# ─────────────────────────────────────────────────────────────
def _cache_path(key: str, ext: str = "pkl") -> Path:
    # 디렉토리가 지워졌거나 권한이 없으면 저장이 조용히 실패합니다.
    # 매번 확인해서 캐시가 말없이 무력화되는 일을 막습니다.
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    h = hashlib.md5(key.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{h}.{ext}"


def cached(key: str, ttl_hours: float = 12.0):
    """디스크 캐시 데코레이터. 장중에는 ttl을 짧게, 재무데이터는 길게 쓰세요."""
    def deco(fn):
        def wrapper(*args, **kwargs):
            p = _cache_path(f"{key}|{args}|{sorted(kwargs.items())}")
            if p.exists():
                age_h = (time.time() - p.stat().st_mtime) / 3600
                if age_h < ttl_hours:
                    try:
                        return pd.read_pickle(p)
                    except Exception:
                        pass
            out = fn(*args, **kwargs)
            # 빈 결과는 캐시하지 않습니다.
            # 일시적 조회 실패를 저장하면 그 실패가 TTL 내내 굳어버립니다.
            empty = (out is None
                     or (isinstance(out, (pd.DataFrame, pd.Series)) and len(out) == 0))
            if not empty:
                try:
                    pd.to_pickle(out, p)
                except Exception:
                    pass
            return out
        return wrapper
    return deco


def ymd(d) -> str:
    if isinstance(d, str):
        return d.replace("-", "")
    return pd.Timestamp(d).strftime("%Y%m%d")


def last_business_day(ref: Optional[str] = None) -> str:
    """최근 영업일 추정. 장 마감(15:30) 이전이면 전 영업일을 반환합니다."""
    now = pd.Timestamp.now(tz="Asia/Seoul") if ref is None else pd.Timestamp(ref)
    if ref is None and now.hour < 16:
        now = now - pd.Timedelta(days=1)
    while now.weekday() >= 5:
        now = now - pd.Timedelta(days=1)
    return now.strftime("%Y%m%d")


# ─────────────────────────────────────────────────────────────
# 데이터 허브
# ─────────────────────────────────────────────────────────────
class KRDataHub:
    """한국 시장 데이터 단일 진입점."""

    def __init__(self, dart_key: str = DART_KEY, verbose: bool = True):
        self.dart_key = dart_key
        self.verbose = verbose
        self._stock = None
        self._fdr = None
        self._requests = None
        self._corp_map: Optional[pd.DataFrame] = None

    # ── 지연 임포트 (라이브러리 미설치 환경에서도 모듈 로드는 되게) ──
    @property
    def stock(self):
        if self._stock is None:
            from pykrx import stock as _s
            self._stock = _s
        return self._stock

    @property
    def fdr(self):
        if self._fdr is None:
            import FinanceDataReader as _f
            self._fdr = _f
        return self._fdr

    @property
    def req(self):
        if self._requests is None:
            import requests as _r
            self._requests = _r
        return self._requests

    def log(self, *a):
        if self.verbose:
            print(*a, flush=True)

    # ─────────────────────────────────────────────────────────
    # 1. 종목 마스터
    # ─────────────────────────────────────────────────────────
    def _listing_via_pykrx(self) -> pd.DataFrame:
        """
        pykrx 경유 종목 마스터 (FDR 실패 시 폴백).
        업종 정보는 없지만 검색·분석에 필요한 Code/Name/Market은 확보됩니다.
        """
        d = last_business_day()
        rows = []
        for mkt in ("KOSPI", "KOSDAQ"):
            try:
                for t in self.stock.get_market_ticker_list(d, market=mkt):
                    try:
                        nm = self.stock.get_market_ticker_name(t)
                    except Exception:
                        nm = t
                    rows.append({"Code": str(t).zfill(6), "Name": nm, "Market": mkt})
            except Exception as ex:
                self.log(f"[listing] pykrx {mkt} 실패: {ex}")
        df = pd.DataFrame(rows)
        if not df.empty:
            # 같은 코드가 두 시장에 잡히면 첫 번째만 남깁니다.
            # 중복이 남으면 종목 검색에서 엉뚱한 시장이 잡힐 수 있습니다.
            df = df.drop_duplicates(subset="Code", keep="first").reset_index(drop=True)
        return df

    @cached("listing", ttl_hours=24)
    def listing(self) -> pd.DataFrame:
        """
        전 종목 마스터. 컬럼: Code, Name, Market, Sector, Industry

        FinanceDataReader를 우선 쓰되, 실패하면 pykrx로 폴백합니다.
        이 함수가 죽으면 종목 검색과 관심목록 추가가 통째로 막히므로
        어느 한쪽이 실패해도 앱이 계속 동작해야 합니다.
        """
        df = None
        try:
            df = self.fdr.StockListing("KRX")
        except Exception as ex:
            self.log(f"[listing] FDR 실패 → pykrx 폴백: {ex}")
        if df is None or len(df) == 0:
            df = self._listing_via_pykrx()
            if df is None or df.empty:
                raise RuntimeError(
                    "종목 마스터 조회 실패 — FinanceDataReader와 pykrx 모두 응답하지 않습니다")
            return df.reset_index(drop=True)
        cols = {c.lower(): c for c in df.columns}
        rename = {}
        for want, cands in {
            "Code": ["code", "symbol"],
            "Name": ["name"],
            "Market": ["market"],
            "Sector": ["sector"],
            "Industry": ["industry"],
        }.items():
            for c in cands:
                if c in cols:
                    rename[cols[c]] = want
                    break
        df = df.rename(columns=rename)
        keep = [c for c in ["Code", "Name", "Market", "Sector", "Industry"] if c in df.columns]
        df = df[keep].copy()
        df["Code"] = df["Code"].astype(str).str.zfill(6)
        return df.reset_index(drop=True)

    @cached("snapshot", ttl_hours=6)
    def market_snapshot(self, date: Optional[str] = None) -> pd.DataFrame:
        """
        특정일 전 종목 스냅샷: 종가·거래량·거래대금·시가총액·상장주식수·PER·PBR·EPS·BPS
        스크리닝 1차 필터의 기반 데이터입니다.
        """
        d = date or last_business_day()
        frames, errs = [], []
        for mkt in ("KOSPI", "KOSDAQ"):
            # 한 시장이 실패해도 다른 시장은 살립니다.
            # 코스닥만 조회가 막히는 경우가 실제로 발생합니다.
            try:
                cap = self.stock.get_market_cap_by_ticker(d, market=mkt)
                if cap is None or cap.empty:
                    errs.append(f"{mkt}: 시가총액 응답 없음")
                    continue
                m = cap
                for getter, cols in (
                    (self.stock.get_market_fundamental_by_ticker, None),
                    (self.stock.get_market_ohlcv_by_ticker,
                     ["시가", "고가", "저가", "등락률"]),
                ):
                    try:
                        sub = getter(d, market=mkt)
                        if sub is not None and not sub.empty:
                            if cols:
                                sub = sub[[c for c in cols if c in sub.columns]]
                            m = m.join(sub, how="left", rsuffix="_x")
                    except Exception as ex:
                        errs.append(f"{mkt} 보조데이터: {ex}")
                m["Market"] = mkt
                frames.append(m)
            except Exception as ex:
                errs.append(f"{mkt}: {ex}")
        if not frames:
            raise RuntimeError("전 종목 스냅샷 조회 실패 — " + " | ".join(errs[:3]))
        if errs:
            self.log("[snapshot] 일부 실패: " + " | ".join(errs[:3]))
        out = pd.concat(frames)
        out.index.name = "Code"
        out = out.reset_index()
        out["Code"] = out["Code"].astype(str).str.zfill(6)
        out["Date"] = d
        return out

    # ─────────────────────────────────────────────────────────
    # 2. 시세
    # ─────────────────────────────────────────────────────────
    @cached("ohlcv", ttl_hours=6)
    def ohlcv(self, code: str, start: str, end: Optional[str] = None) -> pd.DataFrame:
        """
        개별 종목 일봉. 컬럼: 시가 고가 저가 종가 거래량 거래대금 등락률

        pykrx 실패 시 FinanceDataReader로 폴백합니다.
        FDR에는 거래대금이 없어 종가×거래량으로 근사합니다
        (유동성 필터에만 쓰이므로 이 정도 정밀도로 충분합니다).
        """
        e = end or last_business_day()
        try:
            df = self.stock.get_market_ohlcv(ymd(start), ymd(e), code)
            df = self._normalize_ohlcv(df)
            if not df.empty:
                return df
        except Exception:
            pass

        try:
            s0 = pd.Timestamp(ymd(start)).strftime("%Y-%m-%d")
            e0 = pd.Timestamp(ymd(e)).strftime("%Y-%m-%d")
            df = self._normalize_ohlcv(self.fdr.DataReader(str(code).zfill(6), s0, e0))
            if not df.empty:
                if "거래대금" not in df.columns and {"종가", "거래량"} <= set(df.columns):
                    df["거래대금"] = df["종가"] * df["거래량"]
                return df
        except Exception:
            pass
        return pd.DataFrame()

    # FDR 지수 심볼 매핑 (pykrx 실패 시 폴백 경로)
    _FDR_INDEX = {IDX_KOSPI: "KS11", IDX_KOSDAQ: "KQ11", "1028": "KS200"}

    @staticmethod
    def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
        """영문 컬럼(FDR)을 한글 컬럼(pykrx)으로 통일합니다."""
        if df is None or df.empty:
            return pd.DataFrame()
        ren = {"Open": "시가", "High": "고가", "Low": "저가", "Close": "종가",
               "Volume": "거래량", "Amount": "거래대금", "Change": "등락률"}
        df = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})
        df.index = pd.to_datetime(df.index)
        return df[~df.index.duplicated(keep="last")].sort_index()

    def _index_via_fdr(self, idx: str, start: str, end: str) -> pd.DataFrame:
        """FinanceDataReader 경유 지수 조회. KRX 라이브 엔드포인트와 독립된 경로입니다."""
        sym = self._FDR_INDEX.get(str(idx))
        if not sym:
            return pd.DataFrame()
        s = pd.Timestamp(ymd(start)).strftime("%Y-%m-%d")
        e = pd.Timestamp(ymd(end)).strftime("%Y-%m-%d")
        df = self.fdr.DataReader(sym, s, e)
        df = self._normalize_ohlcv(df)
        if not df.empty and "거래량" not in df.columns:
            df["거래량"] = float("nan")
        return df

    @cached("index_ohlcv", ttl_hours=6)
    def index_ohlcv(self, idx: str, start: str, end: Optional[str] = None) -> pd.DataFrame:
        """
        지수 일봉.

        ★ name_display=False 가 핵심입니다 ★
        pykrx는 기본값 name_display=True 로 동작하면서 지수 '이름'을 붙이려고
        IndexTicker 싱글턴을 조회합니다. KRX의 전체지수기본정보 엔드포인트가
        막히면 그 싱글턴이 빈 DataFrame이 되고, 시세를 정상 수신하고도
        KeyError('지수명') 로 죽습니다. 이름은 우리가 쓰지 않으므로 끕니다.

        그래도 실패하면 FinanceDataReader로 폴백합니다.
        """
        e = end or last_business_day()
        errs = []

        for fn_name in ("get_index_ohlcv", "get_index_ohlcv_by_date"):
            fn = getattr(self.stock, fn_name, None)
            if fn is None:
                continue
            for kw in ({"name_display": False}, {}):
                try:
                    df = fn(ymd(start), ymd(e), idx, **kw)
                    df = self._normalize_ohlcv(df)
                    if not df.empty:
                        return df
                except Exception as ex:
                    errs.append(f"{fn_name}{'(name_display=False)' if kw else ''}: {ex}")

        try:
            df = self._index_via_fdr(idx, start, e)
            if not df.empty:
                self.log(f"[지수 {idx}] pykrx 실패 → FinanceDataReader로 조회 성공")
                return df
        except Exception as ex:
            errs.append(f"fdr: {ex}")

        raise RuntimeError(
            f"지수 {idx} 조회 실패. 시도한 경로: " + " | ".join(errs[:3]))

    @cached("close_matrix", ttl_hours=12)
    def close_matrix(self, codes: List[str], start: str,
                     end: Optional[str] = None, sleep: float = 0.12) -> pd.DataFrame:
        """
        RS Rating 계산용 종가 행렬 (행=날짜, 열=종목코드).
        전 종목을 한 번에 받는 API가 없어 순차 호출합니다. 캐시가 필수입니다.
        """
        e = end or last_business_day()
        out = {}
        for i, c in enumerate(codes):
            try:
                df = self.ohlcv(c, start, e)
                if not df.empty:
                    out[c] = df["종가"]
            except Exception:
                pass
            if sleep:
                time.sleep(sleep)
            if self.verbose and (i + 1) % 100 == 0:
                self.log(f"  시세 수집 {i+1}/{len(codes)}")
        return pd.DataFrame(out).sort_index()

    # ─────────────────────────────────────────────────────────
    # 3. 수급 (I 지표의 핵심 — 한국 시장의 강점)
    # ─────────────────────────────────────────────────────────
    @cached("investor", ttl_hours=6)
    def investor_flow(self, code: str, start: str, end: Optional[str] = None) -> pd.DataFrame:
        """
        투자자별 일별 순매수 '거래대금'.
        컬럼 예: 기관합계, 기타법인, 개인, 외국인합계, 전체
        당일 최종치는 오후 6시 이후 반영됩니다.
        """
        e = end or last_business_day()
        df = self.stock.get_market_trading_value_by_date(ymd(start), ymd(e), code)
        if df is None or df.empty:
            return pd.DataFrame()
        df.index = pd.to_datetime(df.index)
        return df.sort_index()

    @cached("foreign_rate", ttl_hours=12)
    def foreign_holding(self, code: str, start: str, end: Optional[str] = None) -> pd.DataFrame:
        """외국인 보유수량·지분율 추이."""
        e = end or last_business_day()
        try:
            df = self.stock.get_exhaustion_rates_of_foreign_investment(
                ymd(start), ymd(e), code)
            if df is None or df.empty:
                return pd.DataFrame()
            df.index = pd.to_datetime(df.index)
            return df.sort_index()
        except Exception:
            return pd.DataFrame()

    @cached("shorting", ttl_hours=12)
    def shorting_balance(self, code: str, start: str, end: Optional[str] = None) -> pd.DataFrame:
        e = end or last_business_day()
        try:
            df = self.stock.get_shorting_balance_by_date(ymd(start), ymd(e), code)
            if df is None or df.empty:
                return pd.DataFrame()
            df.index = pd.to_datetime(df.index)
            return df.sort_index()
        except Exception:
            return pd.DataFrame()

    # ─────────────────────────────────────────────────────────
    # 4. DART 재무제표 (C·A 지표)
    # ─────────────────────────────────────────────────────────
    @cached("corp_codes", ttl_hours=24 * 14)
    def corp_codes(self) -> pd.DataFrame:
        """DART 고유번호 ↔ 종목코드 매핑표."""
        if not self.dart_key:
            raise RuntimeError("DART_API_KEY 환경변수가 필요합니다. https://opendart.fss.or.kr")
        url = "https://opendart.fss.or.kr/api/corpCode.xml"
        r = self.req.get(url, params={"crtfc_key": self.dart_key}, timeout=30)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        xml = z.read(z.namelist()[0]).decode("utf-8")
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml)
        rows = []
        for it in root.iter("list"):
            sc = (it.findtext("stock_code") or "").strip()
            if sc:
                rows.append({
                    "corp_code": it.findtext("corp_code").strip(),
                    "Code": sc,
                    "corp_name": (it.findtext("corp_name") or "").strip(),
                })
        return pd.DataFrame(rows)

    def corp_code_of(self, code: str) -> Optional[str]:
        if self._corp_map is None:
            self._corp_map = self.corp_codes().set_index("Code")
        try:
            return self._corp_map.loc[str(code).zfill(6), "corp_code"]
        except KeyError:
            return None

    @cached("dart_fs", ttl_hours=24 * 3)
    def dart_financials(self, code: str, year: int, quarter: int,
                        fs_div: str = "CFS") -> pd.DataFrame:
        """
        단일회사 전체 재무제표 (fnlttSinglAcntAll).
        fs_div: CFS=연결, OFS=별도
        """
        cc = self.corp_code_of(code)
        if not cc:
            return pd.DataFrame()
        url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
        params = {
            "crtfc_key": self.dart_key,
            "corp_code": cc,
            "bsns_year": str(year),
            "reprt_code": REPRT[quarter],
            "fs_div": fs_div,
        }
        try:
            r = self.req.get(url, params=params, timeout=20).json()
        except Exception:
            return pd.DataFrame()
        if r.get("status") != "000":
            return pd.DataFrame()
        return pd.DataFrame(r["list"])

    @cached("dart_disclosures", ttl_hours=12)
    def disclosures(self, code: str, days: int = 180) -> pd.DataFrame:
        """
        최근 공시 목록 (list.json).
        S 지표에서 유상증자·CB/BW·자사주 취득/소각을 탐지하는 데 씁니다.
        """
        cc = self.corp_code_of(code)
        if not cc:
            return pd.DataFrame()
        end = datetime.now()
        start = end - timedelta(days=days)
        url = "https://opendart.fss.or.kr/api/list.json"
        rows, page = [], 1
        while page <= 5:
            params = {
                "crtfc_key": self.dart_key, "corp_code": cc,
                "bgn_de": start.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
                "page_no": page, "page_count": 100,
            }
            try:
                r = self.req.get(url, params=params, timeout=20).json()
            except Exception:
                break
            if r.get("status") != "000":
                break
            rows.extend(r.get("list", []))
            if page >= int(r.get("total_page", 1)):
                break
            page += 1
        return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────
# DART 계정 파서 — 여기가 정확도의 핵심
# ─────────────────────────────────────────────────────────────
ACCOUNT_ALIASES: Dict[str, List[str]] = {
    "revenue": ["매출액", "수익(매출액)", "영업수익", "매출"],
    "operating_income": ["영업이익", "영업이익(손실)"],
    "net_income": ["당기순이익", "당기순이익(손실)", "연결당기순이익",
                   "지배기업소유주지분순이익", "당기순이익(당기)"],
    "equity": ["자본총계", "자본총계(지배기업소유주지분)"],
    "assets": ["자산총계"],
    "liabilities": ["부채총계"],
    "eps": ["기본주당이익", "기본주당순이익", "주당순이익"],
}


def pick_account(df: pd.DataFrame, key: str, col: str = "thstrm_amount") -> Optional[float]:
    """DART 재무제표 DataFrame에서 계정 하나를 뽑습니다."""
    if df is None or df.empty or "account_nm" not in df.columns:
        return None
    names = ACCOUNT_ALIASES.get(key, [key])
    for nm in names:
        hit = df[df["account_nm"].astype(str).str.replace(" ", "") == nm.replace(" ", "")]
        if not hit.empty:
            v = str(hit.iloc[0].get(col, "")).replace(",", "").strip()
            if v in ("", "-", "nan", "None"):
                continue
            try:
                return float(v)
            except ValueError:
                continue
    return None


def quarterly_from_cumulative(cum: Dict[int, Optional[float]]) -> Dict[int, Optional[float]]:
    """
    ★ 한국 재무데이터에서 가장 많이 틀리는 지점 ★

    DART의 분기 손익계산서는 '누적' 기준입니다.
    (반기보고서 = 1~6월 누적, 3분기보고서 = 1~9월 누적, 사업보고서 = 연간)
    단일 분기 실적을 얻으려면 차분해야 합니다.

    입력  : {1: 1Q누적, 2: 반기누적, 3: 3Q누적, 4: 연간}
    출력  : {1: 1Q, 2: 2Q, 3: 3Q, 4: 4Q}
    """
    q = {}
    q[1] = cum.get(1)
    for i in (2, 3, 4):
        cur, prev = cum.get(i), cum.get(i - 1)
        q[i] = None if (cur is None or prev is None) else cur - prev
    return q


def build_quarterly_series(hub: KRDataHub, code: str, years: int = 4,
                           prefer_consolidated: bool = True) -> pd.DataFrame:
    """
    최근 N년치 '단일 분기' 매출·영업이익·순이익 시계열을 만듭니다.
    연결(CFS)을 우선하되 없으면 별도(OFS)로 폴백합니다.

    반환 컬럼: year, quarter, revenue, operating_income, net_income, equity, fs_div
    """
    this_year = datetime.now().year
    recs = []
    for y in range(this_year - years + 1, this_year + 1):
        cum = {k: {} for k in ("revenue", "operating_income", "net_income")}
        eq, fsdiv_used = {}, {}
        for q in (1, 2, 3, 4):
            df = pd.DataFrame()
            for fs in (["CFS", "OFS"] if prefer_consolidated else ["OFS", "CFS"]):
                df = hub.dart_financials(code, y, q, fs_div=fs)
                if not df.empty:
                    fsdiv_used[q] = fs
                    break
            if df.empty:
                for k in cum:
                    cum[k][q] = None
                eq[q] = None
                continue
            for k in cum:
                cum[k][q] = pick_account(df, k)
            eq[q] = pick_account(df, "equity")

        qtr = {k: quarterly_from_cumulative(cum[k]) for k in cum}
        for q in (1, 2, 3, 4):
            if all(qtr[k][q] is None for k in qtr):
                continue
            recs.append({
                "year": y, "quarter": q,
                "revenue": qtr["revenue"][q],
                "operating_income": qtr["operating_income"][q],
                "net_income": qtr["net_income"][q],
                "equity": eq.get(q),
                "fs_div": fsdiv_used.get(q),
            })
    out = pd.DataFrame(recs)
    if out.empty:
        return out
    return out.sort_values(["year", "quarter"]).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# 공시 이벤트 분류 (S 지표)
# ─────────────────────────────────────────────────────────────
DISCLOSURE_PATTERNS = {
    "dilution": ["유상증자결정", "유상증자", "주주배정", "제3자배정"],
    "cb_bw": ["전환사채", "신주인수권부사채", "교환사채", "전환청구권행사"],
    "treasury_buy": ["자기주식취득", "자기주식 취득", "자기주식취득신탁"],
    "treasury_cancel": ["자기주식소각", "자기주식 소각", "이익소각"],
    "major_sell": ["최대주주변경", "주식등의대량보유상황보고", "임원ㆍ주요주주특정증권등소유상황"],
    "split_off": ["물적분할", "인적분할", "분할결정"],
    "new_business": ["신규시설투자", "타법인주식및출자증권취득", "단일판매ㆍ공급계약체결"],
}


def classify_disclosures(df: pd.DataFrame) -> Dict[str, int]:
    """공시 목록에서 수급 관련 이벤트 건수를 셉니다."""
    out = {k: 0 for k in DISCLOSURE_PATTERNS}
    if df is None or df.empty or "report_nm" not in df.columns:
        return out
    titles = df["report_nm"].astype(str).str.replace(" ", "")
    for k, pats in DISCLOSURE_PATTERNS.items():
        for p in pats:
            out[k] += int(titles.str.contains(p.replace(" ", ""), regex=False).sum())
    return out

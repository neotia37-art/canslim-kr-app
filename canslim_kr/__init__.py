"""
CANSLIM-KR — 윌리엄 오닐 기법의 한국 주식 적용 엔진

quickstart
----------
    import os
    os.environ["DART_API_KEY"] = "발급받은키"

    from canslim_kr import CanslimKR, DEFAULT

    kr = CanslimKR(DEFAULT)

    # 1) 오늘 시장이 살 만한가?
    kr.market_gate()

    # 2) 종목 하나 정밀 분석
    rep = kr.analyze("005930", account_size=50_000_000)
    print(kr.to_markdown(rep))

    # 3) 전 종목 스크리닝
    res = kr.screen(top_n=30, deep_n=40, account_size=50_000_000)
    kr.save_json(res, "canslim_result.json")
"""

from .config import (CanslimKRConfig, DEFAULT, strict_preset, loose_preset,
                     UniverseConfig, CurrentEarningsConfig, AnnualEarningsConfig,
                     NewHighConfig, SupplyDemandConfig, LeaderConfig,
                     InstitutionalConfig, MarketConfig, BaseConfig,
                     TradeConfig, ScoringConfig)
from .datahub import KRDataHub, build_quarterly_series, last_business_day
from .screener import CanslimKR
from . import indicators, market, engine

__version__ = "1.0.0"
__all__ = ["CanslimKR", "CanslimKRConfig", "DEFAULT", "strict_preset",
           "loose_preset", "KRDataHub", "indicators", "market", "engine",
           "build_quarterly_series", "last_business_day"]

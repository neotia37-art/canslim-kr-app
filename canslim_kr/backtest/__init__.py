"""
canslim_kr.backtest — 2015~2026 한국 데이터 실증 검증 및 임계값 최적화

quickstart
----------
    from canslim_kr import KRDataHub
    from canslim_kr.backtest import PanelStore, SignalPanel, Backtester, Optimizer
    from canslim_kr.backtest import report, DEFAULT_GRID, sanity_checks

    hub = KRDataHub()
    store = PanelStore(hub)

    # 1) 패널 구축 (최초 1회, 시간 오래 걸림 / 중단 후 재개 가능)
    store.build_all(start="2014-01-01", end="2026-08-18", max_codes=600)

    # 2) 신호 계산
    sig = SignalPanel(store, DEFAULT); sig.compute()

    # 3) 단일 백테스트
    bt = Backtester(store, sig, DEFAULT, initial_capital=100_000_000)
    res = bt.run("2015-01-01", "2026-08-18")
    print(report(res, benchmark=store.index("KOSPI")["종가"]))

    # 4) 워크포워드 최적화 (과최적화 방지)
    opt = Optimizer(bt, DEFAULT)
    wf = opt.walk_forward("2015-01-01", "2026-08-18", DEFAULT_GRID,
                          train_years=3, test_years=1,
                          benchmark=store.index("KOSPI")["종가"])
    print(wf["요약"]); print(wf["권장파라미터"])
    for m in sanity_checks(wf): print("·", m)

    # 5) 최적 파라미터를 실전 설정에 반영
    tuned = Optimizer.apply(DEFAULT, wf["권장파라미터"])
"""

from .costs import CostModel, FRICTIONLESS, CONSERVATIVE, TAX_SCHEDULE
from .panel import PanelStore, available_from, FILING_LAG
from .signals import SignalPanel
from .simulator import Backtester, Position, Trade
from .metrics import (performance, yearly_breakdown, trade_attribution, report)
from .optimizer import (Optimizer, DEFAULT_GRID, QUICK_GRID, set_param,
                        objective_default, make_objective, sanity_checks)

__all__ = [
    "CostModel", "FRICTIONLESS", "CONSERVATIVE", "TAX_SCHEDULE",
    "PanelStore", "available_from", "FILING_LAG", "SignalPanel",
    "Backtester", "Position", "Trade",
    "performance", "yearly_breakdown", "trade_attribution", "report",
    "Optimizer", "DEFAULT_GRID", "QUICK_GRID", "set_param",
    "objective_default", "make_objective", "sanity_checks",
]

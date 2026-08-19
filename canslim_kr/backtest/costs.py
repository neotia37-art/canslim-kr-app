"""
costs.py — 거래비용 모델

백테스트에서 가장 흔한 거짓말이 "비용 무시"입니다.
오닐 전략은 손절이 잦아(-7~8%) 회전율이 높기 때문에
거래비용을 빼면 수익률이 실제보다 한참 부풀려집니다.

한국 증권거래세는 2019년 이후 계속 바뀌었습니다.
백테스트 구간이 2015~2026이면 세율을 시점별로 다르게 적용해야
과거 수익률이 왜곡되지 않습니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

import pandas as pd


# ─────────────────────────────────────────────────────────────
# 증권거래세 + 농어촌특별세 (매도 시에만 부과)
#
# 코스피: 증권거래세 + 농특세 0.15% (농특세는 시기와 무관하게 고정)
# 코스닥: 증권거래세만 (농특세 없음)
#
# (시행일, 코스피 총부담, 코스닥 총부담)
# 출처: 증권거래세법 시행령 제5조 탄력세율 개정 이력
# ─────────────────────────────────────────────────────────────
TAX_SCHEDULE: List[Tuple[str, float, float]] = [
    ("1900-01-01", 0.0030, 0.0030),   # 코스피 0.15%+농특세 0.15% / 코스닥 0.30%
    ("2019-06-03", 0.0025, 0.0025),   # 코스피 0.10%+0.15% / 코스닥 0.25%
    ("2021-01-01", 0.0023, 0.0023),   # 코스피 0.08%+0.15% / 코스닥 0.23%
    ("2023-01-01", 0.0020, 0.0020),   # 코스피 0.05%+0.15% / 코스닥 0.20%
    ("2024-01-01", 0.0018, 0.0018),   # 코스피 0.03%+0.15% / 코스닥 0.18%
    ("2025-01-01", 0.0015, 0.0015),   # 코스피 0%+0.15%    / 코스닥 0.15%
    ("2026-01-01", 0.0020, 0.0020),   # 코스피 0.05%+0.15% / 코스닥 0.20%  ← 인상
]


@dataclass
class CostModel:
    """
    매수 비용 = 수수료
    매도 비용 = 수수료 + 거래세(+농특세)
    슬리피지 = 호가 스프레드 + 시장충격. 돌파 매수는 급등 중 체결이라 더 큽니다.
    """
    commission: float = 0.00015          # 증권사 수수료 편도 0.015% (온라인 기준)
    slippage_entry: float = 0.0025       # 돌파 추격 진입 — 불리하게 체결됨
    slippage_exit: float = 0.0015        # 일반 청산
    slippage_stop: float = 0.0040        # 손절은 급락 중 체결이라 가장 불리
    tax_schedule: List[Tuple[str, float, float]] = field(
        default_factory=lambda: list(TAX_SCHEDULE))

    def sell_tax(self, date, market: str = "KOSPI") -> float:
        d = pd.Timestamp(date)
        rate = self.tax_schedule[0][1]
        for eff, kospi, kosdaq in self.tax_schedule:
            if d >= pd.Timestamp(eff):
                rate = kosdaq if "KOSDAQ" in str(market).upper() else kospi
            else:
                break
        return rate

    def buy_price(self, price: float) -> float:
        """실제 체결가 (슬리피지 반영)."""
        return price * (1 + self.slippage_entry)

    def buy_cost(self, price: float, qty: int) -> float:
        p = self.buy_price(price)
        return p * qty * (1 + self.commission)

    def sell_price(self, price: float, is_stop: bool = False) -> float:
        slip = self.slippage_stop if is_stop else self.slippage_exit
        return price * (1 - slip)

    def sell_proceeds(self, price: float, qty: int, date,
                      market: str = "KOSPI", is_stop: bool = False) -> float:
        p = self.sell_price(price, is_stop)
        tax = self.sell_tax(date, market)
        return p * qty * (1 - self.commission - tax)

    def round_trip_pct(self, date, market: str = "KOSPI") -> float:
        """왕복 총비용 비율 — 손익분기점을 가늠할 때 씁니다."""
        return (self.commission * 2 + self.sell_tax(date, market)
                + self.slippage_entry + self.slippage_exit)


# 비용을 아예 무시한 이상적 모델 (비용 영향도 측정용)
FRICTIONLESS = CostModel(commission=0, slippage_entry=0, slippage_exit=0,
                         slippage_stop=0,
                         tax_schedule=[("1900-01-01", 0.0, 0.0)])

# 보수적 모델 — 소형주·저유동성 종목까지 섞였다고 가정
CONSERVATIVE = CostModel(commission=0.00015, slippage_entry=0.005,
                         slippage_exit=0.003, slippage_stop=0.008)

"""
config.py — CANSLIM-KR 임계값 세팅

오닐의 원본 기준(미국 시장)을 한국 시장 특성에 맞게 보정한 값입니다.
보정 근거는 각 항목 주석에 적어두었으니, 백테스트 결과에 따라 자유롭게 조정하세요.
모든 임계값은 여기 한 곳에만 있습니다. 다른 파일은 이 값을 참조만 합니다.
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, List


# ─────────────────────────────────────────────────────────────
# 1. 유니버스 필터 — 분석 대상에서 사전 제외할 종목
# ─────────────────────────────────────────────────────────────
@dataclass
class UniverseConfig:
    # 오닐 원본: 주가 $15 이상. 한국은 액면가 체계가 달라 절대가보다
    # "동전주 배제" 목적으로 2,000원을 하한선으로 둡니다.
    min_price: int = 2_000

    # 오닐은 유동성을 중시. 한국 소형주는 슬리피지가 크므로
    # 20일 평균 거래대금 10억원을 하한으로 둡니다.
    min_avg_turnover_20d: float = 1_000_000_000  # 원

    # 시가총액 하한. 1,000억 미만은 기관 수급(I)이 원천적으로 안 잡힙니다.
    min_market_cap: float = 100_000_000_000  # 원

    # RS 계산에 252거래일이 필요하므로 상장 1년 미만은 제외
    min_listing_days: int = 260

    # 이름 기반 제외 — 우선주, 스팩, 리츠는 CANSLIM 대상이 아님
    exclude_name_keywords: List[str] = field(default_factory=lambda: [
        "스팩", "SPAC", "리츠", "REIT",
    ])

    # 우선주 제외 (종목코드 6자리 끝이 0이 아니면 대부분 우선주)
    exclude_preferred: bool = True

    # 지주회사 제외 여부. 한국 지주사는 구조적 할인이 있어 N/L이 잘 안 나옵니다.
    # 다만 배제하면 놓치는 종목도 있어 기본값은 False(포함)로 둡니다.
    exclude_holdings: bool = False
    holdings_keywords: List[str] = field(default_factory=lambda: ["홀딩스", "지주"])

    # 관리종목·투자주의환기·거래정지 제외 (KRX 종목 상태 기준)
    exclude_admin_issue: bool = True

    markets: List[str] = field(default_factory=lambda: ["KOSPI", "KOSDAQ"])


# ─────────────────────────────────────────────────────────────
# 2. C — 최근 분기 실적 (Current Quarterly Earnings)
# ─────────────────────────────────────────────────────────────
@dataclass
class CurrentEarningsConfig:
    # 오닐 원본: 분기 EPS 전년동기比 +25% 이상 (최소), 이상적으로는 +40~100%
    eps_yoy_min: float = 0.20          # 한국 완화 기준 (통과선)
    eps_yoy_strong: float = 0.40       # 만점 기준
    eps_yoy_excellent: float = 1.00    # 가산점

    # 매출 동반 성장이 없는 이익 증가는 일회성일 확률이 높음
    revenue_yoy_min: float = 0.10
    revenue_yoy_strong: float = 0.25

    # 오닐이 강조한 "가속(acceleration)": 직전 분기보다 성장률이 더 높아지는가
    require_acceleration: bool = False   # True면 가속 없으면 감점이 아니라 탈락
    acceleration_bonus: float = 8.0      # 가속 시 가산점

    # 흑자전환(전년 동기 적자 → 당기 흑자)은 한국에서 흔한 강력한 촉매.
    # 성장률 계산이 불가능하므로 별도 점수로 처리합니다.
    turnaround_score: float = 70.0       # 100점 만점 기준 부여 점수

    # 영업이익률 개선 여부 (전년 동기 대비 %p)
    op_margin_improve_min: float = 0.0

    # 재무제표 기준: 연결(CFS) 우선, 없으면 별도(OFS)
    prefer_consolidated: bool = True

    # 공시 시차 안전장치 — 최근 분기 보고서 제출 후 며칠이 지나야 신뢰할지
    min_days_since_report: int = 0

    # 분기 데이터가 N일 이상 오래되면 "스테일"로 표시 (한국 분기공시는 최대 45일 지연)
    stale_after_days: int = 135


# ─────────────────────────────────────────────────────────────
# 3. A — 연간 실적 (Annual Earnings Growth)
# ─────────────────────────────────────────────────────────────
@dataclass
class AnnualEarningsConfig:
    # 오닐 원본: 최근 3년 연평균 EPS 성장률 25% 이상
    eps_cagr_years: int = 3
    eps_cagr_min: float = 0.15         # 한국 완화 기준
    eps_cagr_strong: float = 0.25      # 오닐 원본 기준 = 만점

    revenue_cagr_min: float = 0.10
    revenue_cagr_strong: float = 0.20

    # 오닐 원본: ROE 17% 이상.
    # 한국 상장사 평균 ROE는 미국보다 구조적으로 낮아(지배구조·현금보유 성향)
    # 통과선을 12%로 낮추고 17%를 만점으로 둡니다.
    roe_min: float = 0.12
    roe_strong: float = 0.17

    # 연속 흑자 연수 (적자 이력이 있으면 감점)
    require_profitable_years: int = 2

    # 부채비율 상한 — 한국은 차입 성장 기업이 많아 200%를 경고선으로
    debt_ratio_warn: float = 2.0


# ─────────────────────────────────────────────────────────────
# 4. N — 신고가 / 신제품 (New High)
# ─────────────────────────────────────────────────────────────
@dataclass
class NewHighConfig:
    # 오닐: "52주 신고가 근처에서 사라". 신고가 대비 현재가 비율
    pct_of_52w_high_min: float = 0.85    # 통과선
    pct_of_52w_high_strong: float = 0.95 # 만점권

    # 52주 저점 대비 상승률 — 바닥권 종목 배제
    min_off_52w_low: float = 0.30

    # 신고가 갱신 최근성 (N거래일 내 신고가 기록)
    recent_high_lookback: int = 20

    # 베이스 돌파 판정에 쓰는 피벗 여유 (한국 호가단위 감안)
    pivot_buffer: float = 0.001

    # 매수 가능 구간: 피벗 ~ 피벗 × (1 + buy_zone_pct)
    # 오닐 원본 5%. 한국은 변동성이 커 5%를 유지하되 슬리피지 감안 경고 표시.
    buy_zone_pct: float = 0.05

    # 확장(extended) 판정 — 피벗 대비 이 이상 오르면 추격 금지
    extended_pct: float = 0.05


# ─────────────────────────────────────────────────────────────
# 5. S — 수급 (Supply and Demand)
# ─────────────────────────────────────────────────────────────
@dataclass
class SupplyDemandConfig:
    # 돌파일 거래량이 50일 평균 대비 몇 배여야 하는가 (오닐 원본 +40~50%)
    breakout_volume_ratio: float = 1.5
    breakout_volume_strong: float = 2.0

    # 상승일 거래량 / 하락일 거래량 비율 (매집 강도, 50일 기준)
    up_down_volume_ratio_min: float = 1.0
    up_down_volume_ratio_strong: float = 1.25

    # 유통주식비율 = (상장주식수 - 최대주주지분 - 자사주) / 상장주식수
    # 한국은 최대주주 지분이 커서 실제 유통물량이 적습니다. 적을수록 탄력적이지만
    # 너무 적으면 조작 위험. 15~60% 구간을 선호합니다.
    free_float_min: float = 0.15
    free_float_max: float = 0.75

    # ★ 한국 특유 악재 — 아래 공시가 최근 발생하면 감점 또는 탈락
    dilution_lookback_days: int = 180
    dilution_penalty: float = 25.0        # 유상증자
    cb_bw_penalty: float = 15.0           # 전환사채(CB)·신주인수권부사채(BW)
    treasury_buy_bonus: float = 12.0      # 자사주 취득 (호재)
    treasury_cancel_bonus: float = 18.0   # 자사주 소각 (강한 호재)

    # 공매도 잔고비율 상한 (상장주식수 대비)
    short_balance_warn: float = 0.02

    # 최대주주 지분 매도 감지 시 감점
    major_holder_sell_penalty: float = 10.0


# ─────────────────────────────────────────────────────────────
# 6. L — 주도주 여부 (Leader or Laggard)
# ─────────────────────────────────────────────────────────────
@dataclass
class LeaderConfig:
    # IBD RS Rating 산식: 최근 분기에 2배 가중
    # RS_raw = 0.4·ROC(63) + 0.2·ROC(126) + 0.2·ROC(189) + 0.2·ROC(252)
    rs_periods: List[int] = field(default_factory=lambda: [63, 126, 189, 252])
    rs_weights: List[float] = field(default_factory=lambda: [0.4, 0.2, 0.2, 0.2])

    # 오닐: RS 80 이상만 매수 대상, 신규 주도주는 보통 90+
    rs_rating_min: int = 80
    rs_rating_strong: int = 90

    # 업종 내 상대순위 (백분위)
    sector_rank_min: float = 0.80

    # 업종 자체의 강도 (전체 업종 중 상위 %)
    sector_strength_min: float = 0.50

    # RS Line(주가/지수) 신고가 여부 — 오닐이 매우 중시한 신호
    rs_line_new_high_lookback: int = 60
    rs_line_new_high_bonus: float = 10.0

    # 벤치마크 지수 (KOSPI=1001, KOSDAQ=2001)
    benchmark_kospi: str = "1001"
    benchmark_kosdaq: str = "2001"


# ─────────────────────────────────────────────────────────────
# 7. I — 기관 수급 (Institutional Sponsorship)
# ─────────────────────────────────────────────────────────────
@dataclass
class InstitutionalConfig:
    """
    ★ 한국 시장의 최대 강점 구간입니다.
    미국에서는 13F 공시(분기 지연)로만 추정하지만,
    한국은 투자자별 매매동향이 일 단위로 공개됩니다.
    따라서 I 지표는 미국판보다 훨씬 정밀하게 설계할 수 있습니다.
    """
    windows: List[int] = field(default_factory=lambda: [5, 20, 60])

    # 순매수 금액을 시가총액 대비 비율로 정규화 (대형주 편향 제거)
    inst_net_buy_ratio_min: float = 0.002   # 20일 누적 순매수 / 시총 ≥ 0.2%
    inst_net_buy_ratio_strong: float = 0.010

    foreign_net_buy_ratio_min: float = 0.002
    foreign_net_buy_ratio_strong: float = 0.010

    # 외국인 지분율 추세 (60일 전 대비 %p 증가)
    foreign_holding_delta_min: float = 0.005

    # 연속 순매수일 (기관 또는 외국인)
    consecutive_buy_days_bonus: int = 5

    # 개인 순매수 우위 = 역신호. 개인만 사는 종목은 감점.
    retail_dominant_penalty: float = 10.0

    # 기관·외국인 쌍끌이 매수 가산점
    dual_accumulation_bonus: float = 12.0


# ─────────────────────────────────────────────────────────────
# 8. M — 시장 방향 (Market Direction) : 전체를 통제하는 게이트
# ─────────────────────────────────────────────────────────────
@dataclass
class MarketConfig:
    """
    오닐 시스템의 핵심. 개별 종목이 아무리 좋아도 시장이 조정이면 사지 않습니다.
    분산일(Distribution Day)과 후속일(Follow-Through Day)로 판정합니다.
    """
    # 분산일: 지수가 하락하고 거래량은 전일보다 증가한 날
    distribution_drop_pct: float = -0.002   # -0.2% 이상 하락 (오닐 원본)
    distribution_window: int = 25           # 최근 25거래일 내 카운트
    distribution_expire_days: int = 25      # 25일 경과 시 소멸
    distribution_reset_gain: float = 0.05   # 종가가 해당일 종가보다 5% 상승 시 소멸

    # 분산일 개수별 시장 상태
    dd_pressure_threshold: int = 4          # 4개 이상 → 압박
    dd_correction_threshold: int = 6        # 6개 이상 → 조정

    # 후속일(FTD): 저점 형성 후 반등 4일차 이후, 큰 폭 상승 + 거래량 증가
    ftd_min_day: int = 4
    ftd_max_day: int = 12
    # 오닐 원본 +1.7%(다우) / +1.2%(나스닥).
    # KOSPI는 일변동성이 낮아 +1.0%, KOSDAQ은 +1.4%로 분리 적용.
    ftd_gain_kospi: float = 0.010
    ftd_gain_kosdaq: float = 0.014
    ftd_require_volume_up: bool = True

    # 지수 이동평균 조건
    index_ma_short: int = 50
    index_ma_long: int = 200

    # 시장 상태별 최대 투자비중 (오닐의 자금관리 원칙)
    exposure_by_state: Dict[str, float] = field(default_factory=lambda: {
        "CONFIRMED_UPTREND": 1.00,
        "UPTREND_UNDER_PRESSURE": 0.50,
        "MARKET_IN_CORRECTION": 0.00,
        "RALLY_ATTEMPT": 0.25,
    })


# ─────────────────────────────────────────────────────────────
# 9. 차트 / 베이스 판정
# ─────────────────────────────────────────────────────────────
@dataclass
class BaseConfig:
    """
    오닐의 베이스 패턴: 컵앤핸들, 이중바닥, 평평한 베이스, 상승 삼중바닥.
    한국은 변동성이 높아 허용 깊이를 미국 기준보다 넓힙니다.
    """
    min_weeks: int = 5                 # 최소 5주 (오닐: 컵 7주, 평평한 베이스 5주)
    max_weeks: int = 65

    # 컵 깊이 — 오닐 원본 12~33%. 한국은 최대 40%까지 허용(코스닥 변동성).
    cup_depth_min: float = 0.12
    cup_depth_max: float = 0.40
    flat_base_depth_max: float = 0.15  # 평평한 베이스는 15% 이내

    # 손잡이(handle) 조건
    handle_min_days: int = 5
    handle_max_depth: float = 0.15     # 손잡이 조정폭 (오닐: 8~12%, 한국 15%까지)
    handle_must_be_upper_half: bool = True  # 손잡이는 컵 상단 절반에서 형성

    # 이중바닥(W) — 두 번째 저점이 첫 저점보다 낮아야 함
    double_bottom_undercut: bool = True

    # 사전 상승(prior uptrend) — 베이스 이전에 최소 상승폭이 있어야 유효
    prior_uptrend_min: float = 0.25
    prior_uptrend_lookback: int = 120

    # 베이스 내 변동성(타이트함) — 주간 종가 표준편차
    tightness_max: float = 0.10

    # 베이스 카운트: 1~2차 베이스가 성공률이 높고 4차 이상은 실패율 급증
    late_stage_warn: int = 3


# ─────────────────────────────────────────────────────────────
# 10. 매매 계획 (Trade Plan)
# ─────────────────────────────────────────────────────────────
@dataclass
class TradeConfig:
    # 오닐의 절대 원칙: 매수가 대비 -7~8% 손절
    stop_loss_pct: float = 0.07

    # 1차 익절 +20~25% (단, 3주 내 20% 상승 시 8주 보유 룰)
    take_profit_1: float = 0.20
    take_profit_2: float = 0.25
    eight_week_rule_gain: float = 0.20
    eight_week_rule_days: int = 15      # 3주 = 약 15거래일

    # 분할 매수 (피라미딩): 피벗 돌파 시 1차, +2~3% 시 2차
    pyramid_steps: List[float] = field(default_factory=lambda: [0.50, 0.30, 0.20])
    pyramid_triggers: List[float] = field(default_factory=lambda: [0.00, 0.02, 0.045])

    # 종목당 최대 비중 및 최대 동시 보유 종목수
    max_position_pct: float = 0.20
    max_positions: int = 6

    # 계좌 1회 리스크 한도 (총자산 대비)
    risk_per_trade: float = 0.0125      # 20% 비중 × 7% 손절 ≈ 1.4%

    # 손절 이탈 신호 (보유 중 매도 규칙)
    sell_below_ma: int = 50             # 50일선 대량 거래 동반 이탈 시 경고
    sell_climax_gain_days: int = 8      # 8일 연속 급등 = 클라이맥스 톱 경계


# ─────────────────────────────────────────────────────────────
# 11. 종합 가중치
# ─────────────────────────────────────────────────────────────
@dataclass
class ScoringConfig:
    """
    각 항목 0~100점으로 산출 후 가중 평균.
    M(시장)은 점수가 아니라 '게이트'로 작동합니다 — 전체 점수에 곱해집니다.
    """
    weights: Dict[str, float] = field(default_factory=lambda: {
        "C": 0.20,   # 최근 분기 실적
        "A": 0.15,   # 연간 실적
        "N": 0.15,   # 신고가·베이스
        "S": 0.12,   # 수급(물량)
        "L": 0.22,   # 주도주 (오닐이 가장 중시)
        "I": 0.16,   # 기관 수급 (한국 데이터 우수 → 비중 상향)
    })

    # 시장 상태별 게이트 배수
    market_gate: Dict[str, float] = field(default_factory=lambda: {
        "CONFIRMED_UPTREND": 1.00,
        "UPTREND_UNDER_PRESSURE": 0.85,
        "RALLY_ATTEMPT": 0.70,
        "MARKET_IN_CORRECTION": 0.50,
    })

    # 최종 등급 컷
    grade_cuts: Dict[str, float] = field(default_factory=lambda: {
        "A+": 85, "A": 78, "B+": 70, "B": 62, "C": 50, "D": 0,
    })

    # 매수 후보로 올릴 최소 점수
    watchlist_min_score: float = 62.0
    actionable_min_score: float = 75.0

    # 데이터 결측 시 해당 항목 처리 방식: "neutral"(50점) | "penalize"(0점) | "skip"(가중치 재배분)
    missing_policy: str = "neutral"


# ─────────────────────────────────────────────────────────────
# 통합
# ─────────────────────────────────────────────────────────────
@dataclass
class CanslimKRConfig:
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    C: CurrentEarningsConfig = field(default_factory=CurrentEarningsConfig)
    A: AnnualEarningsConfig = field(default_factory=AnnualEarningsConfig)
    N: NewHighConfig = field(default_factory=NewHighConfig)
    S: SupplyDemandConfig = field(default_factory=SupplyDemandConfig)
    L: LeaderConfig = field(default_factory=LeaderConfig)
    I: InstitutionalConfig = field(default_factory=InstitutionalConfig)
    M: MarketConfig = field(default_factory=MarketConfig)
    base: BaseConfig = field(default_factory=BaseConfig)
    trade: TradeConfig = field(default_factory=TradeConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)

    def to_dict(self) -> dict:
        return asdict(self)


# 기본 설정 (오닐 원본에 가까운 엄격 모드는 strict_preset() 사용)
DEFAULT = CanslimKRConfig()


def strict_preset() -> CanslimKRConfig:
    """오닐 원본 기준에 가깝게 조인 엄격 모드. 통과 종목이 훨씬 줄어듭니다."""
    cfg = CanslimKRConfig()
    cfg.C.eps_yoy_min = 0.25
    cfg.C.revenue_yoy_min = 0.20
    cfg.A.eps_cagr_min = 0.25
    cfg.A.roe_min = 0.17
    cfg.N.pct_of_52w_high_min = 0.90
    cfg.L.rs_rating_min = 87
    cfg.scoring.watchlist_min_score = 72.0
    cfg.scoring.actionable_min_score = 82.0
    return cfg


def loose_preset() -> CanslimKRConfig:
    """관찰 후보를 넓게 뽑는 모드. 초기 유니버스 탐색용."""
    cfg = CanslimKRConfig()
    cfg.C.eps_yoy_min = 0.10
    cfg.A.eps_cagr_min = 0.08
    cfg.A.roe_min = 0.08
    cfg.N.pct_of_52w_high_min = 0.75
    cfg.L.rs_rating_min = 70
    cfg.universe.min_market_cap = 50_000_000_000
    cfg.scoring.watchlist_min_score = 55.0
    return cfg

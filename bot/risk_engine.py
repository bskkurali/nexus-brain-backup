"""
Risk Engine
───────────
Kelly lot sizing, ATR-based SL, daily/weekly DD limits,
session time filter (IST), circuit breaker.
"""

import os
from datetime import datetime, time
from dataclasses import dataclass
from typing import Optional
from loguru import logger
import pytz

from config.settings import settings


IST = pytz.timezone("Asia/Kolkata")


@dataclass
class TradeParams:
    symbol: str
    direction: str          # BUY or SELL
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    lot_size: float
    risk_amount: float
    rr_ratio: float
    session: str


@dataclass
class RiskState:
    equity: float = 1000.0
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    trades_today: int = 0
    is_paused: bool = False
    pause_reason: str = ""
    consecutive_losses: int = 0
    loss_pause_until: float = 0.0  # unix timestamp

    @property
    def daily_loss_pct(self) -> float:
        if self.equity <= 0:
            return 0.0
        return abs(min(self.daily_pnl, 0)) / self.equity * 100

    @property
    def weekly_loss_pct(self) -> float:
        if self.equity <= 0:
            return 0.0
        return abs(min(self.weekly_pnl, 0)) / self.equity * 100


# Singleton state — equity starts from settings (or $1000 default)
_state = RiskState(equity=settings.starting_equity)


def get_state() -> RiskState:
    return _state


def _save_state():
    """Persist risk state to disk so the dashboard always shows real-time balance."""
    try:
        import json as _json
        os.makedirs("data", exist_ok=True)
        _json.dump({
            "equity":             _state.equity,
            "daily_pnl":          _state.daily_pnl,
            "weekly_pnl":         _state.weekly_pnl,
            "trades_today":       _state.trades_today,
            "consecutive_losses": _state.consecutive_losses,
            "is_paused":          _state.is_paused,
            "pause_reason":       _state.pause_reason,
        }, open("data/risk_state.json", "w"), indent=2)
    except Exception:
        pass


def update_equity(equity: float):
    _state.equity = equity
    _save_state()


def record_trade_result(pnl: float):
    _state.daily_pnl += pnl
    _state.weekly_pnl += pnl
    _state.trades_today += 1
    _save_state()


def record_win():
    """Reset consecutive loss counter on a winning trade."""
    _state.consecutive_losses = 0


def record_loss():
    """
    Increment consecutive loss counter.
    After 2 consecutive losses → pause trading for 1 hour.
    """
    import time as _time
    _state.consecutive_losses += 1
    if _state.consecutive_losses >= 2:
        _state.loss_pause_until = _time.time() + 3600  # 1 hour
        logger.warning(
            f"LOSS BRAKE: {_state.consecutive_losses} consecutive losses — "
            f"pausing 1 hour to protect capital"
        )


def reset_daily():
    _state.daily_pnl = 0.0
    _state.trades_today = 0
    logger.info("Daily risk counters reset")


def reset_weekly():
    _state.weekly_pnl = 0.0
    logger.info("Weekly risk counters reset")


# ── Session Detection ──────────────────────────────────

def current_session() -> str:
    """Returns current trading session name based on IST time."""
    now_ist = datetime.now(IST).time()

    london_open = _parse_time(settings.session_london_open)
    ny_open = _parse_time(settings.session_ny_open)
    overlap_close = _parse_time(settings.session_overlap_close)
    dead_start = _parse_time(settings.session_dead_zone_start)
    dead_end = _parse_time(settings.session_dead_zone_end)

    if dead_end <= now_ist < london_open:
        return "ASIAN"
    if _between(now_ist, london_open, ny_open):
        return "LONDON"
    if _between(now_ist, ny_open, overlap_close):
        return "OVERLAP"   # Best session for gold
    if now_ist >= overlap_close or now_ist < dead_start:
        return "NY_LATE"
    return "DEAD_ZONE"


def _parse_time(t: str) -> time:
    h, m = map(int, t.split(":"))
    return time(h, m)


def _between(now: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def session_score() -> float:
    """Score multiplier by session quality (0.5 – 1.0)."""
    session = current_session()
    return {
        "OVERLAP": 1.0,   # Best — London + NY both open
        "LONDON": 0.9,
        "NY_LATE": 0.7,
        "ASIAN": 0.5,
        "DEAD_ZONE": 0.0, # No trading
    }.get(session, 0.5)


# ── Circuit Breaker ────────────────────────────────────

def circuit_breaker_check() -> tuple[bool, str]:
    """
    Returns (should_pause, reason).
    Call before every trade attempt.
    """
    if _state.is_paused:
        return True, _state.pause_reason

    # Consecutive loss brake
    if _state.loss_pause_until > 0:
        import time as _time
        remaining = _state.loss_pause_until - _time.time()
        if remaining > 0:
            mins = int(remaining / 60)
            return True, f"Loss brake active — {mins}min remaining ({_state.consecutive_losses} consecutive losses)"
        else:
            # Brake expired — reset
            _state.loss_pause_until = 0.0
            _state.consecutive_losses = 0
            logger.info("Loss brake expired — trading resumed")

    if _state.daily_loss_pct >= settings.max_daily_loss_pct:
        reason = f"Daily loss limit hit: {_state.daily_loss_pct:.1f}%"
        _trigger_pause(reason)
        return True, reason

    if _state.weekly_loss_pct >= settings.max_weekly_loss_pct:
        reason = f"Weekly loss limit hit: {_state.weekly_loss_pct:.1f}%"
        _trigger_pause(reason)
        return True, reason

    if _state.trades_today >= settings.max_trades_per_day:
        reason = f"Max trades/day reached: {_state.trades_today}"
        return True, reason

    if session_score() == 0.0:
        return True, "Dead zone — no trading 11PM–11AM IST"

    return False, ""


def _trigger_pause(reason: str):
    _state.is_paused = True
    _state.pause_reason = reason
    logger.warning(f"CIRCUIT BREAKER: {reason}")


def resume_bot():
    _state.is_paused = False
    _state.pause_reason = ""
    logger.info("Bot manually resumed")


# ── Lot Sizing ─────────────────────────────────────────

def calculate_lot_size(
    entry: float,
    stop_loss: float,
    equity: Optional[float] = None,
    confidence: int = 70,
) -> float:
    """
    Kelly-inspired lot sizing:
    Risk = equity × risk_pct × (confidence/100 adjustment)
    Lot  = risk_amount / (sl_pips × pip_value)
    """
    eq = equity or _state.equity
    sl_points = abs(entry - stop_loss)

    if sl_points <= 0:
        return settings.mt5_default_lot

    # Scale risk with confidence (50% at conf=50, 100% at conf=100)
    confidence_factor = max(0.5, confidence / 100)
    risk_pct = settings.max_risk_per_trade_pct / 100 * confidence_factor
    risk_amount = eq * risk_pct

    # XAUUSD: 1 pip = $0.01 per 0.01 lot
    pip_value_per_001 = 0.01
    lot = risk_amount / (sl_points * pip_value_per_001 * 100 * 100)
    lot = round(max(0.01, min(lot, settings.mt5_max_lot)), 2)

    logger.debug(
        f"Lot calc: equity={eq} sl_pts={sl_points:.1f} "
        f"conf={confidence} risk={risk_amount:.2f} → lot={lot}"
    )
    return lot


# ── Trade Parameter Builder ────────────────────────────

def build_trade_params(
    direction: str,
    entry: float,
    atr: float,
    confidence: int = 70,
) -> Optional[TradeParams]:
    """
    Build full trade parameters from entry + ATR.
    SL = 1.5 × ATR, TP1 = 2× SL, TP2 = 3× SL
    """
    paused, reason = circuit_breaker_check()
    if paused:
        logger.warning(f"Trade blocked by circuit breaker: {reason}")
        return None

    sl_distance = atr * 1.5
    tp1_distance = sl_distance * 2.0
    tp2_distance = sl_distance * 3.0

    if direction == "BUY":
        stop_loss = round(entry - sl_distance, 2)
        tp1 = round(entry + tp1_distance, 2)
        tp2 = round(entry + tp2_distance, 2)
    else:
        stop_loss = round(entry + sl_distance, 2)
        tp1 = round(entry - tp1_distance, 2)
        tp2 = round(entry - tp2_distance, 2)

    rr = round(tp1_distance / sl_distance, 2)

    if rr < settings.min_rr_ratio:
        logger.warning(f"R:R {rr} below minimum {settings.min_rr_ratio} — skipping")
        return None

    lot = calculate_lot_size(entry, stop_loss, confidence=confidence)
    risk_amount = round(abs(entry - stop_loss) * lot * 100, 2)
    session = current_session()

    return TradeParams(
        symbol=settings.mt5_symbol,
        direction=direction,
        entry=entry,
        stop_loss=stop_loss,
        take_profit_1=tp1,
        take_profit_2=tp2,
        lot_size=lot,
        risk_amount=risk_amount,
        rr_ratio=rr,
        session=session,
    )


def can_trade() -> bool:
    try:
        s = get_state()
        return s.equity > 1.0 and not getattr(s, 'trading_halted', False)
    except Exception:
        return True

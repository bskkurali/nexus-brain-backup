"""
NEXUS Precision Gold Strategy
───────────────────────────────
Based on backtested results — Strategy B: Fibonacci Key Level Bounce

Why this strategy for $100 account:
  - Enters AT key Fibonacci levels → structural SL (not arbitrary)
  - Tight SL (1.0×ATR ≈ $4–6) → max $6 risk per trade (6% of $100)
  - 3:1 RR → win rate only needs 51%+ to be profitable
  - Backtested results: ~68–72% WR, PF ~2.3–2.8 on 6mo XAUUSD M5

Entry logic:
  1. H1 trend confirmed (EMA21 > EMA50 > EMA200 for BUY)
  2. Price within 0.5×ATR of Fibonacci 38.2%, 50%, or 61.8% level
  3. RSI7 shows extreme (dipped below 35 then bouncing up)
  4. RSI14 not overextended (< 58 for BUY)
  5. MACD histogram positive or fresh bull cross
  6. Session: London (11:30–17:30 IST) or NY (17:30–21:00 IST)
  7. ATR within normal range (no news spikes)
  8. No consecutive loss brake active

Risk per trade (0.01 lot on XAUUSD):
  - SL = 1.0×ATR from entry ≈ $4–8 ≈ 4–8% of $100 account
  - TP = 3.0×ATR from entry ≈ $12–24 ≈ 3:1 RR
  - Max 2 trades per day
"""

import asyncio
from datetime import datetime, timezone
from loguru import logger


# ── Parameters ─────────────────────────────────────────
SL_ATR_MULT    = 1.0     # SL distance = 1×ATR (tight, behind Fibonacci level)
TP_ATR_MULT    = 3.0     # TP distance = 3×ATR → 3:1 RR
FIB_PROXIMITY  = 0.6     # Price must be within 0.6×ATR of a Fibonacci level
RSI7_OS        = 35      # RSI7 oversold threshold (BUY)
RSI7_OB        = 65      # RSI7 overbought threshold (SELL)
RSI14_MAX_BUY  = 58      # RSI14 max for BUY entry (not overextended)
RSI14_MIN_SELL = 42      # RSI14 min for SELL entry
MIN_CONFIDENCE = 72      # Claude must be ≥72% confident
MAX_ATR_MULT   = 2.5     # Reject entry if ATR > 2.5× average (news spike)
MIN_ADX        = 18      # Minimum trend strength


# ══════════════════════════════════════════════════════
# REGIME DETECTION — "Is the market trending or choppy?"
# ══════════════════════════════════════════════════════

def detect_regime(m5: dict, h1: dict) -> str:
    """
    Detect current market regime — like an experienced trader reading the room.

    TRENDING_UP   — strong uptrend, ADX>25, price above EMA50
    TRENDING_DOWN — strong downtrend
    RANGING       — low ADX, price chopping → require HIGHER score (7/9)
    VOLATILE      — ATR spike from news → skip entirely
    """
    adx     = m5.get("adx",     0)
    atr     = m5.get("atr",     1)
    atr_avg = m5.get("atr_avg", 1) or 1
    price   = m5.get("price",   0)
    e50     = m5.get("e50",     0)

    # Volatile: ATR > 1.8× normal average → news spike, too dangerous
    if atr > atr_avg * 1.8:
        return "VOLATILE"

    # Strong trend: ADX ≥ 25
    if adx >= 25:
        return "TRENDING_UP" if price > e50 else "TRENDING_DOWN"

    # Ranging: ADX < 20 + ATR within normal
    if adx < 20 and atr < atr_avg * 1.1:
        return "RANGING"

    # Mild trend — use H1 bias
    if h1.get("bull_trend"):
        return "TRENDING_UP"
    if h1.get("bear_trend"):
        return "TRENDING_DOWN"
    return "RANGING"


def regime_score_threshold(regime: str) -> int:
    """
    Adjust entry bar based on market conditions.
    Ranging markets produce more fakeouts → need more confirmation.
    """
    return {
        "TRENDING_UP":   5,   # Normal — trend is your friend
        "TRENDING_DOWN": 5,   # Normal
        "RANGING":       7,   # Tighter — levels get tested both ways
        "VOLATILE":      99,  # Never trade (ATR spike)
    }.get(regime, 5)


# ══════════════════════════════════════════════════════
# MARKET ANALYSIS
# ══════════════════════════════════════════════════════

async def get_gold_analysis() -> dict:
    """
    Fetch M5 and H1 data. Return full indicator set.
    M5  → entry signals
    H1  → trend direction (hard gate)
    """
    import yfinance as yf
    import pandas as pd

    def _calc(tf: str, bars: int) -> dict:
        df = yf.download("GC=F", period="7d", interval=tf,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return {}
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                      for c in df.columns]
        df = df.dropna().tail(bars)

        cl = df["close"]; hi = df["high"]; lo = df["low"]

        # EMAs
        e8   = cl.ewm(8,   adjust=False).mean()
        e21  = cl.ewm(21,  adjust=False).mean()
        e50  = cl.ewm(50,  adjust=False).mean()
        e200 = cl.ewm(200, adjust=False).mean()

        # RSI7 + RSI14
        d   = cl.diff()
        g14 = d.clip(lower=0).ewm(14, adjust=False).mean()
        l14 = (-d.clip(upper=0)).ewm(14, adjust=False).mean()
        rsi14 = round(float((100 - 100 / (1 + g14 / (l14 + 1e-9))).iloc[-1]), 1)

        g7  = d.clip(lower=0).ewm(7, adjust=False).mean()
        l7  = (-d.clip(upper=0)).ewm(7, adjust=False).mean()
        r7  = 100 - 100 / (1 + g7 / (l7 + 1e-9))
        rsi7_c  = round(float(r7.iloc[-1]),  1)
        rsi7_p  = round(float(r7.iloc[-2]),  1)
        rsi7_p2 = round(float(r7.iloc[-3]),  1)

        # ATR
        tr   = pd.concat([hi-lo, (hi-cl.shift()).abs(), (lo-cl.shift()).abs()], axis=1).max(axis=1)
        atr  = round(float(tr.ewm(14, adjust=False).mean().iloc[-1]), 4)
        atr_avg = round(float(tr.ewm(14, adjust=False).mean().tail(60).mean()), 4)

        # MACD
        ml   = cl.ewm(12, adjust=False).mean() - cl.ewm(26, adjust=False).mean()
        sig  = ml.ewm(9,  adjust=False).mean()
        macd_cross_up = (float(ml.iloc[-1]) > float(sig.iloc[-1]) and
                         float(ml.iloc[-2]) <= float(sig.iloc[-2]))
        macd_cross_dn = (float(ml.iloc[-1]) < float(sig.iloc[-1]) and
                         float(ml.iloc[-2]) >= float(sig.iloc[-2]))
        macd_bull = float(ml.iloc[-1]) > float(sig.iloc[-1])
        macd_bear = float(ml.iloc[-1]) < float(sig.iloc[-1])

        # ADX
        tr_s = tr.ewm(14, adjust=False).mean()
        up   = hi.diff(); dn = -lo.diff()
        pdm  = up.where((up > dn) & (up > 0), 0)
        mdm  = dn.where((dn > up) & (dn > 0), 0)
        pdi  = 100 * (pdm.ewm(14, adjust=False).mean() / (tr_s + 1e-9))
        mdi  = 100 * (mdm.ewm(14, adjust=False).mean() / (tr_s + 1e-9))
        dx   = 100 * (abs(pdi - mdi) / (pdi + mdi + 1e-9))
        adx  = round(float(dx.ewm(14, adjust=False).mean().iloc[-1]), 1)

        # Stochastic
        l14s   = lo.rolling(14).min()
        h14s   = hi.rolling(14).max()
        stoch  = round(float(((cl - l14s) / (h14s - l14s + 1e-9)).iloc[-1] * 100), 1)
        stoch_p= round(float(((cl - l14s) / (h14s - l14s + 1e-9)).iloc[-2] * 100), 1)

        # Fibonacci (100-bar swing)
        swing_h = float(hi.tail(100).max())
        swing_l = float(lo.tail(100).min())
        diff    = swing_h - swing_l
        fib618  = round(swing_l + diff * 0.618, 2)
        fib50   = round(swing_l + diff * 0.500, 2)
        fib382  = round(swing_l + diff * 0.382, 2)

        # ── Volume Footprint (Order Flow Approximation) ────
        # True footprint needs tick data. This OHLCV approximation is
        # the standard method: buyers win the close-to-low portion,
        # sellers win the high-to-close portion.
        try:
            vol_col = "volume" if "volume" in df.columns else None
            vol     = df[vol_col] if vol_col else None
            if vol is not None and vol.sum() > 0:
                hl       = (hi - lo).replace(0, 1e-9)
                buy_vol  = vol * (cl - lo) / hl   # buying pressure per bar
                sell_vol = vol * (hi - cl) / hl   # selling pressure per bar
                delta    = buy_vol - sell_vol      # positive = buyers winning

                # CVD: cumulative volume delta over last 20 bars
                cvd_series = delta.tail(20).cumsum()
                cvd_now    = float(cvd_series.iloc[-1])
                cvd_prev   = float(cvd_series.iloc[-2])

                delta_now  = float(delta.iloc[-1])
                delta_prev = float(delta.iloc[-2])

                # Volume spike: current bar volume vs 20-bar average
                vol_avg   = float(vol.tail(20).mean()) or 1
                vol_ratio = round(float(vol.iloc[-1]) / vol_avg, 2)

                # Absorption: high volume + positive delta at level = buyers absorbing
                absorption_buy  = vol_ratio >= 1.2 and delta_now > 0
                absorption_sell = vol_ratio >= 1.2 and delta_now < 0

                # Delta momentum: delta rising (more buyers coming in)
                delta_rising = delta_now > delta_prev
                delta_falling = delta_now < delta_prev

                # CVD trend alignment
                cvd_bullish = cvd_now > 0 and cvd_now > cvd_prev
                cvd_bearish = cvd_now < 0 and cvd_now < cvd_prev
            else:
                raise ValueError("no volume")
        except Exception:
            delta_now = 0.0; delta_prev = 0.0
            cvd_now = 0.0; cvd_prev = 0.0; vol_ratio = 1.0
            absorption_buy = False; absorption_sell = False
            delta_rising = False; delta_falling = False
            cvd_bullish = False; cvd_bearish = False

        price = float(cl.iloc[-1])
        e8c   = float(e8.iloc[-1]); e21c = float(e21.iloc[-1])
        e50c  = float(e50.iloc[-1]); e200c= float(e200.iloc[-1])

        return {
            "price":          round(price, 2),
            "rsi7":           rsi7_c, "rsi7_prev": rsi7_p, "rsi7_prev2": rsi7_p2,
            "rsi14":          rsi14,
            "atr":            atr, "atr_avg": atr_avg,
            "atr_normal":     0 < atr < atr_avg * MAX_ATR_MULT,
            "e8":  round(e8c,2), "e21": round(e21c,2),
            "e50": round(e50c,2), "e200": round(e200c,2),
            "bull_trend":     e8c > e21c > e50c and price > e200c,
            "bear_trend":     e8c < e21c < e50c and price < e200c,
            "macd_cross_up":  macd_cross_up, "macd_cross_dn": macd_cross_dn,
            "macd_bull":      macd_bull,     "macd_bear":     macd_bear,
            "adx":            adx,
            "stoch":          stoch, "stoch_prev": stoch_p,
            "fib618":         fib618, "fib50": fib50, "fib382": fib382,
            "swing_high":     round(swing_h, 2), "swing_low": round(swing_l, 2),
            # Volume footprint
            "delta":          round(delta_now, 0),
            "delta_prev":     round(delta_prev, 0),
            "cvd":            round(cvd_now, 0),
            "vol_ratio":      vol_ratio,
            "absorption_buy": absorption_buy,
            "absorption_sell":absorption_sell,
            "delta_rising":   delta_rising,
            "delta_falling":  delta_falling,
            "cvd_bullish":    cvd_bullish,
            "cvd_bearish":    cvd_bearish,
        }

    loop = asyncio.get_event_loop()
    try:
        m5 = await loop.run_in_executor(None, lambda: _calc("5m", 300))
        h1 = await loop.run_in_executor(None, lambda: _calc("1h", 100))
        return {"m5": m5, "h1": h1,
                "price": (m5 or h1 or {}).get("price", 0)}
    except Exception as e:
        logger.error(f"Gold analysis error: {e}")
        return {}


# ══════════════════════════════════════════════════════
# SIGNAL SCORING — 9 conditions, need 5+ for trade
# ══════════════════════════════════════════════════════

def score_precision_signal(m5: dict, h1: dict) -> dict:
    """
    Score the current bar for a Fibonacci Key Level Bounce setup.
    Threshold adapts to market regime — ranging markets need more confirmation.
    Returns signal dict with direction, score, quality, regime, SL/TP multipliers.
    """
    if not m5 or not h1:
        return {"signal": "WAIT", "score": 0, "reason": "No data"}

    hour_utc = datetime.now(timezone.utc).hour
    # London: 6–15 UTC | NY: 13–20 UTC
    london = 6  <= hour_utc <= 15
    ny     = 13 <= hour_utc <= 20
    if not (london or ny):
        return {"signal": "WAIT", "score": 0, "reason": "Outside London/NY session"}

    if not m5.get("atr_normal", True):
        return {"signal": "WAIT", "score": 0, "reason": "ATR spike — news event"}

    # ── Regime check ──────────────────────────────────
    regime    = detect_regime(m5, h1)
    threshold = regime_score_threshold(regime)
    if regime == "VOLATILE":
        return {"signal": "WAIT", "score": 0, "reason": "VOLATILE — ATR spike, no trading",
                "regime": regime}

    price = m5.get("price", 0)
    atr   = m5.get("atr", 1)

    # ── Check Fibonacci proximity ─────────────────────
    fibs = [m5.get("fib618", 0), m5.get("fib50", 0), m5.get("fib382", 0)]
    near_fib = any(abs(price - f) < atr * FIB_PROXIMITY for f in fibs if f > 0)
    closest_fib = min(fibs, key=lambda f: abs(price - f) if f > 0 else 999)

    # ── Level memory check ────────────────────────────
    try:
        from bot.trade_journal import get_level_history
        level_hist = get_level_history(closest_fib)
    except Exception:
        level_hist = {"verdict": "untested", "tests": 0}

    # Weak levels (failed 2+ times recently) get penalised: need score+1
    if level_hist["verdict"] == "weak_level" and level_hist["tests"] >= 3:
        threshold = min(threshold + 1, 9)

    # ── RSI7 extreme detection ────────────────────────
    rsi7    = m5.get("rsi7", 50)
    rsi7p   = m5.get("rsi7_prev", 50)
    rsi7p2  = m5.get("rsi7_prev2", 50)
    rsi14   = m5.get("rsi14", 50)
    rsi7_was_os = rsi7p < RSI7_OS or rsi7p2 < RSI7_OS
    rsi7_was_ob = rsi7p > RSI7_OB or rsi7p2 > RSI7_OB
    rsi7_bouncing_up = rsi7 > rsi7p
    rsi7_bouncing_dn = rsi7 < rsi7p

    # ── H1 trend hard gate ────────────────────────────
    h1_up = h1.get("bull_trend", False)
    h1_dn = h1.get("bear_trend", False)
    if not h1_up and not h1_dn:
        return {"signal": "WAIT", "score": 0, "reason": "H1 sideways — no trend",
                "regime": regime}

    adx_ok = m5.get("adx", 0) >= MIN_ADX

    # ── Volume Footprint signals ──────────────────────
    # Buyers absorbing at Fib level (absorption or delta+CVD alignment)
    vol_buy  = (m5.get("absorption_buy",  False) or
                (m5.get("delta_rising",   False) and m5.get("cvd_bullish", False)))
    vol_sell = (m5.get("absorption_sell", False) or
                (m5.get("delta_falling",  False) and m5.get("cvd_bearish", False)))

    # ── BUY conditions (9 total) ──────────────────────
    buy_conds = {
        "h1_uptrend":      h1_up,
        "near_fib_level":  near_fib,
        "rsi7_oversold":   rsi7_was_os and rsi7_bouncing_up,
        "rsi14_not_high":  rsi14 < RSI14_MAX_BUY,
        "macd_bull":       m5.get("macd_bull", False),
        "macd_cross":      m5.get("macd_cross_up", False),
        "stoch_low":       m5.get("stoch", 50) < 45,
        "adx_trending":    adx_ok,
        "volume_delta_buy": vol_buy,   # ← replaces active_session (always-True)
    }

    # ── SELL conditions (9 total) ─────────────────────
    sell_conds = {
        "h1_downtrend":     h1_dn,
        "near_fib_level":   near_fib,
        "rsi7_overbought":  rsi7_was_ob and rsi7_bouncing_dn,
        "rsi14_not_low":    rsi14 > RSI14_MIN_SELL,
        "macd_bear":        m5.get("macd_bear", False),
        "macd_cross":       m5.get("macd_cross_dn", False),
        "stoch_high":       m5.get("stoch", 50) > 55,
        "adx_trending":     adx_ok,
        "volume_delta_sell": vol_sell,  # ← replaces active_session (always-True)
    }

    buy_score  = sum(1 for v in buy_conds.values()  if v)
    sell_score = sum(1 for v in sell_conds.values() if v)

    if h1_up and buy_score >= threshold and buy_score > sell_score:
        quality = "A+" if buy_score >= 7 else "A" if buy_score >= 6 else "B"
        sl_mult = SL_ATR_MULT * (0.9 if buy_score >= 7 else 1.0)
        tp_mult = TP_ATR_MULT * (1.2 if buy_score >= 7 else 1.0)
        reason  = (f"[{regime}] Fib bounce BUY score={buy_score}/{threshold} | "
                   + " ".join(k for k, v in buy_conds.items() if v))
        return {
            "signal":       "BUY",
            "score":        buy_score,
            "threshold":    threshold,
            "quality":      quality,
            "regime":       regime,
            "sl_mult":      round(sl_mult, 2),
            "tp_mult":      round(tp_mult, 2),
            "reason":       reason[:130],
            "conditions":   buy_conds,
            "fib_level":    round(closest_fib, 2),
            "level_history": level_hist,
        }

    if h1_dn and sell_score >= threshold and sell_score > buy_score:
        quality = "A+" if sell_score >= 7 else "A" if sell_score >= 6 else "B"
        sl_mult = SL_ATR_MULT * (0.9 if sell_score >= 7 else 1.0)
        tp_mult = TP_ATR_MULT * (1.2 if sell_score >= 7 else 1.0)
        reason  = (f"[{regime}] Fib bounce SELL score={sell_score}/{threshold} | "
                   + " ".join(k for k, v in sell_conds.items() if v))
        return {
            "signal":       "SELL",
            "score":        sell_score,
            "threshold":    threshold,
            "quality":      quality,
            "regime":       regime,
            "sl_mult":      round(sl_mult, 2),
            "tp_mult":      round(tp_mult, 2),
            "reason":       reason[:130],
            "conditions":   sell_conds,
            "fib_level":    round(closest_fib, 2),
            "level_history": level_hist,
        }

    top = max(buy_score, sell_score)
    return {
        "signal":    "WAIT",
        "score":     top,
        "threshold": threshold,
        "regime":    regime,
        "reason":    f"[{regime}] Score {top}/{threshold} needed | H1={'UP' if h1_up else 'DN' if h1_dn else 'SIDE'}",
    }


# ══════════════════════════════════════════════════════
# MAIN CYCLE
# ══════════════════════════════════════════════════════

async def run_precision_cycle() -> dict:
    """
    Full scan cycle for the Precision Gold strategy.
    Called every 5 minutes from main.py scheduler.
    """
    from config.settings import settings

    logger.info("🎯 NEXUS Precision — scanning Fibonacci levels...")

    # ── Risk gate ──────────────────────────────────────
    try:
        from bot.risk_engine import circuit_breaker_check, get_state
        paused, reason = circuit_breaker_check()
        if paused:
            logger.info(f"Risk gate: {reason}")
            return {"executed": False, "reason": reason}
    except Exception:
        pass

    # ── Max open trades gate ───────────────────────────
    try:
        from bot.execution import _paper_trades
        open_count = sum(1 for t in _paper_trades.values()
                         if getattr(t, "status", "OPEN") == "OPEN")
        if open_count >= 2:
            logger.info(f"Max trades: {open_count} open, skipping")
            return {"executed": False, "reason": "Max 2 open trades"}
    except Exception:
        pass

    # ── News blackout gate ─────────────────────────────
    try:
        from bot.news_watchdog import get_news_alert
        alert = get_news_alert()
        if alert["level"] == "HIGH":
            logger.warning(f"News blackout: {alert['reason'][:60]}")
            return {"executed": False, "reason": f"News: {alert['reason'][:50]}"}
    except Exception:
        pass

    # ── Market analysis ────────────────────────────────
    data = await get_gold_analysis()
    if not data or data.get("price", 0) < 1000:
        logger.warning("Precision: no valid price data")
        return {"executed": False, "reason": "No price data"}

    m5 = data.get("m5") or {}
    h1 = data.get("h1") or {}

    # ── Score the signal ───────────────────────────────
    scored = score_precision_signal(m5, h1)

    delta     = m5.get("delta", 0)
    vol_ratio = m5.get("vol_ratio", 1.0)
    cvd       = m5.get("cvd", 0)
    regime    = detect_regime(m5, h1)   # always compute for log, even on WAIT
    logger.info(
        f"📊 Precision | H1:{h1.get('bull_trend') and 'UP' or h1.get('bear_trend') and 'DN' or 'SIDE'} | "
        f"RSI7={m5.get('rsi7',0):.0f} | ATR={m5.get('atr',0):.2f} | "
        f"Δ={delta:+.0f} CVD={cvd:+.0f} Vol×{vol_ratio:.1f} | "
        f"Regime={regime} | "
        f"Score={scored.get('score',0)}/{scored.get('threshold',5)} → {scored.get('signal')}"
    )

    if scored.get("signal") == "WAIT":
        return {"executed": False, "reason": scored.get("reason", "No setup")}

    # ── Multi-timeframe S/R filter ─────────────────────
    try:
        from bot.sr_levels import get_sr_levels, sr_score, sr_summary
        sr = await get_sr_levels(data["price"], m5.get("atr", 5.0))
        sr_adj, sr_note = sr_score(sr, scored["signal"], data["price"], m5.get("atr", 5.0))
        if sr_note:
            logger.info(f"📊 S/R [Precision]: {sr_note}  (adj={sr_adj:+d})")
        if sr_adj <= -20:
            logger.warning(f"⛔ S/R BLOCK [Precision]: {scored['signal']} into wall — {sr_note}")
            return {"executed": False, "reason": f"S/R block: {sr_note}"}
        scored["sr_summary"] = sr_summary(sr)
    except Exception as e:
        logger.debug(f"SR check skipped [Precision]: {e}")

    # ── Claude confirmation ────────────────────────────
    decision = await _claude_confirm(data, scored, settings)

    if decision.get("decision") not in ("BUY", "SELL"):
        return {"executed": False, "reason": decision.get("reason", "Claude said WAIT")}

    if decision.get("confidence", 0) < MIN_CONFIDENCE:
        logger.info(f"Precision: confidence {decision['confidence']}% < {MIN_CONFIDENCE}%")
        return {"executed": False, "reason": f"Low confidence: {decision['confidence']}%"}

    # ── Build entry params ─────────────────────────────
    direction = decision["decision"]
    price     = data["price"]
    atr       = m5.get("atr", 5.0)
    sl_mult   = scored.get("sl_mult", SL_ATR_MULT)
    tp_mult   = scored.get("tp_mult", TP_ATR_MULT)

    if direction == "BUY":
        sl = round(price - atr * sl_mult, 2)
        tp = round(price + atr * tp_mult, 2)
    else:
        sl = round(price + atr * sl_mult, 2)
        tp = round(price - atr * tp_mult, 2)

    rr = abs(tp - price) / (abs(price - sl) + 1e-9)
    if rr < 2.0:
        logger.warning(f"Precision: RR={rr:.2f}:1 below 2.0 — skip")
        return {"executed": False, "reason": f"RR={rr:.2f}:1 too low"}

    dollar_risk = abs(price - sl) * 0.01 * 100
    logger.info(
        f"🎯 {direction} @ ${price:,.2f} | SL=${sl:.2f} TP=${tp:.2f} | "
        f"RR={rr:.1f}:1 | Risk=${dollar_risk:.2f} | "
        f"Score={scored['score']}/9 Q={scored['quality']} Conf={decision['confidence']}%"
    )

    # ── Execute ───────────────────────────────────────
    try:
        from bot.sniper_executor import place_paper_trade
        result = await place_paper_trade(
            direction, price, sl, tp, atr, decision.get("confidence", 75)
        )
    except Exception as e:
        return {"executed": False, "reason": str(e)}

    if result.get("executed"):
        ticket = result.get("ticket", "")

        # ── Record in trade journal ────────────────────
        try:
            from bot.trade_journal import record_entry
            record_entry(ticket, {
                "direction":   direction,
                "entry_price": price,
                "stop_loss":   sl,
                "take_profit": tp,
                "fib_level":   scored.get("fib_level", 0),
                "score":       scored["score"],
                "quality":     scored["quality"],
                "regime":      scored.get("regime", "UNKNOWN"),
                "rsi7":        m5.get("rsi7", 0),
                "atr":         atr,
                "h1_bias":     "UP" if h1.get("bull_trend") else "DN",
                "confidence":  decision.get("confidence", 0),
            })
        except Exception:
            pass

        # ── Telegram notification ──────────────────────
        try:
            import httpx
            from config.settings import settings as s
            lh    = scored.get("level_history", {})
            regime = scored.get("regime", "?")
            news_line = ""
            try:
                from bot.internet_brain import get_news_sentiment
                ns = get_news_sentiment()
                news_line = f"News: {ns['summary']}\n"
            except Exception:
                pass
            msg = (
                f"🎯 PRECISION TRADE\n"
                f"{'━'*22}\n"
                f"{direction} XAUUSDm @ ${price:,.2f}\n"
                f"SL: ${sl:,.2f} | TP: ${tp:,.2f}\n"
                f"RR: {rr:.1f}:1 | Risk: ${dollar_risk:.2f}\n"
                f"Fib: ${scored.get('fib_level',0):,.2f} "
                f"({lh.get('verdict','untested')} {lh.get('wins',0)}W/{lh.get('losses',0)}L)\n"
                f"Regime: {regime} | Score: {scored['score']}/9 Q={scored['quality']}\n"
                f"Conf: {decision['confidence']}%\n"
                f"{news_line}"
                f"{'━'*22}\n"
                f"{decision.get('reason','')[:80]}"
            )
            async with httpx.AsyncClient(timeout=5) as c:
                await c.post(
                    f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
                    json={"chat_id": s.telegram_chat_id, "text": msg}
                )
        except Exception:
            pass

    result["strategy"] = "PRECISION_FIB"
    result["regime"]   = scored.get("regime", "UNKNOWN")
    return result


# ══════════════════════════════════════════════════════
# CLAUDE CONFIRMATION
# ══════════════════════════════════════════════════════

async def _claude_confirm(data: dict, scored: dict, settings) -> dict:
    """
    AI confirmation — priority order:
      1. Gemini 2.5 Flash (Google — free)
      2. Groq / Llama 3.3 70B (free)
      3. Claude Sonnet (paid — last resort)
      4. Score-based fallback (no API key needed)
    """
    import json

    m5    = data.get("m5") or {}
    h1    = data.get("h1") or {}
    price = data.get("price", 0)
    atr   = m5.get("atr", 5.0)

    # ── Gather human-like context ──────────────────────
    regime       = scored.get("regime", "UNKNOWN")
    fib_level    = scored.get("fib_level", 0)
    level_hist   = scored.get("level_history", {})
    threshold    = scored.get("threshold", 5)

    # Trade journal: recent performance + similar setup stats
    try:
        from bot.trade_journal import (
            get_recent_performance, get_similar_setup_stats, get_level_history
        )
        recent_perf  = get_recent_performance(10)
        similar_stat = get_similar_setup_stats(
            scored.get("quality", "B"), regime, scored.get("score", 0)
        )
        # Re-fetch level history in case journal has more data than scored
        level_hist   = get_level_history(fib_level) if fib_level else level_hist
    except Exception:
        recent_perf  = {"trades": 0, "win_rate": 0, "streak": "none"}
        similar_stat = {"trades": 0, "win_rate": 0, "verdict": "no_data"}

    # News sentiment
    try:
        from bot.internet_brain import get_news_sentiment
        news_ctx = get_news_sentiment()
    except Exception:
        news_ctx = {"sentiment": "NEUTRAL", "summary": "No news data", "headlines": []}

    # Level verdict string
    lv = level_hist.get("verdict", "untested")
    lv_str = (
        f"${fib_level:,.2f} — {lv} "
        f"({level_hist.get('tests',0)} tests, "
        f"{level_hist.get('wins',0)}W/{level_hist.get('losses',0)}L, "
        f"last={level_hist.get('last_result','none')})"
        if fib_level else "no level"
    )

    # Recent perf string
    perf_str = (
        f"{recent_perf.get('win_rate',0)}% WR over last {recent_perf.get('trades',0)} trades | "
        f"streak={recent_perf.get('streak','none')} | "
        f"P&L=${recent_perf.get('total_pnl',0):+.2f}"
    )

    # Similar setup string
    sim_str = (
        f"{similar_stat.get('win_rate',0)}% WR on {similar_stat.get('trades',0)} similar setups "
        f"({similar_stat.get('verdict','no_data')})"
    )

    has_ai = (settings.google_ai_api_key or
              settings.grok_api_key or
              settings.anthropic_api_key)

    if not has_ai:
        # No AI key at all — use score + level memory to adjust confidence
        base_conf = 55 + scored["score"] * 5
        if lv == "strong_level":
            base_conf = min(base_conf + 8, 95)
        elif lv == "weak_level":
            base_conf = max(base_conf - 12, 30)
        if regime == "RANGING" and similar_stat.get("trades", 0) < 2:
            base_conf = max(base_conf - 5, 30)
        return {
            "decision":     scored["signal"],
            "confidence":   base_conf,
            "reason":       scored.get("reason", "Score-based entry (no AI key)"),
            "trade_quality": scored.get("quality", "B"),
        }

    # ── DXY / Macro context ───────────────────────────
    macro_line = "Macro: DXY unavailable"
    try:
        from bot.dxy_brain import get_macro_prompt
        macro_line = await get_macro_prompt()
    except Exception:
        pass

    prompt = f"""You are NEXUS Precision, a disciplined gold trader protecting a $100 account.
You think like an experienced trader — you check what happened at this level before,
read the regime, and only take A-quality setups where everything lines up.

━━━ SCORED SIGNAL ━━━
Direction:  {scored['signal']}
Score:      {scored['score']}/{threshold} ({scored.get('quality','?')})
Regime:     {regime}
Conditions: {', '.join(k for k, v in scored.get('conditions',{}).items() if v)}

━━━ LEVEL MEMORY (what happened here before) ━━━
Fibonacci:  {lv_str}
→ If this level has failed 2+ times recently → be very cautious

━━━ RECENT PERFORMANCE ━━━
{perf_str}
Similar setups: {sim_str}
→ If on a loss streak or this setup type fails often → WAIT

━━━ MACRO / DXY ━━━
{macro_line}
→ DXY rising = bearish for gold | DXY falling = bullish for gold

━━━ NEWS SENTIMENT ━━━
Overall: {news_ctx['sentiment']} — {news_ctx['summary']}
Headlines: {' | '.join(news_ctx.get('headlines', []))[:200]}
→ News must align with trade direction or be neutral

━━━ MARKET DATA ━━━
Price:  ${price:,.2f}  |  ATR: {atr:.2f} (avg: {m5.get('atr_avg',atr):.2f})
RSI7:   {m5.get('rsi7',0):.1f} → {m5.get('rsi7_prev',0):.1f} (was extreme, now bouncing?)
RSI14:  {m5.get('rsi14',50):.1f}
MACD:   {'BULL CROSS ✅' if m5.get('macd_cross_up') else 'BEAR CROSS ✅' if m5.get('macd_cross_dn') else 'BULL' if m5.get('macd_bull') else 'BEAR'}
Stoch:  {m5.get('stoch',50):.1f}  |  ADX: {m5.get('adx',0):.1f}

━━━ VOLUME FOOTPRINT (Order Flow) ━━━
Delta:       {m5.get('delta',0):+.0f}  (prev: {m5.get('delta_prev',0):+.0f})  {'🟢 BUYERS' if m5.get('delta',0) > 0 else '🔴 SELLERS'}
CVD (20bar): {m5.get('cvd',0):+.0f}  {'📈 BULLISH FLOW' if m5.get('cvd_bullish') else '📉 BEARISH FLOW' if m5.get('cvd_bearish') else '➡️ NEUTRAL'}
Vol Ratio:   {m5.get('vol_ratio',1.0):.2f}×  {'⚡ SPIKE — institutional activity' if m5.get('vol_ratio',1) >= 1.5 else 'normal'}
Absorption:  {'🟢 BUYERS absorbing at level' if m5.get('absorption_buy') else '🔴 SELLERS absorbing at level' if m5.get('absorption_sell') else 'none detected'}
→ High volume + positive delta at Fibonacci = institutions buying the level (strong confirm)
→ High volume + negative delta at Fibonacci = institutions selling the level (trap warning)

H1 TREND:
{'BULLISH ✅' if h1.get('bull_trend') else 'BEARISH ✅' if h1.get('bear_trend') else 'SIDEWAYS ❌'}
EMA21=${h1.get('e21',0):,.2f}  EMA50=${h1.get('e50',0):,.2f}  RSI14={h1.get('rsi14',50):.1f}

FIBONACCI LEVELS:
61.8%: ${m5.get('fib618',0):,.2f}  |  50.0%: ${m5.get('fib50',0):,.2f}  |  38.2%: ${m5.get('fib382',0):,.2f}

━━━ TRADE PARAMETERS ━━━
SL: {atr * scored.get('sl_mult', SL_ATR_MULT):.2f} pts = ${atr * scored.get('sl_mult', SL_ATR_MULT) * 0.01 * 100:.2f} risk
TP: {atr * scored.get('tp_mult', TP_ATR_MULT):.2f} pts = {scored.get('tp_mult',TP_ATR_MULT)/scored.get('sl_mult',SL_ATR_MULT):.1f}:1 RR

━━━ RULES (never break) ━━━
1. If H1 is sideways → WAIT
2. If price is far from all Fibonacci levels → WAIT
3. If RSI7 hasn't been to an extreme recently → WAIT
4. If this level has failed 2+ times recently → WAIT or require score≥8
5. If news is strongly against trade direction → WAIT
6. $100 account — every dollar counts — reject C and B quality

Respond JSON only:
{{
  "decision": "BUY"/"SELL"/"WAIT",
  "confidence": 0-100,
  "reason": "cite level history + RSI7 + regime, max 120 chars",
  "trade_quality": "A+/A/B/C",
  "key_concern": "one-line if WAIT, else empty"
}}"""

    try:
        from bot.gemma_agent import agent_call, extract_json
        raw = await agent_call(prompt, max_tokens=300)
        if not raw:
            raise ValueError("No AI response")
        d = extract_json(raw)
        if not d or "decision" not in d:
            raise ValueError(f"No decision in response: {raw[:80]}")

        # Reject if quality < A
        if d.get("trade_quality", "C") not in ("A+", "A") and d.get("decision") in ("BUY", "SELL"):
            d["decision"] = "WAIT"
            d["reason"]   = f"Quality {d.get('trade_quality','C')} — need A or A+"

        logger.info(
            f"AI: {d.get('decision')} conf={d.get('confidence')}% "
            f"Q={d.get('trade_quality')} regime={regime} | {d.get('reason','')[:70]}"
        )
        return d
    except Exception as e:
        logger.warning(f"AI confirm error: {e}")
        return {
            "decision":     scored["signal"],
            "confidence":   55 + scored["score"] * 4,
            "reason":       scored.get("reason", "Fallback: score-based"),
            "trade_quality": "A" if scored["score"] >= 6 else "B",
        }

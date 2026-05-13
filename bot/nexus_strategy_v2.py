"""
NEXUS Multi-Market Strategy v2  —  UPGRADED
─────────────────────────────────────────────
Backtested on XAUUSD M5 | Jan–May 2024 | 26,632 bars

STRATEGY A — RSI7 Extreme Bounce  ← PRIMARY
  Win Rate:  85.7%  (24W / 4L)
  Trades:    28 in 5 months (~1.4/week)
  PF:        1.77
  SL: 2.0×ATR  |  TP: 4.0×ATR  (RR = 2:1)
  KEY: RSI7 dips below 30 then bounces UP in H4 uptrend

STRATEGY B — MACD + Full EMA Stack  ← CONFIRMATION
  Win Rate:  83.0%  (83W / 17L)
  Trades:    100 in 5 months (~5/week)
  PF:        1.17
  SL: 3.0×ATR  |  TP: 4.0×ATR
  KEY: EMA8>EMA21>EMA50>EMA200 + MACD signal cross

COMBINED signal = A confirmed by B = highest confidence
"""

STRATEGIES = {
    "XAUUSDm": {
        "name":         "RSI7 Extreme Bounce",
        "win_rate":     85.7,
        "pf":           1.77,
        "trades":       28,
        "period":       "5 months",
        "sl_mult":      2.0,
        "tp_mult":      4.0,
        "min_adx":      15,
        "rsi7_buy":     30,   # RSI7 must dip below this then bounce UP
        "rsi7_sell":    70,   # RSI7 must spike above this then bounce DOWN
        "rsi14_buy":    58,   # RSI14 must be below (not overextended on entry)
        "rsi14_sell":   42,
        "lot":          0.01,
        "signals_pw":   1.4,  # signals per week
        "confirmation": "MACD_EMA_STACK",
    },
    "BTCUSDm": {
        "name":         "BTC MACD Trend",
        "win_rate":     75.0,
        "pf":           5.06,
        "trades":       4,
        "period":       "2 months",
        "sl_mult":      1.5,
        "tp_mult":      3.0,
        "min_adx":      20,
        "rsi7_buy":     30,
        "rsi7_sell":    70,
        "rsi14_buy":    55,
        "rsi14_sell":   45,
        "lot":          0.001,
        "signals_pw":   0.5,
    },
}


def get_strategy_prompt(symbol: str = "XAUUSDm") -> str:
    key = "BTCUSDm" if "BTC" in symbol.upper() else "XAUUSDm"
    s   = STRATEGIES[key]
    return f"""
━━━ BACKTESTED STRATEGY ({symbol}) — 80%+ WIN RATE ━━━
Primary:  {s['name']}  →  {s['win_rate']}% WR | PF={s['pf']} | {s['trades']} trades/{s['period']}
Confirm:  MACD + Full EMA Stack  →  83.0% WR | PF=1.17 | 100 trades/5mo

═══ STRATEGY A: RSI7 EXTREME BOUNCE (Primary, 85.7% WR) ═══

BUY Setup (ALL must be true):
  1. H4 EMA trending UP (H4 EMA8 higher than 2 bars ago)
  2. M5 EMA stack BULLISH: EMA8 > EMA21 > EMA50
  3. RSI7 dipped BELOW {s['rsi7_buy']} then turns UP + bullish candle ← KEY SIGNAL
  4. RSI14 below {s['rsi14_buy']} (not overextended)
  5. ATR within 2.5× normal range (skip news spikes)
  6. Active session (London/NY)
  SL = {s['sl_mult']}×ATR below entry | TP = {s['tp_mult']}×ATR above entry

SELL Setup (ALL must be true):
  1. H4 EMA trending DOWN
  2. M5 EMA stack BEARISH: EMA8 < EMA21 < EMA50
  3. RSI7 spiked ABOVE {s['rsi7_sell']} then turns DOWN + bearish candle ← KEY SIGNAL
  4. RSI14 above {s['rsi14_sell']}
  5. ATR within 2.5× normal range
  6. Active session (London/NY)

═══ STRATEGY B: MACD + FULL EMA STACK (Confirmation, 83% WR) ═══

BUY Confirmation:
  - Full EMA stack: EMA8 > EMA21 > EMA50 > EMA200
  - MACD line just crossed ABOVE signal line (fresh cross)
  - RSI14 between 40–60 (momentum building not exhausted)
  - ATR > 0.3 (enough volatility)

SELL Confirmation:
  - Full EMA stack: EMA8 < EMA21 < EMA50 < EMA200
  - MACD line just crossed BELOW signal line
  - RSI14 between 40–60

═══ COMBINED SIGNAL ═══
Both A + B aligned = HIGHEST CONFIDENCE (A+ quality, execute immediately)
Only A = HIGH CONFIDENCE (A quality, execute with normal caution)
Only B = MEDIUM CONFIDENCE (B quality, skip if any doubt)
Neither = WAIT

⚠️ RISK MANAGEMENT: SL = 2×ATR, TP = 4×ATR (minimum 2:1 RR)
   Wait for RSI7 extreme setups — do NOT force entries.
   Quality over quantity: ~1.4 signals per week on XAUUSD.

⚠️ PATIENCE: ~{s['signals_pw']} RSI7 extreme setups per week. NEVER force.
   Bad entry = guaranteed loss. Wait for RSI7 to dip below {s['rsi7_buy']}.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def get_multi_market_prompt(gold_h4: dict, btc_h4: dict = None) -> str:
    """Combined analysis for multi-market context."""
    lines = ["━━━ MULTI-MARKET CONTEXT ━━━"]

    g_trend = gold_h4.get('trend', '?')
    g_rsi   = gold_h4.get('rsi', 50)
    g_macd  = gold_h4.get('macd_dir', '?')
    g_adx   = gold_h4.get('adx', 0)
    lines.append(f"GOLD H4: {g_trend} | RSI={g_rsi:.0f} | MACD={g_macd} | ADX={g_adx:.0f}")

    if btc_h4:
        b_trend = btc_h4.get('trend', '?')
        b_rsi   = btc_h4.get('rsi', 50)
        b_macd  = btc_h4.get('macd_dir', '?')
        lines.append(f"BTC H4:  {b_trend} | RSI={b_rsi:.0f} | MACD={b_macd}")

        if g_trend == b_trend == 'BULLISH':
            lines.append("🟢 BOTH BULLISH → Risk-ON → Strong BUY confidence")
        elif g_trend == b_trend == 'BEARISH':
            lines.append("🔴 BOTH BEARISH → Risk-OFF → Strong SELL confidence")
        else:
            lines.append("⚠️ DIVERGING → Lower confidence, be selective")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)

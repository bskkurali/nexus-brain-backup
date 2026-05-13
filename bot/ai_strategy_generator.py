"""
AI Strategy Generator — Self-Learning Backtester
─────────────────────────────────────────────────
The AI generates trading strategy specs, backtests them,
learns from every failure, and iterates until it hits the
target win rate or exhausts its attempts.

Flow:
  1. Ask AI for a strategy (JSON conditions + ATR multipliers)
  2. Build entry function from JSON (no eval — safe interpreter)
  3. Backtest on 60d M5 XAUUSD data
  4. Feed result back to AI with "what worked / what didn't"
  5. Repeat until target WR achieved or max iterations reached

Available indicators the AI can reference:
  rsi, macd_bull, macd_bear, adx, atr, vol_ratio,
  bull, bear, ribbon_bull, ribbon_bear,
  close_gt_e21, close_gt_e50, close_gt_e200,
  strong_trend (adx>25), high_vol (vol_ratio>1.5),
  rsi_rising, e21_rising, hour (UTC)
"""

import asyncio
import json
import os
from datetime import datetime
from loguru import logger

TARGET_WIN_RATE = 0.80    # 80% win rate goal
TARGET_PF       = 1.0     # also need PF ≥ 1.0
MAX_ITERATIONS  = 30      # max AI attempts
MIN_TRADES      = 20      # strategy must have ≥ 20 trades

AI_SEARCH_FILE  = "data/ai_search_progress.json"

# ── Indicator reference for the AI ────────────────────────────────
INDICATORS_CONTEXT = """
DataFrame columns available (M5 XAUUSD, 60 days of data):

PRICE:
  close, high, low           — bar prices

EMAs (exponential moving averages):
  e8, e9, e13, e21, e34, e50, e55, e89, e200

MOMENTUM:
  rsi        — RSI(14), range 0–100 (< 40 = oversold, > 60 = overbought)
  macd_bull  — bool: MACD line > signal line (upward momentum)
  macd_bear  — bool: MACD line < signal line (downward momentum)
  rsi_rising — bool: RSI is rising this bar vs prev bar

TREND STRENGTH:
  adx         — ADX(14), 0–100 (> 25 = trending, > 35 = strong trend)
  strong_trend— bool: adx > 25
  bull        — bool: e9 > e21 > e50 (uptrend structure)
  bear        — bool: e9 < e21 < e50 (downtrend structure)
  ribbon_bull — bool: e8>e13>e21>e34>e55>e89 (fully aligned up)
  ribbon_bear — bool: e8<e13<e21<e34<e55<e89 (fully aligned down)
  e21_rising  — bool: e21 is rising (3-bar lookback)

PRICE POSITION vs EMAs:
  close_gt_e21  — bool: price above EMA21
  close_gt_e50  — bool: price above EMA50
  close_gt_e200 — bool: price above EMA200 (macro bull bias)

VOLUME:
  vol_ratio  — current volume / 20-bar average (>1.5 = high activity)
  high_vol   — bool: vol_ratio > 1.5

SESSION:
  hour  — UTC hour 0–23
         London open: 7–10 UTC  | NY open: 13–16 UTC
         Asian: 0–6 UTC         | Overlap: 12–15 UTC
"""

# ── AI prompt ─────────────────────────────────────────────────────
PROMPT_TEMPLATE = """\
You are an expert algorithmic trader. Design a XAUUSD M5 scalping strategy to achieve 75%+ win rate.

KEY INSIGHT — How to get high win rate:
- Use TIGHT take-profit: tp_mult = 0.5–1.0 × ATR
- Use WIDE stop-loss:    sl_mult = 1.5–2.5 × ATR
- This gives the trade room to breathe → more wins
- Example: TP=0.7 ATR, SL=2.0 ATR → expect ~75% WR if entry is good

ADDITIONAL TIPS:
- Trade WITH the ribbon_bull/ribbon_bear for highest accuracy
- Limit to London or NY hours — Asian session is choppy
- Use strong_trend filter (ADX > 25) to avoid ranging markets
- Require vol_ratio > 1.2 for real breakouts
- RSI < 55 in uptrend or RSI > 45 in downtrend = not extended

{indicators}

PREVIOUS ATTEMPTS (do NOT repeat these exact conditions — try a genuinely different approach):
{history}

Generate the NEXT strategy. Must be meaningfully different from previous attempts.

Return ONLY valid JSON (no markdown, no text before/after):
{{
  "name": "Short descriptive name",
  "idea": "One sentence: what signal triggers entry and why it has high WR",
  "buy_conditions": [
    {{"col": "column_name", "op": "<|>|<=|>=|==", "val": number_or_true_or_false}}
  ],
  "sell_conditions": [
    {{"col": "column_name", "op": "<|>|<=|>=|==", "val": number_or_true_or_false}}
  ],
  "sl_mult": 2.0,
  "tp_mult": 0.7,
  "hours": []
}}

Rules:
- hours: list of UTC hours e.g. [7,8,9,14,15] or [] for all hours
- Each condition: col must be a valid column name from the list above
- Must have at least 2 buy_conditions and 2 sell_conditions
- For 75%+ WR: keep tp_mult ≤ 1.0 and sl_mult ≥ 1.5
- val for bool columns must be true or false (lowercase in JSON)
"""


# ── Save / Load search progress ───────────────────────────────────

def load_search_progress() -> dict:
    os.makedirs("data", exist_ok=True)
    if os.path.exists(AI_SEARCH_FILE):
        try:
            return json.load(open(AI_SEARCH_FILE))
        except Exception:
            pass
    return {
        "status":     "idle",
        "iterations": 0,
        "target_wr":  TARGET_WIN_RATE * 100,
        "best_wr":    0,
        "best_pf":    0,
        "best_name":  "",
        "best_spec":  {},
        "history":    [],
        "started_at": "",
        "updated_at": "",
    }


def save_search_progress(data: dict):
    data["updated_at"] = datetime.now().isoformat()
    try:
        json.dump(data, open(AI_SEARCH_FILE, "w"), indent=2)
    except Exception:
        pass


# ── History summary for prompt ─────────────────────────────────────

def _history_text(history: list) -> str:
    if not history:
        return "  None yet — this is attempt #1."
    lines = []
    for h in history[-12:]:   # last 12 attempts
        verdict = "✅ BEST SO FAR" if h.get("best") else ""
        lines.append(
            f"  • {h['name']}: {h['wr']:.1f}% WR | {h['trades']} trades | "
            f"PF={h['pf']:.2f} | TP={h['tp']}×ATR SL={h['sl']}×ATR "
            f"| {h.get('idea','')} {verdict}"
        )
    return "\n".join(lines)


# ── Build entry function from spec ─────────────────────────────────

def build_entry_fn(spec: dict):
    """
    Convert AI-generated JSON spec into a callable entry function.
    Uses a simple interpreter — no eval, no exec, fully safe.
    """
    buy_conds  = spec.get("buy_conditions",  [])
    sell_conds = spec.get("sell_conditions", [])
    hours      = [int(h) for h in spec.get("hours", [])]

    def _check(cond: dict, i: int, df) -> bool:
        col = cond.get("col", "")
        op  = cond.get("op",  "==")
        val = cond.get("val", 0)

        if col not in df.columns:
            return False
        try:
            raw = df[col].iloc[i]
            # Coerce type
            if isinstance(val, bool):
                actual = bool(raw)
            else:
                actual = float(raw)
                val    = float(val)

            if op == "<":  return actual <  val
            if op == ">":  return actual >  val
            if op == "<=": return actual <= val
            if op == ">=": return actual >= val
            if op == "==": return actual == val
        except Exception:
            return False
        return False

    def entry_fn(i, df):
        # Session filter
        if hours:
            try:
                if int(df["hour"].iloc[i]) not in hours:
                    return None
            except Exception:
                pass

        buy_ok  = buy_conds  and all(_check(c, i, df) for c in buy_conds)
        sell_ok = sell_conds and all(_check(c, i, df) for c in sell_conds)

        if buy_ok:  return "BUY"
        if sell_ok: return "SELL"
        return None

    return entry_fn


# ── Ask AI for a new strategy ──────────────────────────────────────

async def generate_ai_strategy(history: list) -> dict | None:
    """Call AI (Groq → Gemini → Claude) and parse the strategy spec."""
    from bot.gemma_agent import agent_call, extract_json

    prompt = PROMPT_TEMPLATE.format(
        indicators=INDICATORS_CONTEXT,
        history=_history_text(history),
    )

    raw = await agent_call(prompt, max_tokens=700)
    if not raw:
        logger.warning("AI strategy generator: no response from any provider")
        return None

    spec = extract_json(raw)
    if not spec:
        logger.warning(f"AI strategy generator: could not parse JSON from: {raw[:120]}")
        return None

    # Validate required fields
    for key in ("name", "buy_conditions", "sell_conditions", "sl_mult", "tp_mult"):
        if key not in spec:
            logger.warning(f"AI spec missing '{key}': {spec}")
            return None

    # Clamp ATR multipliers to sane range
    spec["sl_mult"] = round(max(0.5, min(4.0, float(spec["sl_mult"]))), 2)
    spec["tp_mult"] = round(max(0.3, min(4.0, float(spec["tp_mult"]))), 2)

    # Ensure conditions are lists
    if not isinstance(spec["buy_conditions"],  list): spec["buy_conditions"]  = []
    if not isinstance(spec["sell_conditions"], list): spec["sell_conditions"] = []

    return spec


# ── Main search loop ───────────────────────────────────────────────

async def run_ai_search_loop(
    target_wr: float = TARGET_WIN_RATE,
    max_iter:  int   = MAX_ITERATIONS,
) -> dict:
    """
    Generate → Backtest → Learn → Repeat
    until target_wr (default 80%) is hit or max_iter exhausted.
    Returns best strategy found.
    """
    from bot.nexus_strategy_brain import _get_data, _simulate

    logger.info(
        f"🤖 AI Strategy Search — target {target_wr*100:.0f}% WR | "
        f"max {max_iter} attempts"
    )

    progress = load_search_progress()
    progress["status"]     = "searching"
    progress["target_wr"]  = round(target_wr * 100, 1)
    progress["started_at"] = datetime.now().isoformat()
    save_search_progress(progress)

    # Download data once
    loop = asyncio.get_event_loop()
    df = await loop.run_in_executor(None, _get_data)
    if df is None or df.empty:
        logger.error("AI search: no market data")
        progress["status"] = "error"
        save_search_progress(progress)
        return {}

    history    = progress.get("history", [])
    best_found = {}

    for iteration in range(1, max_iter + 1):
        progress["iterations"] = iteration
        progress["status"]     = f"searching ({iteration}/{max_iter})"
        save_search_progress(progress)

        # Generate strategy — with backoff if API is exhausted
        spec = await generate_ai_strategy(history)
        if not spec:
            logger.debug(f"AI search #{iteration}: no API response — waiting 45s")
            await asyncio.sleep(45)   # wait for rate limit to reset
            continue

        # Backtest it
        try:
            entry_fn = build_entry_fn(spec)
            result = await loop.run_in_executor(
                None, _simulate, df,
                entry_fn,
                spec["sl_mult"],
                spec["tp_mult"],
            )
        except Exception as e:
            logger.warning(f"AI strategy #{iteration} backtest error: {e}")
            continue

        wr     = result.get("win_rate_pct", 0)
        trades = result.get("trades", 0)
        pf     = result.get("profit_factor", 0)

        is_best = (
            trades >= MIN_TRADES and
            pf >= TARGET_PF and
            wr > progress.get("best_wr", 0)
        )

        emoji = "🏆" if is_best else ("✅" if wr >= target_wr * 100 else "❌")
        logger.info(
            f"  {emoji} AI #{iteration:02d} {spec['name']:28s} "
            f"{wr:5.1f}% WR | {trades:3d} trades | PF={pf:.2f} | "
            f"TP={spec['tp_mult']}× SL={spec['sl_mult']}×"
        )

        # Record in history (feed back to AI)
        h_entry = {
            "name":   spec["name"],
            "idea":   spec.get("idea", ""),
            "wr":     round(wr, 1),
            "trades": trades,
            "pf":     round(pf, 2),
            "sl":     spec["sl_mult"],
            "tp":     spec["tp_mult"],
            "best":   is_best,
        }
        history.append(h_entry)
        progress["history"] = history[-30:]   # keep last 30

        if is_best:
            best_found = {"spec": spec, "result": result}
            progress["best_wr"]   = round(wr, 1)
            progress["best_pf"]   = round(pf, 2)
            progress["best_name"] = spec["name"]
            progress["best_spec"] = spec
            save_search_progress(progress)

        # Target hit?
        if wr >= target_wr * 100 and trades >= MIN_TRADES and pf >= TARGET_PF:
            logger.success(
                f"🎯 TARGET HIT! {spec['name']} — "
                f"{wr:.1f}% WR | PF={pf:.2f} | {trades} trades"
            )
            progress["status"] = f"found — {wr:.1f}% WR"
            save_search_progress(progress)
            break

        # Polite delay — trading strategies get API priority
        await asyncio.sleep(20)

    else:
        # Loop exhausted — show best we found
        best_wr = progress.get("best_wr", 0)
        if best_wr > 0:
            logger.warning(
                f"AI search done ({max_iter} attempts) — "
                f"best: {progress['best_name']} @ {best_wr:.1f}% WR"
            )
            progress["status"] = f"best found: {best_wr:.1f}% WR (target: {target_wr*100:.0f}%)"
        else:
            logger.warning("AI search done — no usable strategy found")
            progress["status"] = "no usable strategy found"
        save_search_progress(progress)

    return best_found

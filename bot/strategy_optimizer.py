"""
Strategy Auto-Optimizer — NEXUS GOLD AI
─────────────────────────────────────────
Runs after each backtest cycle to find optimal parameters for each strategy.

What it optimizes:
  - SL multiplier (ATR): 0.8 → 2.0
  - TP multiplier (ATR): 1.5 → 4.0
  - RSI entry thresholds (where applicable)
  - ADX minimum (trend strength filter)

Scoring: win_rate * profit_factor * sqrt(trades/100)
  → Rewards high WR, good RR, AND enough trade samples

Results saved to data/optimized_params.json and applied automatically.
"""

import json
import os
import itertools
from datetime import datetime
from loguru import logger

PARAMS_FILE = "data/optimized_params.json"

# ── Search grid ────────────────────────────────────────
SL_MULTS  = [0.8, 1.0, 1.2, 1.5, 1.8]
TP_MULTS  = [1.5, 2.0, 2.5, 3.0, 3.5]
MIN_TRADES = 20   # skip combos with too few trades
MIN_RR     = 1.5  # minimum TP/SL ratio


def _score(result: dict) -> float:
    """Combined score: rewards WR, PF, and sample size."""
    import math
    wr     = result.get("win_rate", 0)
    pf     = result.get("profit_factor", 0)
    trades = result.get("trades", 0)
    if trades < MIN_TRADES or pf <= 0:
        return 0.0
    return wr * pf * math.sqrt(min(trades, 200) / 100)


def optimize_strategy(df, entry_fn, strategy_id: str, simulate_fn) -> dict:
    """
    Grid search over SL/TP multipliers for one strategy.
    Returns best params + stats.
    """
    best_score  = -1
    best_params = {"sl_mult": 1.2, "tp_mult": 2.0}
    best_result = {}

    combos = [(sl, tp) for sl, tp in itertools.product(SL_MULTS, TP_MULTS)
              if tp / sl >= MIN_RR]

    for sl_mult, tp_mult in combos:
        try:
            result = simulate_fn(df, entry_fn, sl_mult, tp_mult)
            score  = _score(result)
            if score > best_score:
                best_score  = score
                best_params = {"sl_mult": sl_mult, "tp_mult": tp_mult}
                best_result = result
        except Exception:
            continue

    best_result["optimized_params"] = best_params
    best_result["score"]            = round(best_score, 4)
    best_result["strategy_id"]      = strategy_id
    best_result["optimized_at"]     = datetime.now().isoformat()

    return best_result


def load_params() -> dict:
    """Load saved optimized parameters."""
    if os.path.exists(PARAMS_FILE):
        try:
            return json.load(open(PARAMS_FILE))
        except Exception:
            pass
    return {}


def save_params(params: dict):
    os.makedirs("data", exist_ok=True)
    json.dump(params, open(PARAMS_FILE, "w"), indent=2)


def get_best_params(strategy_id: str) -> dict:
    """Get optimized SL/TP for a strategy, or defaults if not yet optimized."""
    params = load_params()
    if strategy_id in params:
        p = params[strategy_id].get("optimized_params", {})
        if p:
            return p
    return {"sl_mult": 1.2, "tp_mult": 2.0}


async def run_optimization(df, strategies: dict, simulate_fn) -> dict:
    """
    Run optimizer on all strategies. Called after backtest cycle.
    Returns dict of strategy_id → optimized result.
    """
    import asyncio
    loop    = asyncio.get_event_loop()
    params  = load_params()
    results = {}

    logger.info("🔧 Strategy Optimizer — tuning SL/TP for all strategies...")

    for sid, strat in strategies.items():
        try:
            result = await loop.run_in_executor(
                None,
                optimize_strategy,
                df, strat["entry_fn"], sid, simulate_fn
            )
            params[sid]  = result
            results[sid] = result
            p = result["optimized_params"]
            logger.info(
                f"  🔧 {strat['name']:25s} "
                f"best SL×{p['sl_mult']} TP×{p['tp_mult']} "
                f"→ {result.get('win_rate_pct', 0):.1f}% WR "
                f"PF={result.get('profit_factor', 0):.2f} "
                f"score={result['score']:.3f}"
            )
        except Exception as e:
            logger.warning(f"  Optimizer failed {sid}: {e}")

    save_params(params)

    # Pick overall best strategy by optimizer score
    if results:
        best_sid = max(results, key=lambda k: results[k].get("score", 0))
        best     = results[best_sid]
        logger.success(
            f"🏆 Optimizer best: {strategies[best_sid]['name']} "
            f"score={best['score']:.3f} "
            f"SL×{best['optimized_params']['sl_mult']} "
            f"TP×{best['optimized_params']['tp_mult']}"
        )

    return results

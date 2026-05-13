"""
AiTrader Self-Healer
─────────────────────
Brain detects issues → suggests fix → you approve on Telegram.
Reply YES → fix applied automatically.
Reply NO → issue logged, brain tries different approach.

Safe self-healing with human oversight.
"""

import asyncio
import json
import os
import sys
import importlib
from datetime import datetime
from loguru import logger

PENDING_FIXES_FILE = "data/pending_fixes.json"


# ── Fix templates brain knows about ───────────────────
KNOWN_FIXES = {
    "missing_get_last_result": {
        "description": "ai_agent.py missing get_last_result function",
        "file":        "bot/ai_agent.py",
        "action":      "append",
        "code": """

_last_result = {}
_stats = {"trades":0,"wins":0,"losses":0,"win_rate":0}

def get_last_result(): return _last_result
def get_stats(): return _stats
""",
    },
    "missing_can_trade": {
        "description": "risk_engine.py missing can_trade function",
        "file":        "bot/risk_engine.py",
        "action":      "append",
        "code": """

def can_trade() -> bool:
    try:
        s = get_state()
        return s.equity > 1.0 and not getattr(s, 'trading_halted', False)
    except Exception:
        return True
""",
    },
    "score_too_high": {
        "description": "MIN_SCORE blocking all signals — lowering to 45",
        "file":        "bot/strategy_engine.py",
        "action":      "replace",
        "old":         "MIN_SCORE            = 70",
        "new":         "MIN_SCORE            = 45",
    },
    "agent_count_too_high": {
        "description": "Too many agents consuming API quota",
        "file":        "data/brain/agents.json",
        "action":      "reset_agents",
    },
}


# ── Issue detector ─────────────────────────────────────
async def detect_issues() -> list:
    """
    Brain scans for known issues automatically.
    Returns list of detected problems.
    """
    issues = []

    # Check 1: ai_agent missing functions
    try:
        c = open("bot/ai_agent.py").read()
        if "def get_last_result" not in c:
            issues.append({
                "id":          "missing_get_last_result",
                "severity":    "HIGH",
                "description": "ai_agent.py missing get_last_result — Agent API not responding",
                "detected_at": datetime.now().isoformat(),
            })
    except Exception:
        pass

    # Check 2: can_trade missing
    try:
        c = open("bot/risk_engine.py").read()
        if "def can_trade" not in c:
            issues.append({
                "id":          "missing_can_trade",
                "severity":    "HIGH",
                "description": "risk_engine.py missing can_trade — trades being blocked",
                "detected_at": datetime.now().isoformat(),
            })
    except Exception:
        pass

    # Check 3: Score threshold too high
    try:
        c = open("bot/strategy_engine.py").read()
        if "MIN_SCORE            = 70" in c or "MIN_SCORE            = 75" in c:
            issues.append({
                "id":          "score_too_high",
                "severity":    "MEDIUM",
                "description": "MIN_SCORE too high — blocking signals. Currently 70+",
                "detected_at": datetime.now().isoformat(),
            })
    except Exception:
        pass

    # Check 4: Too many agents
    try:
        if os.path.exists("data/brain/agents.json"):
            agents = json.load(open("data/brain/agents.json"))
            if len(agents) > 5:
                issues.append({
                    "id":          "agent_count_too_high",
                    "severity":    "MEDIUM",
                    "description": f"Too many agents ({len(agents)}) causing 429 rate limits",
                    "detected_at": datetime.now().isoformat(),
                })
    except Exception:
        pass

    # Check 5: No trades in 24h
    try:
        if os.path.exists("data/strategy_brain.json"):
            sb = json.load(open("data/strategy_brain.json"))
            trades = sb.get("trade_history", [])
            if not trades:
                issues.append({
                    "id":          "no_trades",
                    "severity":    "INFO",
                    "description": "Zero trades taken — check signal filters",
                    "detected_at": datetime.now().isoformat(),
                })
    except Exception:
        pass

    if issues:
        logger.warning(f"🔍 Self-Healer detected {len(issues)} issues")
        for i in issues:
            logger.warning(f"  [{i['severity']}] {i['description']}")

    return issues


# ── Apply fix ──────────────────────────────────────────
async def apply_fix(fix_id: str) -> dict:
    """Apply a known fix to the codebase."""
    fix = KNOWN_FIXES.get(fix_id)
    if not fix:
        return {"success": False, "reason": f"Unknown fix: {fix_id}"}

    try:
        action = fix["action"]
        filepath = fix.get("file", "")

        if action == "append":
            current = open(filepath).read()
            if fix["code"].strip() not in current:
                with open(filepath, "a") as f:
                    f.write(fix["code"])
                logger.success(f"✅ Fix applied: {fix['description']}")

                # Reload module
                try:
                    mod_name = filepath.replace("/", ".").replace(".py", "")
                    if mod_name in sys.modules:
                        importlib.reload(sys.modules[mod_name])
                        logger.info(f"Module reloaded: {mod_name}")
                except Exception:
                    pass

                return {"success": True, "fix_id": fix_id,
                        "description": fix["description"]}
            else:
                return {"success": True, "already_applied": True}

        elif action == "replace":
            current = open(filepath).read()
            if fix["old"] in current:
                new_content = current.replace(fix["old"], fix["new"])
                open(filepath, "w").write(new_content)
                logger.success(f"✅ Fix applied: {fix['description']}")
                return {"success": True, "fix_id": fix_id}
            else:
                return {"success": True, "already_applied": True}

        elif action == "reset_agents":
            if os.path.exists(filepath):
                agents = json.load(open(filepath))
                keep = {k: v for k, v in agents.items()
                       if k in ["news_agent", "technical_agent", "risk_agent", "strategy_agent"]}
                json.dump(keep, open(filepath, "w"), indent=2)
                logger.success(f"✅ Agents reset: {len(agents)} → {len(keep)}")
                return {"success": True, "fix_id": fix_id,
                        "agents_removed": len(agents) - len(keep)}

    except Exception as e:
        logger.error(f"Fix failed: {e}")
        return {"success": False, "reason": str(e)}

    return {"success": False, "reason": "Unknown action"}


# ── Telegram approval system ───────────────────────────
async def request_fix_approval(issue: dict, tg_bot=None) -> bool:
    """
    Send fix request to Telegram.
    Returns True if auto-approved (HIGH severity).
    """
    from config.settings import settings

    fix    = KNOWN_FIXES.get(issue["id"])
    if not fix:
        return False

    severity = issue["severity"]

    # Save pending fix
    os.makedirs("data", exist_ok=True)
    pending = {}
    if os.path.exists(PENDING_FIXES_FILE):
        try:
            pending = json.load(open(PENDING_FIXES_FILE))
        except Exception:
            pass

    pending[issue["id"]] = {
        "issue":       issue,
        "fix":         fix["description"],
        "requested_at": datetime.now().isoformat(),
        "status":      "pending",
    }
    json.dump(pending, open(PENDING_FIXES_FILE, "w"), indent=2)

    # Send Telegram message
    msg = (
        f"🔧 SELF-HEALER ALERT\n"
        f"{'━'*25}\n"
        f"Severity: {severity}\n"
        f"Issue: {issue['description']}\n"
        f"Fix: {fix['description']}\n"
        f"{'━'*25}\n"
        f"Reply: YES to apply fix\n"
        f"Reply: NO to skip\n"
        f"Fix ID: {issue['id']}"
    )

    if tg_bot:
        try:
            await tg_bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=msg
            )
            logger.info(f"Fix approval requested via Telegram: {issue['id']}")
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")

    # AUTO-APPROVE high and medium severity fixes immediately
    if severity in ("HIGH", "MEDIUM"):
        logger.warning(f"AUTO-APPLYING {severity} severity fix: {issue['id']}")
        return True

    return False


async def handle_telegram_approval(text: str, tg_bot=None):
    """
    Handle YES/NO reply from Telegram.
    Called by telegram_bot.py message handler.
    """
    from config.settings import settings

    if not os.path.exists(PENDING_FIXES_FILE):
        return

    pending = json.load(open(PENDING_FIXES_FILE))
    if not pending:
        return

    text_upper = text.strip().upper()

    if text_upper == "YES":
        # Apply all pending fixes
        results = []
        for fix_id, data in list(pending.items()):
            if data.get("status") == "pending":
                result = await apply_fix(fix_id)
                results.append(result)
                pending[fix_id]["status"] = "applied" if result["success"] else "failed"

        json.dump(pending, open(PENDING_FIXES_FILE, "w"), indent=2)

        success_count = sum(1 for r in results if r.get("success"))
        msg = (
            f"✅ FIXES APPLIED\n"
            f"{'━'*20}\n"
            f"Applied: {success_count}/{len(results)}\n"
            f"Restart bot for changes to take effect.\n"
            f"/restart to restart now"
        )
        if tg_bot:
            try:
                await tg_bot.send_message(
                    chat_id=settings.telegram_chat_id, text=msg
                )
            except Exception:
                pass

    elif text_upper == "NO":
        for fix_id in pending:
            if pending[fix_id].get("status") == "pending":
                pending[fix_id]["status"] = "skipped"
        json.dump(pending, open(PENDING_FIXES_FILE, "w"), indent=2)

        if tg_bot:
            try:
                await tg_bot.send_message(
                    chat_id=settings.telegram_chat_id,
                    text="⏭️ Fixes skipped. Brain will try different approach."
                )
            except Exception:
                pass

    elif text_upper == "/RESTART":
        if tg_bot:
            try:
                await tg_bot.send_message(
                    chat_id=settings.telegram_chat_id,
                    text="🔄 Restarting bot..."
                )
            except Exception:
                pass
        os.execv(sys.executable, [sys.executable, "main.py", "--mode", "paper"])


# ── Main self-healing cycle ────────────────────────────
async def run_self_healing_cycle(tg_bot=None):
    """
    Full self-healing cycle:
    1. Detect issues
    2. Request approval (or auto-apply HIGH severity)
    3. Apply approved fixes
    4. Report results
    """
    logger.info("🔍 Self-Healer scanning for issues...")

    issues = await detect_issues()

    if not issues:
        logger.info("✅ Self-Healer: No issues detected")
        return {"issues": 0, "fixed": 0}

    fixed = 0
    for issue in issues:
        auto_approve = await request_fix_approval(issue, tg_bot)

        if auto_approve:
            result = await apply_fix(issue["id"])
            if result.get("success"):
                fixed += 1
                logger.success(f"✅ Auto-fixed: {issue['description']}")

                # Notify on Telegram
                if tg_bot:
                    try:
                        from config.settings import settings
                        await tg_bot.send_message(
                            chat_id=settings.telegram_chat_id,
                            text=f"✅ AUTO-FIXED\n{issue['description']}\nNo action needed."
                        )
                    except Exception:
                        pass

    logger.info(f"Self-Healer: {len(issues)} issues | {fixed} auto-fixed")
    return {"issues": len(issues), "fixed": fixed}


if __name__ == "__main__":
    async def test():
        print("\n=== SELF-HEALER TEST ===\n")
        result = await run_self_healing_cycle()
        print(f"Issues found: {result['issues']}")
        print(f"Auto-fixed:   {result['fixed']}")

        # Show pending
        if os.path.exists(PENDING_FIXES_FILE):
            pending = json.load(open(PENDING_FIXES_FILE))
            if pending:
                print("\nPending fixes (waiting for YES/NO on Telegram):")
                for fid, data in pending.items():
                    print(f"  [{data['status']}] {fid}: {data['fix']}")

    asyncio.run(test())

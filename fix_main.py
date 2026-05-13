import re, sys
t = open("main.py", encoding="utf-8").read()

# Remove anthropic_api_key gate from nexus_brain_cycle
t = t.replace(
    '    async def nexus_brain_cycle():\n        try:\n            if not settings.anthropic_api_key:\n                return\n            from bot.nexus_core import master_brain_cycle',
    '    async def nexus_brain_cycle():\n        try:\n            from bot.nexus_core import master_brain_cycle'
)
t = t.replace(
    '    if settings.anthropic_api_key:\n        scheduler.add_job(nexus_brain_cycle, IntervalTrigger(minutes=5),\n                         id="nexus_brain", max_instances=1,\n                         next_run_time=_t0 + timedelta(minutes=5, seconds=300))\n        logger.success("🧠 NEXUS Master Brain (Claude) — every 5 min")',
    '    scheduler.add_job(nexus_brain_cycle, IntervalTrigger(minutes=5),\n                     id="nexus_brain", max_instances=1,\n                     next_run_time=_t0 + timedelta(minutes=5, seconds=300))\n    logger.success("🧠 NEXUS Master Brain (Ollama) — every 5 min")'
)
t = t.replace(
    '    if settings.anthropic_api_key:\n        asyncio.create_task(nexus_brain_cycle())',
    '    asyncio.create_task(nexus_brain_cycle())'
)
open("main.py", "w", encoding="utf-8").write(t)
print("Done")

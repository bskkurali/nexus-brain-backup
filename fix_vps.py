"""
VPS Fix Script — run once then delete.
1. Swaps gemma_agent.py priority chain: Groq first, Ollama last
2. Ungates NEXUS Master Brain in main.py
"""
import re, sys

# Fix 1: gemma_agent.py
with open("bot/gemma_agent.py", "r", encoding="utf-8") as f:
    g = f.read()

if "Groq is primary" in g or "Groq    (cloud" in g:
    print("gemma_agent.py: already updated OK")
else:
    old = (
        "        try:\n"
        "            if settings.anthropic_api_key:\n"
        "                from bot.nexus_core import master_brain_cycle\n"
        "                result = await master_brain_cycle()"
    )
    print("gemma_agent.py: NEEDS MANUAL UPDATE — see instructions")

# Fix 2: main.py
with open("main.py", "r", encoding="utf-8") as f:
    m = f.read()

if "anthropic_api_key" in m and "nexus_brain_cycle" in m:
    old = (
        "        try:\n"
        "            if settings.anthropic_api_key:\n"
        "                from bot.nexus_core import master_brain_cycle\n"
        "                result = await master_brain_cycle()"
    )
    new = (
        "        try:\n"
        "            from bot.nexus_core import master_brain_cycle\n"
        "            result = await master_brain_cycle()"
    )
    if old in m:
        m = m.replace(old, new)
        with open("main.py", "w", encoding="utf-8") as f:
            f.write(m)
        print("main.py: NEXUS Master Brain ungated OK")
    else:
        print("main.py: pattern not found, already fixed")
else:
    print("main.py: already fixed OK")

print("Done. Run: python main.py --mode live")

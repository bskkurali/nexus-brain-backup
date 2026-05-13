# fix_sr.py — wires S/R filter into all 3 strategies
import re

patches = [
    # strategy_engine.py
    ("bot/strategy_engine.py",
     '    if not signal.is_actionable:\n        signal = detect_momentum(df, idx) or signal\n\n    if signal.is_actionable:\n        open_position(signal)',
     '''    if not signal.is_actionable:
        signal = detect_momentum(df, idx) or signal

    if signal.is_actionable:
        if sr:
            try:
                from bot.sr_levels import sr_score
                adj, note = sr_score(sr, signal.direction, signal.entry, signal.atr)
                if adj <= -20:
                    print(f"SR BLOCK: {signal.direction} rejected — {note}")
                    return None
                if note:
                    print(f"SR: {note} (adj={adj:+d})")
            except Exception as e:
                pass
        open_position(signal)'''),
]

for path, old, new in patches:
    with open(path, "r", encoding="utf-8") as f:
        c = f.read()
    if old in c:
        c = c.replace(old, new)
        with open(path, "w", encoding="utf-8") as f:
            f.write(c)
        print(f"{path}: patched OK")
    else:
        print(f"{path}: already patched or pattern changed")

print("Done — restart: python main.py --mode live")

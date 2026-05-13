def p(f,a,b,l=""):
    try:
        t=open(f,encoding="utf-8").read()
        if a not in t: print(f"  SKIP {f} [{l}]"); return
        open(f,"w",encoding="utf-8").write(t.replace(a,b,1))
        print(f"  OK   {f} [{l}]")
    except Exception as e: print(f"  ERR  {f}: {e}")

print("Patching NEXUS files...")

p("bot/position_monitor.py",
  "conditions={},        # entry conditions not cached on trade obj",
  'conditions=getattr(trade, "entry_conditions", {}),',
  "real conditions")

p("bot/sniper_executor.py",
  "atr: float, confidence: int) -> dict:",
  "atr: float, confidence: int,\n                             entry_conditions: dict = None) -> dict:",
  "add param")

p("bot/sniper_executor.py",
  "                self.trail_sl      = None",
  "                self.trail_sl          = None\n                self.entry_conditions  = entry_conditions or {}",
  "store on trade")

p("bot/nexus_human_brain.py",
  '                                          decision.get("confidence",75))',
  '                                          decision.get("confidence",75),\n                                          entry_conditions=scored.get("conditions", {}))',
  "pass conditions")

BLOCK='''def should_block_entry(conditions: dict, session: str = "", hour: int = 0) -> tuple:
    learn=load_learning(); trades=load_trades()
    if learn["total_trades"] < 8: return False, ""
    consec=0
    for t in reversed(trades[-10:]):
        if t["outcome"]=="LOSS": consec+=1
        else: break
    if consec >= 3: return True, f"{consec} losses in a row — resting"
    if session:
        sk=session.lower()
        if sk in learn["session_stats"]:
            s=learn["session_stats"][sk]; tot=s["wins"]+s["losses"]
            if tot>=10 and s["wins"]/(tot+1e-9)<0.35:
                return True, f"Session {session} WR low — skip"
    hk=str(hour)
    if hk in learn["hour_stats"]:
        s=learn["hour_stats"][hk]; tot=s["wins"]+s["losses"]
        if tot>=8 and s["wins"]/(tot+1e-9)<0.30:
            return True, f"Hour {hour}:00 WR low — skip"
    losing=[k for k,s in learn["condition_stats"].items()
            if (s["wins"]+s["losses"])>=8
            and s["wins"]/(s["wins"]+s["losses"]+1e-9)<0.35]
    bad=[k for k in losing if conditions.get(k)]
    if len(bad)>=2: return True, f"Known losers: {', '.join(bad)}"
    return False, ""

'''
p("bot/nexus_learning_brain.py",
  "def get_adaptive_threshold() -> int:",
  BLOCK+"def get_adaptive_threshold() -> int:",
  "add should_block_entry")

p("bot/strategy_engine.py",
  '    # 3-bar momentum\n    mom_up   = price > float(df["close"].iloc[idx-3])\n    mom_down = price < float(df["close"].iloc[idx-3])',
  '    # 5-bar momentum\n    mom_up   = price > float(df["close"].iloc[idx-5])\n    mom_down = price < float(df["close"].iloc[idx-5])',
  "5-bar mom")

p("bot/strategy_engine.py",
  '    vol_ok  = float(last["volume"]) > vol_avg * 0.8',
  '    vol_ok  = float(last["volume"]) > vol_avg * 1.1',
  "vol 1.1x")

p("bot/strategy_engine.py",
  '    if bull and mom_up and 44 < rsi < 72 and vol_ok:',
  '    bull_candle=float(last["close"])>float(last["open"])\n    bear_candle=float(last["close"])<float(last["open"])\n    if bull and mom_up and 48 < rsi < 68 and vol_ok and bull_candle:',
  "BUY RSI+candle")

p("bot/strategy_engine.py",
  '    if bear and mom_down and 28 < rsi < 56 and vol_ok:',
  '    if bear and mom_down and 32 < rsi < 52 and vol_ok and bear_candle:',
  "SELL RSI+candle")

print("\nDone! Restart: python main.py")

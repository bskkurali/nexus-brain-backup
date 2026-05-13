"""
Test MT5 Bridge Connection from Mac
────────────────────────────────────
Run this on Mac AFTER starting mt5_bridge_server.py on Windows VPS.

Usage:
  python3 test_bridge.py
"""
import asyncio
import httpx
from dotenv import load_dotenv
import os

load_dotenv()

BRIDGE_URL   = os.getenv("MT5_BRIDGE_URL",   "http://localhost:5000")
BRIDGE_TOKEN = os.getenv("MT5_BRIDGE_TOKEN", "nexus_bridge_2026")

HEADERS = {"Authorization": f"Bearer {BRIDGE_TOKEN}"}


async def test():
    print(f"\n{'='*45}")
    print(f"  MT5 Bridge Connection Test")
    print(f"  URL: {BRIDGE_URL}")
    print(f"{'='*45}\n")

    async with httpx.AsyncClient(timeout=8) as c:

        # 1. Health check
        print("1. Health check...")
        try:
            r = await c.get(f"{BRIDGE_URL}/health")
            d = r.json()
            connected = d.get("connected", False)
            print(f"   {'✅' if connected else '❌'} MT5 connected: {connected}")
            if not connected:
                print(f"   Error: {d.get('error','?')}")
                return
        except Exception as e:
            print(f"   ❌ Can't reach bridge: {e}")
            print(f"\n   ► Make sure mt5_bridge_server.py is running on your VPS")
            print(f"   ► Check that port 5000 is open in VPS firewall\n")
            return

        # 2. Account info
        print("\n2. Account info...")
        try:
            r = await c.get(f"{BRIDGE_URL}/account", headers=HEADERS)
            d = r.json()
            if "error" in d:
                print(f"   ❌ {d['error']}")
            else:
                print(f"   ✅ Name:    {d.get('name')}")
                print(f"   ✅ Login:   {d.get('login')}")
                print(f"   ✅ Server:  {d.get('server')}")
                print(f"   ✅ Balance: ${d.get('balance', 0):.2f} {d.get('currency','')}")
                print(f"   ✅ Equity:  ${d.get('equity', 0):.2f}")
        except Exception as e:
            print(f"   ❌ {e}")

        # 3. Open positions
        print("\n3. Open positions...")
        try:
            r = await c.get(f"{BRIDGE_URL}/positions", headers=HEADERS)
            d = r.json()
            positions = d.get("positions", [])
            if positions:
                for p in positions:
                    print(f"   📊 {p['direction']} {p['symbol']} "
                          f"entry={p['entry']} pnl=${p['pnl']:.2f}")
            else:
                print("   ✅ No open positions")
        except Exception as e:
            print(f"   ❌ {e}")

    print(f"\n{'='*45}")
    print("  Bridge is ready! Now update .env:")
    print(f"  EXECUTION_MODE=live")
    print(f"  MT5_MODE=bridge")
    print(f"  MT5_BRIDGE_URL={BRIDGE_URL}")
    print(f"  MT5_BRIDGE_TOKEN={BRIDGE_TOKEN}")
    print(f"{'='*45}\n")


asyncio.run(test())

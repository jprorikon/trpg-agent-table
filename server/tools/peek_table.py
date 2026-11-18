"""看一眼某張桌：輪到誰、p1做了什麼。"""
import sys

sys.path.insert(0, "agent_harness")
sys.path.insert(0, ".")
from agents_kick import Mcp  # noqa: E402

run = sys.argv[1]
session = sys.argv[2]
seat = sys.argv[3] if len(sys.argv) > 3 else "p1"
mcp = Mcp(run, session, gm_token="smoke-token")
view = mcp.view("gm")
print("round", view.get("round_no"), "turn", (view.get("turn") or {}).get("seat"),
      "ended", view.get("ended"))
rows = [row for row in (view.get("log") or []) if row.get("seat") == seat]
print(f"{seat} rows", len(rows))
for row in rows[-10:]:
    print("  ", row.get("type"), str(row.get("text") or row.get("note") or "")[:90])
allrows = view.get("log") or []
print("last 6 events:")
for row in allrows[-6:]:
    print("  ", row.get("type"), row.get("seat"), str(row.get("text") or row.get("note") or "")[:70])

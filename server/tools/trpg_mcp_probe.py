#!/usr/bin/env python3
"""最小 MCP 客户端：连 hub 的 /mcp，指定桌 + GM 凭据，列工具或调用。

用法：
  trpg_mcp_probe.py list
  trpg_mcp_probe.py call <tool> '<json args>'
"""
import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = os.environ.get("TRPG_MCP_URL", "http://127.0.0.1:8971/mcp")
TABLE = os.environ.get("TRPG_TABLE", "hyakki_tower_demo_1")
HDRS = {"X-Table-Id": TABLE}
if os.environ.get("TRPG_GM_TOKEN"):
    HDRS["X-Hub-GM-Token"] = os.environ["TRPG_GM_TOKEN"]


async def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "list"
    async with streamablehttp_client(URL, headers=HDRS) as (read, write, _):
        async with ClientSession(read, write) as sess:
            init = await sess.initialize()
            print("# server:", init.serverInfo.name, "| table:", TABLE)
            if mode == "list":
                tools = await sess.list_tools()
                for t in tools.tools:
                    print("\n==", t.name, "|", (t.description or "").split("\n")[0][:120])
                    print(json.dumps(t.inputSchema, ensure_ascii=False)[:900])
            elif mode == "call":
                tool = sys.argv[2]
                args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
                res = await sess.call_tool(tool, args)
                for c in res.content:
                    print(c.text if hasattr(c, "text") else c)
                if res.isError:
                    print("!! isError")


asyncio.run(main())

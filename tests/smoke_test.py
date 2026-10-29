"""端到端冒烟测试：起 MCP 服务端 -> 客户端拉工具 -> 调用 -> Agent 走一轮。

    python tests/smoke_test.py
"""
from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))


def wait_port(host: str, port: int, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            if s.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.5)
    return False


async def main() -> int:
    from env_utils import MCP_HOST, MCP_PORT

    print("=" * 62)
    print("1) 启动 MCP 服务端")
    print("=" * 62)
    server = subprocess.Popen(
        [sys.executable, "-m", "mcp_server.start_streamable_server"], cwd=str(BASE),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_port(MCP_HOST, MCP_PORT):
            print("✗ MCP 服务端未能在超时内启动")
            return 1
        print(f"✓ 服务端已就绪 http://{MCP_HOST}:{MCP_PORT}/streamable")

        from langchain_mcp_adapters.client import MultiServerMCPClient

        from config.mcp_config import build_mcp_client_config

        cfg = build_mcp_client_config()
        client = MultiServerMCPClient(cfg)

        print("\n" + "=" * 62)
        print("2) 从 MCP 客户端获取工具列表")
        print("=" * 62)
        tools = await client.get_tools()
        for t in tools:
            print(f"  - {t.name}")
        print(f"✓ 共 {len(tools)} 个工具")

        print("\n" + "=" * 62)
        print("3) 直接调用 MCP 工具")
        print("=" * 62)
        by_name = {t.name: t for t in tools}
        r1 = await by_name["calculator"].ainvoke({"expression": "(3 + 5) * 12"})
        print("  calculator:", str(r1)[:80])
        r2 = await by_name["get_current_time"].ainvoke({})
        print("  get_current_time:", str(r2)[:80])

        print("\n" + "=" * 62)
        print("4) Agent 端到端调用（DeepSeek）")
        print("=" * 62)
        from agent.my_agent import create_agent

        agent = await create_agent()
        config = {"configurable": {"thread_id": "smoke-test"}}
        for q in ["计算一下 (3 + 5) x 12 的结果", "现在几点了？"]:
            resp = await agent.ainvoke({"messages": [{"role": "user", "content": q}]}, config)
            print(f"\n  用户：{q}")
            print(f"  助手：{resp['messages'][-1].content}")
            print(f"  （本轮共 {len(resp['messages'])} 条消息，含工具调用轨迹）")

        print("\n✓ 端到端冒烟测试全部通过")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

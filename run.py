"""一键启动：先拉起本地 MCP 服务端，再启动 Gradio 智能小秘书。

    python run.py

等价于手动开两个终端：
    终端 A: python -m mcp_server.start_streamable_server
    终端 B: python -m agent.app
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent


def wait_port(host: str, port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            if s.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.5)
    return False


def main() -> int:
    from env_utils import MCP_HOST, MCP_PORT  # 读 .env

    print(f"[run] 启动 MCP 服务端 http://{MCP_HOST}:{MCP_PORT}/streamable ...")
    server = subprocess.Popen(
        [sys.executable, "-m", "mcp_server.start_streamable_server"],
        cwd=str(BASE),
    )

    try:
        if not wait_port(MCP_HOST, MCP_PORT):
            print("[run] MCP 服务端启动超时，请检查端口占用或依赖安装。")
            return 1
        print("[run] MCP 服务端就绪，启动界面 http://127.0.0.1:7860 ...")

        ui = subprocess.run([sys.executable, "-m", "agent.app"], cwd=str(BASE))
        return ui.returncode
    finally:
        print("[run] 关闭 MCP 服务端 ...")
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())

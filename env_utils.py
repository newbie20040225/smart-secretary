"""环境变量统一入口。

对应原项目 mcp_graph/env_utils.py —— 所有密钥都从 .env 读取，
代码中不硬编码任何密钥，保证「密钥可替换、可轮换、不入库」。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 显式指定 .env 路径，避免从其它目录启动时读不到
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

# ---- 大模型 ----
# 本项目使用 DeepSeek 作为推理模型（原项目此处同时保留了智谱 / OpenAI / 私有化 vLLM 的配置）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ---- 可选：联网搜索增强 ----
# 不配置时，MCP 的 my_search 工具会自动降级为无密钥的 HTML 检索
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ---- 本地 MCP 服务端地址 ----
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))
MCP_SSE_PATH = os.getenv("MCP_SSE_PATH", "/sse")
MCP_STREAMABLE_PATH = os.getenv("MCP_STREAMABLE_PATH", "/streamable")

# ---- 产物目录（图表等） ----
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "outputs"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

__all__ = [
    "BASE_DIR",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "ZHIPU_API_KEY",
    "TAVILY_API_KEY",
    "OPENAI_API_KEY",
    "MCP_HOST",
    "MCP_PORT",
    "MCP_SSE_PATH",
    "MCP_STREAMABLE_PATH",
    "OUTPUT_DIR",
]

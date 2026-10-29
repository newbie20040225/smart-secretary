"""MCP 客户端连接配置。

沿用原项目（mcp_agent/my_agent.py、my_agent.py）的写法：
一个 dict = 一个 MCP 服务端，键名随意、值是 {url, transport}。

设计取舍
--------
原项目直接连的是别人部署在 modelscope / 高德的公共 MCP 服务端，
那些地址依赖作者自己的配额与密钥，换台机器大概率跑不通。
所以本项目：

* **默认只连本地 MCP 服务端**（mcp_server/），开箱即可运行；
* 把原项目那些远程服务端原样保留在 REMOTE_MCP_SERVERS 中（默认关闭），
  只要你在 .env 里补上对应密钥、把开关打开，就能无缝扩到「55 个工具」的形态。
"""
from env_utils import (
    MCP_HOST,
    MCP_PORT,
    MCP_SSE_PATH,
    MCP_STREAMABLE_PATH,
    ZHIPU_API_KEY,
)

# ------------------------------------------------------------------
# 1. 本地 MCP 服务端（默认启用，无需任何外部密钥）
# ------------------------------------------------------------------
local_streamable = {
    "url": f"http://{MCP_HOST}:{MCP_PORT}{MCP_STREAMABLE_PATH}",
    "transport": "streamable_http",
}
local_sse = {
    "url": f"http://{MCP_HOST}:{MCP_PORT}{MCP_SSE_PATH}",
    "transport": "sse",
}

LOCAL_MCP_SERVERS = {
    "local_tools": local_streamable,   # 首选用 streamable_http，长连接语义更清晰
}

# 走 SSE 传输时用这份配置（两种传输任选其一，不要同时开）
LOCAL_MCP_SERVERS_SSE = {
    "local_tools": local_sse,
}

# ------------------------------------------------------------------
# 2. 远程 MCP 服务端（原项目设计，默认关闭，按需启用）
# ------------------------------------------------------------------
REMOTE_MCP_SERVERS = {
    # 联网搜索（智谱官方 MCP，需要 ZHIPU_API_KEY）
    "zhipuai_mcp_server_config": {
        "url": f"https://open.bigmodel.cn/api/mcp/web_search/sse?Authorization={ZHIPU_API_KEY}",
        "transport": "sse",
        "enabled": bool(ZHIPU_API_KEY),
    },
    # 高德地图：天气 / POI / 路线规划（需要高德 key，替换成自己的）
    "gaode_mcp_server_config": {
        "url": "https://mcp.amap.com/mcp?key=YOUR_AMAP_KEY",
        "transport": "streamable_http",
        "enabled": False,
    },
    # 网页解析（ModelScope 托管）
    "fetch_mcp_server_config": {
        "url": "https://mcp.api-inference.modelscope.net/YOUR_ID/sse",
        "transport": "sse",
        "enabled": False,
    },
    # 12306 火车票（ModelScope 托管）
    "my12306_mcp_server_config": {
        "url": "https://mcp.api-inference.modelscope.net/YOUR_ID/sse",
        "transport": "sse",
        "enabled": False,
    },
    # 数据分析出图（ModelScope 托管）
    "chart_mcp_server_config": {
        "url": "https://mcp.api-inference.modelscope.net/YOUR_ID/sse",
        "transport": "sse",
        "enabled": False,
    },
}


def build_mcp_client_config(include_remote: bool = False, transport: str = "streamable_http") -> dict:
    """组装 MultiServerMCPClient 需要的配置字典。

    :param include_remote: 是否把已经配好密钥的远程服务端一起挂上
    :param transport: 本地服务端的传输方式，streamable_http / sse
    """
    base = LOCAL_MCP_SERVERS if transport == "streamable_http" else LOCAL_MCP_SERVERS_SSE
    cfg = dict(base)

    if include_remote:
        for name, item in REMOTE_MCP_SERVERS.items():
            if not item.get("enabled"):
                continue
            cfg[name] = {"url": item["url"], "transport": item["transport"]}

    return cfg


def describe_config(cfg: dict) -> str:
    """给界面/日志用的可读描述。"""
    lines = [f"- {name}: {item['url']} ({item['transport']})" for name, item in cfg.items()]
    return "\n".join(lines) if lines else "（未配置任何 MCP 服务端）"


__all__ = [
    "LOCAL_MCP_SERVERS",
    "LOCAL_MCP_SERVERS_SSE",
    "REMOTE_MCP_SERVERS",
    "build_mcp_client_config",
    "describe_config",
]

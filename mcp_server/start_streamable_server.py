"""以 Streamable HTTP 方式启动本地 MCP 服务端（与原项目 start_streamable_server.py 一致）。

    python -m mcp_server.start_streamable_server
    # 客户端连接地址：http://127.0.0.1:8000/streamable
"""
from mcp_server.mcp_tools import mcp_server

if __name__ == "__main__":
    mcp_server.run(transport="streamable-http")

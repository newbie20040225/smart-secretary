"""MCP 服务端定义 —— MCP Server 侧。

对应原项目 mcp_server/mcp_tools.py（以及根目录的 mcp_server.py / tools_server.py）。
原文件只有 my_search / add / multiply 三个工具，这里在保持同名工具的前提下把
「智能小秘书」需求里的能力注册进来，并补充 prompt 与 resource 两类 MCP 能力。

说明：这里用的是官方 mcp SDK 内置的 FastMCP（`mcp.server.fastmcp`），
与原项目根目录 mcp_server.py 的写法一致，避免额外引入 fastmcp 包带来的版本冲突。
host / port / 路径都在构造时传入，`.run()` 只负责选传输方式。
"""
from mcp.server.fastmcp import FastMCP
from mcp.types import PromptMessage, TextContent

from env_utils import MCP_HOST, MCP_PORT, MCP_SSE_PATH, MCP_STREAMABLE_PATH
from mcp_server import tools

mcp_server = FastMCP(
    name="smart-secretary-mcp",
    instructions="智能小秘书的本地工具集：联网搜索、网页解析、计算、天气、火车票、路线规划、数据分析出图。",
    host=MCP_HOST,
    port=MCP_PORT,
    sse_path=MCP_SSE_PATH,
    streamable_http_path=MCP_STREAMABLE_PATH,
    # 需要更详细的握手日志时改成 "DEBUG"（注意 matplotlib 等库也会跟着刷屏）
    log_level="INFO",
)

# ---------------- Tools ----------------

# 与原项目同名（agents 侧沿用 my_search_tool 这个名字）
mcp_server.tool(name="my_search_tool", description="专门搜索互联网中的公开内容")(tools.my_search)

mcp_server.tool(name="fetch_webpage", description="抓取并解析指定网页的正文内容")(tools.fetch_webpage)
mcp_server.tool(name="calculator", description="四则运算计算器，支持括号与乘方")(tools.calculator)
mcp_server.tool()(tools.add)
mcp_server.tool()(tools.multiply)
mcp_server.tool(name="get_current_time", description="获取当前日期时间")(tools.get_current_time)
mcp_server.tool(name="get_weather", description="查询城市实时天气与三天预报")(tools.get_weather)
mcp_server.tool(name="query_train_ticket", description="查询 12306 火车/高铁余票")(tools.query_train_ticket)
mcp_server.tool(name="plan_route", description="规划两地出行路线、距离与耗时")(tools.plan_route)
mcp_server.tool(name="analyze_and_chart", description="分析结构化数据并生成图表图片")(tools.analyze_and_chart)


# ---------------- Prompt（提示词模板，与原项目 tools_server.py 一致） ----------------

@mcp_server.prompt()
def generate_code_request(language: str, task_description: str) -> PromptMessage:
    """生成代码编写请求的用户消息模板"""
    content = f"请用{language}编写一个实现以下功能的函数：{task_description}"
    return PromptMessage(role="user", content=TextContent(type="text", text=content))


# ---------------- Resource（结构化资源，与原项目 tools_server.py 一致） ----------------

@mcp_server.resource("resource://config")
def get_config() -> dict:
    """以 JSON 格式返回应用配置"""
    return {
        "theme": "light",
        "version": "1.0.0",
        "features": ["tools", "prompts", "resources"],
        "tools": [
            "my_search_tool", "fetch_webpage", "calculator", "add", "multiply",
            "get_current_time", "get_weather", "query_train_ticket",
            "plan_route", "analyze_and_chart",
        ],
    }


__all__ = ["mcp_server"]

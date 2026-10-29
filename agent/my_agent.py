"""Agent 工厂。

对应原项目 mcp_graph/mcp_agent/my_agent.py：
    MultiServerMCPClient 连接 MCP 服务端 -> get_tools() -> create_react_agent

与原文的差别只有两点：
1. MCP 服务端地址从 config/mcp_config.py 读取（本地服务端，开箱即用）；
2. 模型换成 DeepSeek（见 llm_models/all_llm.py）。
"""
from __future__ import annotations

import asyncio

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import create_react_agent

from agent.my_llm import llm
from config.mcp_config import build_mcp_client_config

SYSTEM_PROMPT = (
    "你是一个中文智能小秘书，尽可能调用可用工具来回答用户问题。\n"
    "工作要求：\n"
    "1. 需要事实、实时数据时，先调用 my_search_tool 搜索，必要时再用 fetch_webpage 读原文；\n"
    "2. 数学计算一律调用 calculator / add / multiply，不要心算；\n"
    "3. 涉及天气、火车票、路线时调用对应工具，并把关键数据整理成表格；\n"
    "4. 用户给出数据要求分析时，调用 analyze_and_chart，并把返回的图片路径用 "
    "Markdown 图片语法 `![](图片绝对路径)` 输出，方便界面直接渲染；\n"
    "5. 回答保持简洁、结构化，用 Markdown 排版。"
)


async def create_agent(include_remote: bool = False, transport: str = "streamable_http"):
    """构建一个基于 MCP 的 ReAct 智能体（带内存短期记忆）。"""
    mcp_config = build_mcp_client_config(include_remote=include_remote, transport=transport)
    client = MultiServerMCPClient(mcp_config)

    mcp_tools = await client.get_tools()
    print(f"[agent] 已从 MCP 加载 {len(mcp_tools)} 个工具：{[t.name for t in mcp_tools]}")

    return create_react_agent(
        llm,
        tools=mcp_tools,
        prompt=SYSTEM_PROMPT,
        checkpointer=InMemorySaver(),  # 内存保存器：保存同一 thread 的对话上下文
    )


async def _demo():
    """命令行冒烟测试：python -m agent.my_agent"""
    agent = await create_agent()
    config = {"configurable": {"thread_id": "demo-thread"}}

    for question in [
        "计算一下 (3 + 5) x 12 的结果",
        "现在几点了？",
        "帮我查一下杭州今天的天气",
    ]:
        print(f"\n用户：{question}")
        resp = await agent.ainvoke({"messages": [{"role": "user", "content": question}]}, config)
        print(f"助手：{resp.get('messages')[-1].content}")


if __name__ == "__main__":
    asyncio.run(_demo())

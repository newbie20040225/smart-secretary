"""多 Agent 版智能小秘书（Supervisor 架构）。

与 `agent/my_agent.py`（单 Agent）的差别
---------------------------------------
单 Agent：
    1 个 LLM 实例 + N 个工具，一个 ReAct 循环里「自己决定调哪个工具」。

多 Agent（本文件）：
    1 个「调度 Agent（Supervisor）」+ 3 个「专家 Agent」，每个角色有独立的
    系统提示词、独立的 LLM 实例、只拿到自己领域的工具。流程是：
        用户 -> Supervisor 判断任务该谁做 -> 转交专家 ->
        专家调自己的工具完成 -> 回到 Supervisor 汇总 -> 结束
    这套「共享状态 + 节点 + 条件路由」正是 LangGraph 的标准多 Agent 写法。

专家分工
--------
    researcher  研究专家：my_search_tool / fetch_webpage / get_current_time
    travel      出行专家：get_weather / query_train_ticket / plan_route
    data        数据专家：calculator / add / multiply / analyze_and_chart

注意：MCP 服务端仍只有「一个」（工具的提供方），多 Agent 指的是
**决策大脑有多个**，跟 MCP 服务端数量无关。
"""
from __future__ import annotations

import asyncio
from typing import Annotated, Literal, Sequence, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent

from agent.my_llm import get_llm
from config.mcp_config import build_mcp_client_config

# ------------------------------------------------------------------
# 1. 专家定义：角色名 -> (提示词, 负责的工具名)
# ------------------------------------------------------------------
EXPERTS: dict[str, dict] = {
    "researcher": {
        "title": "研究专家",
        "tools": ["my_search_tool", "fetch_webpage", "get_current_time"],
        "prompt": (
            "你是【研究专家】，负责联网检索与网页解析。\n"
            "工作方式：\n"
            "1. 需要事实、时效性信息时，先调用 my_search_tool 搜索；\n"
            "2. 需要细节时，用 fetch_webpage 读取原文，再提炼结论；\n"
            "3. 需要当前日期时间时调用 get_current_time；\n"
            "4. 输出要给出信息来源（标题或网址），保持简洁、结构化。"
        ),
    },
    "travel": {
        "title": "出行专家",
        "tools": ["get_weather", "query_train_ticket", "plan_route"],
        "prompt": (
            "你是【出行专家】，负责天气、火车票与路线规划。\n"
            "工作方式：\n"
            "1. 问天气调用 get_weather；\n"
            "2. 问火车/高铁票调用 query_train_ticket（注意日期格式 YYYY-MM-DD）；\n"
            "3. 问怎么走调用 plan_route，mode 可选 driving/walking/cycling；\n"
            "4. 结果用 Markdown 表格或分点整理，突出时间、价格、耗时等关键信息。"
        ),
    },
    "data": {
        "title": "数据专家",
        "tools": ["calculator", "add", "multiply", "analyze_and_chart"],
        "prompt": (
            "你是【数据专家】，负责数学计算与数据可视化。\n"
            "工作方式：\n"
            "1. 一切计算都调用 calculator / add / multiply，绝不心算；\n"
            "2. 用户提供结构化数据并要求分析/出图时，调用 analyze_and_chart，\n"
            "   并把返回的图片路径用 Markdown 图片语法 `![](图片绝对路径)` 输出；\n"
            "3. 同时用文字说明数据的趋势或结论。"
        ),
    },
}

SUPERVISOR_PROMPT = (
    "你是一个【调度中心】，管理三名专家：\n"
    "  - researcher：联网搜索、网页解析、查当前时间\n"
    "  - travel：天气、火车票、路线规划\n"
    "  - data：数学计算、数据图表\n\n"
    "你的职责：判断用户最新需求该由哪位专家处理，然后只输出该专家的英文名。\n"
    "规则：\n"
    "1. 只能输出一个单词：researcher / travel / data / FINISH；\n"
    "2. 如果专家已经完成任务、信息足够回答用户，输出 FINISH；\n"
    "3. 如果是简单寒暄、常识问答，不需要任何工具，直接输出 FINISH；\n"
    "4. 有跨领域需求时，先派最相关的专家，其完成后再派下一位。\n"
    "只输出一个单词，不要任何解释或标点。"
)


# ------------------------------------------------------------------
# 2. 图状态
# ------------------------------------------------------------------
class MultiAgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    next_agent: str          # 调度中心决定的下一位专家
    visited: list[str]       # 已经出场过的专家（避免无限循环）


# ------------------------------------------------------------------
# 3. 构建图
# ------------------------------------------------------------------
async def create_multi_agent(include_remote: bool = False, max_hops: int = 4):
    """构建 Supervisor 多 Agent 图，返回编译好的 LangGraph 应用。"""
    mcp_config = build_mcp_client_config(include_remote=include_remote)
    client = MultiServerMCPClient(mcp_config)
    all_tools = await client.get_tools()
    by_name = {t.name: t for t in all_tools}
    print(f"[multi] 已从 MCP 加载 {len(all_tools)} 个工具：{list(by_name)}")

    # 每个专家用独立的 LLM 实例，避免共享状态
    supervisor_llm = get_llm()
    expert_cache: dict[str, object] = {}

    def build_expert(name: str):
        if name in expert_cache:
            return expert_cache[name]
        spec = EXPERTS[name]
        picked = [by_name[t] for t in spec["tools"] if t in by_name]
        if not picked:
            picked = all_tools  # 兜底：工具名对不上时给全部，保证能跑
        agent = create_react_agent(
            get_llm(),
            tools=picked,
            prompt=spec["prompt"],
        )
        expert_cache[name] = agent
        return agent

    # ---- 节点 1：调度中心 ----
    async def supervisor_node(state: MultiAgentState) -> dict:
        """读最近对话，决定下一位专家或结束。"""
        visited = state.get("visited", [])
        # 已完成 3 位专家仍未收敛，直接收尾，防止死循环
        if len(visited) >= max_hops:
            return {"next_agent": "FINISH"}

        convo = state["messages"]
        history_hint = (
            f"\n【已出场的专家】{'、'.join(visited) if visited else '（无）'}"
        )
        decision_msg = supervisor_llm.invoke(
            [SystemMessage(content=SUPERVISOR_PROMPT + history_hint)] + list(convo)
        )
        raw = (decision_msg.content or "").strip().upper()
        if "FINISH" in raw or not any(k.upper() in raw for k in EXPERTS):
            nxt = "FINISH"
        else:
            # 取最先出现的专家名
            hits = [(raw.find(k.upper()), k) for k in EXPERTS if k.upper() in raw]
            hits.sort()
            nxt = hits[0][1]

        print(f"[multi] 调度中心 -> {nxt}")
        return {"next_agent": nxt}

    # ---- 节点 2：专家执行 ----
    def make_expert_node(name: str):
        async def expert_node(state: MultiAgentState) -> dict:
            agent = build_expert(name)
            spec = EXPERTS[name]
            # 给专家补一条「你是谁」的引导消息，避免它把整段历史当自己的任务
            task_msg = HumanMessage(
                content=(
                    f"请以【{spec['title']}】的身份处理用户最新需求。"
                    "只做你自己职责内的事，不要越权调用别人的工具。"
                )
            )
            result = await agent.ainvoke({"messages": list(state["messages"]) + [task_msg]})
            reply = result["messages"][-1]
            visited = list(state.get("visited", []))
            visited.append(name)
            # 把专家的回复打上角色标记，方便回溯
            marked = AIMessage(
                content=reply.content,
                name=name,
                additional_kwargs={"agent": name, "title": spec["title"]},
            )
            return {"messages": [marked], "visited": visited}

        return expert_node

    # ---- 节点 3：汇总 ----
    async def summarize_node(state: MultiAgentState) -> dict:
        """把所有专家的产出汇总成给用户的最终答复。"""
        # 没有任何专家出场：说明这是纯寒暄/常识问答，直接由对外发言人回答。
        if not state.get("visited"):
            prompt = (
                "你是【智能小秘书】。用户这句话不需要调用任何工具，请直接自然、"
                "友好地中文回答。如果用户是在打招呼，简单介绍自己是智能小秘书、"
                "可以帮它搜索资料、查天气车票、算数出图即可，控制在 3 句话内。"
            )
            result = await supervisor_llm.ainvoke(
                [SystemMessage(content=prompt)] + list(state["messages"])
            )
            return {
                "messages": [
                    AIMessage(
                        content=result.content or "",
                        name="final",
                        additional_kwargs={"agent": "final"},
                    )
                ]
            }

        prompt = (
            "你是【智能小秘书】的对外发言人。下面是各位专家的处理结果，"
            "请整合成一段完整、口语化、结构清晰的中文答复给用户。\n"
            "要求：\n"
            "1. 直接回答用户问题，不要罗列专家名字或内部流程；\n"
            "2. 保留关键数据（时间、价格、耗时、结论）；\n"
            "3. 如果专家返回了 Markdown 图片语法 `![](路径)`，原样保留；\n"
            "4. 用 Markdown 排版，简洁友好。"
        )
        result = await supervisor_llm.ainvoke(
            [SystemMessage(content=prompt)] + list(state["messages"])
        )
        return {
            "messages": [
                AIMessage(
                    content=result.content or "",
                    name="final",
                    additional_kwargs={"agent": "final"},
                )
            ]
        }

    # ---- 条件路由 ----
    def route(state: MultiAgentState) -> Literal["researcher", "travel", "data", "__end__"]:
        nxt = state.get("next_agent", "FINISH")
        return "__end__" if nxt == "FINISH" else nxt

    # ---- 组装图 ----
    graph = StateGraph(MultiAgentState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("summarize", summarize_node)
    for name in EXPERTS:
        graph.add_node(name, make_expert_node(name))

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route,
        {**{n: n for n in EXPERTS}, "__end__": "summarize"},
    )
    # 每位专家做完都回到调度中心，由它决定是否还需要别的专家
    for name in EXPERTS:
        graph.add_edge(name, "supervisor")
    graph.add_edge("summarize", END)

    return graph.compile(checkpointer=InMemorySaver())


# ------------------------------------------------------------------
# 4. 命令行冒烟测试
# ------------------------------------------------------------------
async def _demo():
    """python -m agent.multi_agent"""
    agent = await create_multi_agent()
    config = {"configurable": {"thread_id": "multi-demo"}}

    questions = [
        "帮我查一下北京南到上海虹桥 2026-10-20 的高铁票，顺便看看杭州今天的天气",
        "计算 (3 + 5) x 12 等于多少？",
        "搜索一下 MCP 协议是什么，用一句话概括",
    ]
    for q in questions:
        print("\n" + "=" * 66)
        print(f"用户：{q}")
        resp = await agent.ainvoke({"messages": [HumanMessage(content=q)]}, config)
        experts = [m.name for m in resp["messages"] if getattr(m, "name", None) in EXPERTS]
        print(f"出场专家：{experts}")
        print(f"最终答复：{resp['messages'][-1].content[:600]}")


if __name__ == "__main__":
    asyncio.run(_demo())

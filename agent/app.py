"""智能小秘书 —— Gradio 流式界面（Agent 部署入口）。

支持两种编排模式，界面上可随时切换：
  · 单 Agent  ：LangGraph ReAct，一个大脑 + 全部工具（agent/my_agent.py）
  · 多 Agent  ：Supervisor 调度 + 研究/出行/数据 三位专家（agent/multi_agent.py）

两种模式共用同一套 MCP 工具与同一个 Gradio 界面，只有编排层不同。

运行：
    python -m agent.app
浏览器打开 http://127.0.0.1:7860
"""
from __future__ import annotations

import asyncio
import uuid
from typing import AsyncGenerator, Dict, List, Optional

import gradio as gr
from gradio import ChatMessage
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from agent.my_agent import create_agent
from agent.multi_agent import EXPERTS, create_multi_agent
from config.mcp_config import build_mcp_client_config, describe_config
from env_utils import DEEPSEEK_MODEL, OUTPUT_DIR

# ------------------------------------------------------------------
# Agent 单例：两种模式各缓存一个，MCP 连接只建立一次
# ------------------------------------------------------------------
_agents: Dict[str, object] = {}
_locks: Dict[str, asyncio.Lock] = {}


def _get_lock(mode: str) -> asyncio.Lock:
    """锁要绑定到 Gradio 的事件循环，延迟创建。"""
    if mode not in _locks:
        _locks[mode] = asyncio.Lock()
    return _locks[mode]


# 模式标识 -> (显示名, 构建函数)
MODES = {
    "single": ("单 Agent（ReAct）", create_agent),
    "multi": ("多 Agent（调度 + 3 专家）", create_multi_agent),
}


async def get_agent(mode: str):
    if mode not in _agents:
        async with _get_lock(mode):
            if mode not in _agents:
                _agents[mode] = await MODES[mode][1]()
    return _agents[mode]


def new_config() -> dict:
    """每个浏览器会话一个 thread_id，短期记忆由 InMemorySaver 按 thread 隔离。"""
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


# ------------------------------------------------------------------
# 事件处理
# ------------------------------------------------------------------
def add_message(
    chat_history: List[Dict],
    user_message: str,
    session: Optional[dict],
    mode: str,
):
    """把用户输入塞进聊天记录，锁住输入框，并保证会话有 thread_id。"""
    if user_message:
        chat_history.append({"role": "user", "content": user_message})
    if not isinstance(session, dict):
        session = new_config()
    if not isinstance(mode, str) or mode not in MODES:
        mode = "multi"
    return chat_history, gr.Textbox(value=None, interactive=False), session, mode


def switch_mode(chat_history: List[Dict], session: Optional[dict], mode: str):
    """切换编排模式：清空当前对话并换 thread_id，避免两种图共用状态。"""
    label = MODES.get(mode, MODES["multi"])[0]
    chat_history = list(chat_history or [])
    chat_history.append(
        ChatMessage(
            role="assistant",
            content=f"已切换到 **{label}** 模式，对话记忆已重置。",
            metadata={"title": "🔄 模式切换"},
        )
    )
    return chat_history, new_config(), mode


def _shorten(text: str, limit: int = 1200) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit] + f"\n…（已截断，共 {len(text)} 字）"


def _node_of(meta) -> Optional[str]:
    """从流式 metadata 里取 langgraph_node。

    LangGraph 的 metadata 在不同版本下可能是 dict，也可能是 (dict, dict) 元组，
    这里两种都兜住，取第一个带 langgraph_node 的。
    """
    if isinstance(meta, dict):
        return meta.get("langgraph_node")
    if isinstance(meta, (list, tuple)):
        for m in meta:
            if isinstance(m, dict) and "langgraph_node" in m:
                return m["langgraph_node"]
    return None


async def submit_messages(
    chat_history: List[Dict], session: Optional[dict], mode: str
) -> AsyncGenerator[List[Dict], None]:
    """流式处理消息的核心函数：边生成边刷新界面。

    多 Agent 模式下按 `metadata["langgraph_node"]` 精确归位：
      · supervisor 节点产出的 token 直接丢弃（那是调度中心在选人，不该给用户看）；
      · 专家节点的产出渲染成「👤 交接给【某专家】」卡片；
      · summarize 节点的产出是最终答复，走打字机效果。
    """
    if not chat_history:
        return

    if not isinstance(mode, str) or mode not in MODES:
        mode = "multi"
    config = session if isinstance(session, dict) else new_config()
    agent = await get_agent(mode)
    user_input = chat_history[-1]["content"]
    is_multi = mode == "multi"

    inputs = {"messages": [HumanMessage(content=user_input)]}

    full_response = ""
    queue: asyncio.Queue = asyncio.Queue()

    async def produce():
        try:
            async for chunk in agent.astream(
                inputs, config, stream_mode=["messages", "updates"]
            ):
                await queue.put(chunk)
        except Exception as exc:  # noqa: BLE001
            await queue.put(exc)
        finally:
            await queue.put(None)

    producer = asyncio.create_task(produce())

    while True:
        chunk = await queue.get()
        if chunk is None:
            break

        if isinstance(chunk, Exception):
            chat_history.append(
                ChatMessage(role="assistant", content=f"⚠️ 调用出错：{chunk}")
            )
            yield chat_history
            break

        # 多 stream_mode 下 chunk 形如 ("messages", (message, metadata))
        if not (isinstance(chunk, tuple) and len(chunk) == 2):
            continue
        mode_name, payload = chunk
        if mode_name != "messages":
            continue

        # payload = (message, metadata)
        if isinstance(payload, (list, tuple)):
            message = payload[0]
            meta = payload[1] if len(payload) > 1 else {}
        else:
            message, meta = payload, {}
        node = _node_of(meta)

        # ---- 多 Agent：丢弃调度中心自己的产出（用户不该看到 "FINISH"）----
        if is_multi and node == "supervisor":
            continue

        # ---- 多 Agent：专家产出 -> 「交接卡片」（专家结果是整条 AIMessage，不逐字）----
        if is_multi and isinstance(message, AIMessage) and not isinstance(message, AIMessageChunk):
            agent_key = getattr(message, "name", None) or node
            if agent_key in EXPERTS and message.content:
                spec = EXPERTS[agent_key]
                chat_history.append(
                    ChatMessage(
                        role="assistant",
                        content=message.content
                        if isinstance(message.content, str)
                        else str(message.content),
                        metadata={
                            "title": f"👤 交接给【{spec['title']}】({agent_key})",
                            "status": "done",
                        },
                    )
                )
                yield chat_history
                full_response = ""  # 后续 summarize 的答复另起一条
                continue

        # ---- 模型回答：累积并原地刷新最后一条，实现打字机效果 ----
        # 只吃 AIMessageChunk（逐字流），忽略 stream 末尾重复下发的完整 AIMessage，
        # 否则最终答复会被渲染两遍。
        if isinstance(message, AIMessageChunk) and message.content:
            text = message.content if isinstance(message.content, str) else str(message.content)
            if (
                chat_history
                and isinstance(chat_history[-1], ChatMessage)
                and not chat_history[-1].metadata
            ):
                full_response += text
                chat_history[-1].content = full_response
            else:
                full_response = text
                chat_history.append(
                    ChatMessage(role="assistant", content=full_response)
                )
            yield chat_history

        # ---- 工具调用：单独一条可折叠卡片，便于观察 Agent 轨迹 ----
        elif isinstance(message, ToolMessage):
            tool_name = getattr(message, "name", "tool")
            owner = f"（{EXPERTS[node]['title']}）" if (is_multi and node in EXPERTS) else ""
            chat_history.append(
                ChatMessage(
                    role="assistant",
                    content=f"🔧 调用工具 `{tool_name}`{owner}\n\n```text\n{_shorten(message.content)}\n```",
                    metadata={"title": f"🛠️ 已调用工具 {tool_name}"},
                )
            )
            yield chat_history

    await producer


def reset_input() -> gr.Textbox:
    return gr.Textbox(interactive=True)


# ------------------------------------------------------------------
# 界面
# ------------------------------------------------------------------
MCP_DESC = describe_config(build_mcp_client_config())

MODE_DESC = """**两种编排模式对比**

| | 单 Agent | 多 Agent |
|---|---|---|
| 大脑数量 | 1 个（一个 LLM 实例） | 1 个调度 + 3 个专家，各自独立 LLM |
| 决策方式 | 一个 ReAct 循环里自己挑工具 | 调度中心先判断「该谁做」，再转交专家 |
| 工具可见性 | 全部 10 个工具 | 每位专家只看得到自己领域的工具 |
| 适合场景 | 简单、单步任务，链路短、成本低 | 复杂、跨领域任务，职责分明、可扩展 |
| 额外开销 | 无 | 多一次调度判断 + 一次总结 |
| 可观测性 | 只看工具调用 | 能看到「交接给谁」，职责轨迹更清晰 |"""

CSS = """
.system { color: #666; font-style: italic; }
#mcp-info { font-size: 12px; color: #888; }
"""

with gr.Blocks(title="我的智能小秘书") as demo:
    gr.Markdown(
        "# 🤖 我的智能小秘书\n"
        f"技术栈：LangGraph + LangChain + MCP + Gradio　|　模型：`{DEEPSEEK_MODEL}`（DeepSeek）\n\n"
        "试试：*计算一下 (3+5)×12* ／ *杭州今天天气怎么样* ／ "
        "*帮我查 2026-10-20 北京南到上海虹桥的高铁票* ／ *搜索一下 MCP 协议是什么* ／ "
        "*把这份数据画成柱状图：月份,销量\\n1月,120\\n2月,150\\n3月,98*"
    )

    with gr.Accordion("🧠 编排模式（单 Agent / 多 Agent）", open=True):
        mode_radio = gr.Radio(
            choices=[(MODES[k][0], k) for k in MODES],
            value="multi",
            label="选择模式",
            info="多 Agent 模式下，界面上会显示「👤 交接给【某某专家】」卡片，可看到分工过程。",
        )
        gr.Markdown(MODE_DESC)

    with gr.Accordion("🔌 已连接的 MCP 服务端", open=False):
        gr.Markdown(f"```text\n{MCP_DESC}\n```", elem_id="mcp-info")

    chatbot = gr.Chatbot(
        height=520,
        render_markdown=True,
        line_breaks=False,
        label="对话记录",
    )

    chat_input = gr.Textbox(
        placeholder="请输入您的消息...",
        label="用户输入",
        max_lines=5,
        container=False,
    )

    with gr.Row():
        submit_btn = gr.Button("发送", variant="primary")
        clear_btn = gr.Button("清空对话")

    # 会话级 config（含 thread_id）与当前模式
    session_config = gr.State(None)
    mode_state = gr.State("multi")

    io_msg = dict(
        fn=add_message,
        inputs=[chatbot, chat_input, session_config, mode_state],
        outputs=[chatbot, chat_input, session_config, mode_state],
    )

    # 回车提交
    (
        chat_input.submit(queue=False, **io_msg)
        .then(
            fn=submit_messages,
            inputs=[chatbot, session_config, mode_state],
            outputs=chatbot,
            api_name="chat_stream",
        )
        .then(fn=reset_input, inputs=None, outputs=[chat_input])
    )

    # 按钮提交
    (
        submit_btn.click(queue=False, **io_msg)
        .then(
            fn=submit_messages,
            inputs=[chatbot, session_config, mode_state],
            outputs=chatbot,
        )
        .then(fn=reset_input, inputs=None, outputs=[chat_input])
    )

    # 切换模式：清空对话 + 换 thread_id
    mode_radio.change(
        fn=switch_mode,
        inputs=[chatbot, session_config, mode_radio],
        outputs=[chatbot, session_config, mode_state],
        queue=False,
    )

    # 清空对话：同时换一个 thread_id，等于开一段全新记忆
    clear_btn.click(
        fn=lambda: ([], new_config()),
        inputs=None,
        outputs=[chatbot, session_config],
        queue=False,
    ).then(fn=reset_input, inputs=None, outputs=[chat_input])


if __name__ == "__main__":
    print(f"[ui] MCP 服务端：\n{MCP_DESC}")
    print(f"[ui] 图表产物目录：{OUTPUT_DIR}")
    print(f"[ui] 可用模式：{ {k: v[0] for k, v in MODES.items()} }")
    demo.queue().launch(
        server_name="127.0.0.1",
        server_port=7860,
        show_error=True,
        theme=gr.themes.Soft(),
        css=CSS,
    )

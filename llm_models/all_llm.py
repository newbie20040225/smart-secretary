"""大模型工厂。

原项目（mcp_graph/mcp_agent/my_llm.py、text2sql/sql_graph/my_llm.py）把
智谱 / OpenAI / DeepSeek / 私有化 vLLM 四种配置写在一起、注释掉不用的，
这里沿用同样的设计思路：把可选项保留成注释，默认启用 DeepSeek。

密钥一律来自 env_utils（即 .env），不在代码里硬编码。
"""
from langchain_openai import ChatOpenAI

from env_utils import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    OPENAI_API_KEY,
    ZHIPU_API_KEY,
)

# ---------- 默认启用：DeepSeek ----------
llm = ChatOpenAI(
    temperature=0,
    model=DEEPSEEK_MODEL,
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    timeout=120,
    max_retries=2,
)

# ---------- 可选：智谱 GLM ----------
# llm = ChatOpenAI(
#     temperature=0,
#     model="glm-4-air-250414",
#     api_key=ZHIPU_API_KEY,
#     base_url="https://open.bigmodel.cn/api/paas/v4/",
# )

# ---------- 可选：OpenAI / 中转 ----------
# llm = ChatOpenAI(
#     temperature=0,
#     model="gpt-4o-mini",
#     api_key=OPENAI_API_KEY,
#     base_url="https://api.openai.com/v1",
# )

# ---------- 可选：私有化部署（vLLM 起 OpenAI 兼容服务，端口 6006）----------
# llm = ChatOpenAI(
#     temperature=0,
#     model="qwen3-8b",
#     openai_api_key="EMPTY",
#     openai_api_base="http://localhost:6006/v1",
#     extra_body={"chat_template_kwargs": {"enable_thinking": False}},
# )


def get_llm(**overrides):
    """按需覆盖参数创建实例（例如流式场景传 streaming=True）。"""
    params = {
        "temperature": 0,
        "model": DEEPSEEK_MODEL,
        "api_key": DEEPSEEK_API_KEY,
        "base_url": DEEPSEEK_BASE_URL,
        "timeout": 120,
        "max_retries": 2,
    }
    params.update(overrides)
    return ChatOpenAI(**params)


__all__ = ["llm", "get_llm"]

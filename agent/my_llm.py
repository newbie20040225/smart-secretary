"""模型别名模块。

对应原项目 mcp_graph/mcp_agent/my_llm.py —— agent 层统一从这里取 llm，
真正的大模型定义在 llm_models/all_llm.py，避免多处重复配置。
"""
from llm_models.all_llm import get_llm, llm

__all__ = ["llm", "get_llm"]

"""Prompt 管理包 —— 集中管理所有 LLM Prompt 模板。

注册表在首次导入时自动加载所有 Prompt。
原有 app/tools/prompts.py 中的函数接口保持不变，内部改为从注册表获取 Prompt。
"""

from app.prompts.registry import PromptRegistry, register_all

# 首次导入时自动注册所有 Prompt
register_all()

__all__ = ["PromptRegistry", "register_all"]

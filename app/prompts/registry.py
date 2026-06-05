"""Prompt 注册表 —— 集中管理所有 Prompt 模板，支持版本标记。"""

from __future__ import annotations


class PromptRegistry:
    """Prompt 注册表。
    用法:
        PromptRegistry.get("EVAL_PROMPT", text="...")
    """

    _prompts: dict[str, dict] = {}  # name → {version, template, source}

    @classmethod
    def register(cls, name: str, template: str, version: str = "1.0", source: str = "") -> None:
        """注册一个 prompt 模板"""
        cls._prompts[name] = {
            "version": version,
            "template": template,
            "source": source or name,
        }

    @classmethod
    def get(cls, name: str, **kwargs) -> str:
        """获取并填充 prompt 模板。填充方式: {key} 占位符替换。"""
        entry = cls._prompts.get(name)
        if entry is None:
            raise KeyError(f"Prompt '{name}' 未注册。可用的 prompt: {list(cls._prompts.keys())}")
        template = entry["template"]
        for key, value in kwargs.items():
            template = template.replace(f"{{{key}}}", str(value))
        return template

    @classmethod
    def list_all(cls) -> list[dict]:
        """列出所有已注册的 prompt"""
        return [
            {"name": k, "version": v["version"], "source": v["source"], "template": v["template"]}
            for k, v in cls._prompts.items()
        ]


def register_all() -> None:
    """加载所有模块中的 prompt（被 __init__.py 调用）"""
    from app.prompts import coach  # noqa
    from app.prompts import companion  # noqa
    from app.prompts import exercise  # noqa
    from app.prompts import review  # noqa

"""所有 LLM Prompt 调用封装。Prompt 模板定义移至 app/prompts/ 目录。
对外接口函数保持不变，内部通过 PromptRegistry 获取模板。
"""

import json
import re

from app.services.llm_service import get_llm
from app.prompts import PromptRegistry  # 导入时自动注册所有 Prompt


def _call_llm(prompt: str, user_msg: str = "", temperature: float = 0.1) -> str:
    llm = get_llm(temperature).bind(response_format={"type": "json_object"})
    resp = llm.invoke(prompt + "\n\n" + user_msg)
    return str(resp.content) if hasattr(resp, "content") else str(resp)


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return {}


# ── 创作教练 ────────────────────────────────────────────────

def evaluate_text(text: str) -> dict:
    prompt = PromptRegistry.get("EVAL_PROMPT", text=text)
    raw = _call_llm(prompt)
    data = _extract_json(raw)
    dims = data.get("dimensions", [])
    scores = [d.get("score", 0) for d in dims if isinstance(d, dict)]
    return {"dimensions": dims, "highlight": data.get("highlight", ""),
            "improvement": data.get("improvement", ""),
            "overall_score": sum(scores) // len(scores) if scores else 0}


def polish_text(text: str, style: str = "", level: str = "medium") -> dict:
    level_map = {
        "light": ("轻修", "仅修复错别字、的得地、标点、基础语法错误"),
        "medium": ("润色", "修复语法错误，优化用词精准度，调整句式节奏"),
        "heavy": ("重写", "全部修正 + 按目标风格深度重写"),
    }
    ld, lr = level_map.get(level, level_map["medium"])
    sd = f"目标风格：「{style}」" if style else ""
    rd = f"参考范文：\n{style}" if style else ""
    prompt = PromptRegistry.get("POLISH_PROMPT",
                                text=text, level_desc=ld, level_requirements=lr,
                                style_desc=sd, reference_desc=rd)
    data = _extract_json(_call_llm(prompt))
    return {"polished": data.get("polished", ""), "changes": data.get("changes", "")}


def inspire_writing(user_input: str, context: str = "", mode: str = "") -> dict:
    ctx = f"作品上下文：\n```\n{context}\n```" if context else ""
    mi = {"inspire": "从3-5个不同角度展开情节构思",
          "generate": "直接输出一段完整文字"}.get(mode, "判断意图自动选择模式")
    prompt = PromptRegistry.get("INSPIRE_PROMPT",
                                context_section=ctx, input=user_input, mode_instruction=mi)
    data = _extract_json(_call_llm(prompt))
    return {"mode": data.get("mode", "inspire"),
            "results": [{"angle": i.get("angle", ""), "content": i.get("content", "")}
                        for i in data.get("results", []) if isinstance(i, dict)]}


def coach_chat(work_content: str, message: str, history: str = "") -> str:
    prompt = PromptRegistry.get("COACH_CHAT_PROMPT",
                                work_content=work_content or "（无全文）")
    user_msg = f"对话历史：\n{history}\n当前消息：\n{message}" if history else message
    llm = get_llm(0.5)
    resp = llm.invoke(prompt + "\n\n" + user_msg)
    return str(resp.content) if hasattr(resp, "content") else str(resp)


# ── 阅读伴侣 ────────────────────────────────────────────────

def companion_chat(text: str, question: str, history: str = "") -> str:
    prompt = PromptRegistry.get("COMPANION_CHAT", text=text)
    user_msg = f"对话历史：\n{history}\n问题：\n{question}" if history else question
    llm = get_llm(0.5)
    resp = llm.invoke(prompt + "\n\n" + user_msg)
    return str(resp.content) if hasattr(resp, "content") else str(resp)


def companion_guide_chat(title: str, author: str, is_classic: bool, work_content: str,
                         message: str, history: str = "") -> str:
    name = "GUIDE_CLASSIC" if is_classic else "GUIDE_SIMPLE"
    prompt = PromptRegistry.get(name, title=title, author=author or "佚名",
                                work_content=work_content or "（无全文）")
    default = "请开始为我导读这部作品" if is_classic else "你好，我们一起聊聊这部作品吧"
    user_msg = (default if not message and not history
                else (f"对话历史：\n{history}\n当前消息：\n{message}" if history else message))
    llm = get_llm(0.5)
    resp = llm.invoke(prompt + "\n\n" + user_msg)
    return str(resp.content) if hasattr(resp, "content") else str(resp)

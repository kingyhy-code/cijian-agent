"""练习生成与批改工具。"""

import json

from app.models.schemas import Exercise, ExerciseReview
from app.services.llm_service import get_llm

EXERCISE_PROMPT = """你是中文写作教练。根据以下信息生成一道个性化练习题。

## 练习主题
{topic}

## 难度
{difficulty}

## 题型
{exercise_type}

题目类型说明：
- fill_blank: 填空题（在句子中留空，填入正确的字词）
- correction: 改错题（给出有语病的句子，让学生修改）
- rewrite: 改写题（给出表达不佳的句子，让学生润色）
- multiple_choice: 选择题（4个选项，1个正确答案）

只输出 JSON（不要其他文字）：
{
  "type": "题目类型",
  "topic": "主题",
  "question": "题目正文",
  "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
  "answer": "正确答案",
  "explanation": "解析（说明为什么这个答案，涉及什么知识点）",
  "difficulty": "难度"
}
注意：options 仅 multiple_choice 类型需要，其他类型不需要该字段。"""

REVIEW_PROMPT = """你是中文写作教练。评估学生的答题情况。

## 题目
{exercise_json}

## 学生答案
{user_answer}

只输出 JSON：
{
  "is_correct": true/false,
  "explanation": "详细解析，说明对在哪里或错在哪里",
  "suggestion": "进一步的学习建议"
}"""


def generate_exercise(topic: str = "综合", difficulty: str = "中等",
                      exercise_type: str = "correction") -> Exercise:
    """为指定主题和难度生成一道练习题。"""
    llm = get_llm(temperature=0.3)
    prompt = EXERCISE_PROMPT.replace("{topic}", topic).replace("{difficulty}", difficulty).replace("{exercise_type}", exercise_type)
    resp = llm.invoke(prompt)
    raw = resp.content if hasattr(resp, "content") else str(resp)

    try:
        data = _extract_json(raw)
        return Exercise(
            type=data.get("type", exercise_type),
            topic=data.get("topic", topic),
            question=data.get("question", ""),
            options=data.get("options"),
            answer=data.get("answer", ""),
            explanation=data.get("explanation", ""),
            difficulty=data.get("difficulty", difficulty),
        )
    except Exception:
        return Exercise(type=exercise_type, topic=topic, question="生成失败",
                        answer="", explanation="", difficulty=difficulty)


def review_exercise_answer(exercise: Exercise, user_answer: str) -> ExerciseReview:
    """批改学生的练习答案。"""
    llm = get_llm(temperature=0.1)
    exercise_json = json.dumps({
        "type": exercise.type, "topic": exercise.topic,
        "question": exercise.question, "answer": exercise.answer,
        "explanation": exercise.explanation,
    }, ensure_ascii=False, indent=2)
    prompt = REVIEW_PROMPT.replace("{exercise_json}", exercise_json).replace("{user_answer}", user_answer)
    resp = llm.invoke(prompt)
    raw = resp.content if hasattr(resp, "content") else str(resp)

    try:
        data = _extract_json(raw)
        return ExerciseReview(
            is_correct=data.get("is_correct", False),
            explanation=data.get("explanation", ""),
            suggestion=data.get("suggestion", ""),
        )
    except Exception:
        return ExerciseReview(is_correct=False, explanation="批改失败", suggestion="")


def _extract_json(text: str) -> dict:
    text = text.strip()
    import re
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("\n```", 1)[0] if "```" in text else text
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        return json.loads(m.group())
    return {}

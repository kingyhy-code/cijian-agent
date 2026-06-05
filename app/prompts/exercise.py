"""练习生成与批改 Prompt 模板。"""

from app.prompts.registry import PromptRegistry

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


for _name, _template in [
    ("EXERCISE_PROMPT", EXERCISE_PROMPT),
    ("REVIEW_PROMPT", REVIEW_PROMPT),
]:
    PromptRegistry.register(_name, _template, source="exercise")

"""L1-L3 规则引擎 —— 纯正则，不依赖 LLM，毫秒级响应。从 Java CoachRuleEngine 移植。"""

import re
from typing import Any

TYPO_MAP: dict[str, str] = {
    "在次": "再次", "以经": "已经", "己经": "已经", "自已": "自己",
    "决对": "绝对", "即然": "既然", "既使": "即使", "不只": "不止",
    "名子": "名字", "知到": "知道", "一至": "一致",
    "再接再励": "再接再厉", "迫不急待": "迫不及待",
    "不径而走": "不胫而走", "一愁莫展": "一筹莫展",
    "穿流不息": "川流不息", "按步就班": "按部就班",
    "别出新裁": "别出心裁", "不记其数": "不计其数",
    "混然一体": "浑然一体", "换然一新": "焕然一新",
    "汗流夹背": "汗流浃背", "挤挤一堂": "济济一堂",
    "鸠占雀巢": "鸠占鹊巢", "谍谍不休": "喋喋不休",
    "感决": "感觉", "感较": "感觉",
    "在说": "再说", "在见": "再见", "在来": "再来",
    "永许": "允许", "须要": "需要",
    "关建": "关键", "由其": "尤其", "厉史": "历史",
    "决望": "绝望", "既将": "即将",
    "合式": "合适", "影象": "影像", "印像": "印象",
    "欢渡": "欢度", "渡假": "度假", "渡过": "度过",
    "侯鸟": "候鸟", "气侯": "气候", "侯车": "候车",
    "弦律": "旋律", "眩耀": "炫耀", "自毫": "自豪",
    "霄夜": "宵夜", "通霄": "通宵", "元霄": "元宵",
    "浪废": "浪费",
    "无可耐何": "无可奈何", "不矛置评": "不予置评",
    "毫尤疑问": "毫无疑问", "一无反顾": "义无反顾",
    "形形式式": "形形色色", "不醒人事": "不省人事",
    "不异而飞": "不翼而飞", "鬼计多端": "诡计多端",
    "口干舌躁": "口干舌燥", "大声急呼": "大声疾呼",
    "不齿下问": "不耻下问",
    "万事具备": "万事俱备", "因地治宜": "因地制宜",
    "当人不让": "当仁不让", "情不自尽": "情不自禁",
    "搔首弄资": "搔首弄姿", "山青水秀": "山清水秀",
    "悬梁刺骨": "悬梁刺股", "仗义直言": "仗义执言",
    "默守成规": "墨守成规", "德高望众": "德高望重",
    "坐想其成": "坐享其成", "谈笑风声": "谈笑风生",
    "甘败下风": "甘拜下风", "自抱自弃": "自暴自弃",
    "指高气扬": "趾高气扬", "一股作气": "一鼓作气",
    "卑恭屈膝": "卑躬屈膝", "不寒而粟": "不寒而栗",
    "怨天忧人": "怨天尤人", "痴心枉想": "痴心妄想",
    "得话": "的话", "的说": "地说",
}

DE_AFTER_VERB = re.compile(
    r"(做|说|写|表现|发展|处理|解决|完成|执行|安排|准备|设计|实现|管理|控制|"
    r"吃|喝|玩|睡|走|跑|跳|唱|画|学|教|读|看|听|想|记|变|长|瘦|胖|"
    r"累|忙|气|急|吓|乐|哭|笑|搞|弄|打|拿|放|推|拉|提|抓)的")

ADJ_DE_BEFORE_VERB = re.compile(
    r"(?:认认真真|仔仔细细|清清楚楚|干干净净|明明白白|高高兴兴|"
    r"轻轻松松|慢慢悠悠|"
    r"认真|仔细|努力|快速|缓慢|轻轻|狠狠|悄悄|默默|静静|高兴|兴奋|"
    r"激动|紧张|着急|耐心|细心|大胆|小心|随意|拼命|不断|反复)的([一-鿿])")

PASSIVE_RE = re.compile(r"(被|由|为[一-鿿]*所)")
SENTENCE_SPLIT = re.compile(r"[。！？；!?;\n]+|(?<=[.!?])\s+")
DUPLICATE_PHRASE = re.compile(r"(.{5,30})\1")

SLANG_MAP: dict[str, str] = {
    "栓Q": "谢谢（建议使用规范表达）", "栓q": "谢谢（建议使用规范表达）",
    "绝绝子": "非常好（建议使用规范表达）",
    "YYDS": "永远的神 / 非常出色", "yyds": "永远的神 / 非常出色",
    "emo了": "感到沮丧 / 情绪低落", "芭比Q": "完蛋了 / 糟糕",
    "家人们": "大家", "破防了": "被触动 / 情绪崩溃",
    "摆烂": "放任不管 / 消极应对", "躺平": "放弃努力 / 安于现状",
    "内卷": "过度竞争", "凡尔赛": "故作低调的炫耀",
}

REDUNDANT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"进行了(?!研究|调查|分析|改革|治疗|考核|评估)"), "「进行了」可省略或替换为具体动词"),
    (re.compile(r"这一个"), "「一个」冗余，可省略"),
    (re.compile(r"有着"), "「有着」可简化为「有」"),
    (re.compile(r"这一(?!个)"), "「这一」可简化为「这」"),
    (re.compile(r"关于(.{0,10})的问题"), "「关于...的问题」表达冗余"),
    (re.compile(r"通过(.{0,10})的方式"), "「通过...的方式」表达冗余"),
    (re.compile(r"在(.{0,20})方面"), "「在...方面」可简化"),
]


def check_l1_l3(text: str) -> dict[str, Any]:
    suggestions: list[dict] = []

    for wrong, correct in TYPO_MAP.items():
        offset = 0
        while True:
            idx = text.find(wrong, offset)
            if idx == -1: break
            suggestions.append({"level": "L1", "start": idx, "end": idx + len(wrong),
                "original": wrong, "replacement": correct,
                "message": f"疑似错别字：「{wrong}」应为「{correct}」"})
            offset = idx + len(wrong)

    for m in DE_AFTER_VERB.finditer(text):
        start = m.start() + len(m.group(1))
        suggestions.append({"level": "L1", "start": start, "end": start + 1,
            "original": "的", "replacement": "得",
            "message": "动词后接补语时应用「得」，例如「做得好」「跑得快」"})
    for m in ADJ_DE_BEFORE_VERB.finditer(text):
        start = m.start() + len(m.group(1))
        suggestions.append({"level": "L1", "start": start, "end": start + 1,
            "original": "的", "replacement": "地",
            "message": "状语修饰动词时应用「地」，例如「认真地学习」「轻轻地放下」"})

    for sent in SENTENCE_SPLIT.split(text):
        sent = sent.strip()
        if not sent: continue
        clean = sent.replace(" ", "")
        if len(clean) > 80:
            idx = text.find(sent)
            if idx != -1:
                suggestions.append({"level": "L2", "start": idx, "end": idx + len(sent),
                    "original": sent, "replacement": sent,
                    "message": f"该句共 {len(clean)} 字，超过 80 字阈值，建议拆分或精简"})

    for m in PASSIVE_RE.finditer(text):
        matched = m.group()
        suggestions.append({"level": "L2", "start": m.start(), "end": m.end(),
            "original": matched, "replacement": matched,
            "message": f"检测到被动标记「{matched}」，建议考虑改用主动语态"})

    for slang, tip in SLANG_MAP.items():
        offset = 0
        while True:
            idx = text.find(slang, offset)
            if idx == -1: break
            suggestions.append({"level": "L3", "start": idx, "end": idx + len(slang),
                "original": slang, "replacement": "",
                "message": f"检测到网络用语「{slang}」：{tip}"})
            offset = idx + len(slang)

    for pattern, desc in REDUNDANT_PATTERNS:
        for m in pattern.finditer(text):
            suggestions.append({"level": "L3", "start": m.start(), "end": m.end(),
                "original": m.group(), "replacement": "", "message": desc})

    for m in DUPLICATE_PHRASE.finditer(text):
        phrase = m.group(1)
        if not re.search(r"[一-鿿\w]", phrase): continue
        suggestions.append({"level": "L3", "start": m.start(), "end": m.end(),
            "original": m.group(), "replacement": phrase,
            "message": f"「{phrase[:20]}…」重复出现，建议删除多余部分"})

    summary = {}
    for s in suggestions:
        summary[s["level"]] = summary.get(s["level"], 0) + 1
    return {"total": len(suggestions), "summary": summary, "suggestions": suggestions}

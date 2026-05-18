"""Chinese tokenizer — jieba for Traditional & Simplified Chinese."""
from __future__ import annotations

try:
    import jieba
    _JIEBA_AVAILABLE = True
except ImportError:
    _JIEBA_AVAILABLE = False

# Common Chinese stop characters (punctuation, measures, filler).
_ZH_STOP_WORDS: set[str] = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "哪",
    "什么", "怎么", "如何", "吗", "吧", "啊", "呢", "哦", "嗯", "哈", "呀",
    "可以", "能", "应该", "需要", "想", "知道", "觉得", "让", "被", "把",
    "从", "对", "向", "以", "及", "等", "或", "但", "然而", "不过",
    "所以", "因为", "如果", "虽然", "而且", "并且", "还是", "只是",
    "还", "又", "再", "才", "刚", "已经", "正在", "将", "会",
    "这个", "那个", "这些", "那些", "这样", "那样", "这么", "那么",
    "更", "最", "比较", "非常", "特别", "太", "真", "挺", "蛮",
    "做", "用", "来", "去", "拿", "给", "让", "把", "被",
    "为", "与", "之", "其", "所", "者", "而", "且", "于",
    "则", "亦", "勿", "毋", "别", "莫", "未", "无", "非",
    "各", "每", "某", "另", "另个", "另个", "另",
    # Punctuation / whitespace — jieba handles these but filter anyway
    "，", "。", "！", "？", "、", "；", "：", "“", "”", "「", "」",
    "『", "』", "（", "）", "《", "》", "【", "】", "—", "…",
    "～", "＃", "＠", "％", "＆", "＊", "＋", "－", "／",
}


class ChineseTokenizer:
    """Traditional/Simplified Chinese tokenizer using jieba.

    Jieba handles both Traditional and Simplified Chinese reasonably well.
    For best Traditional Chinese results, consider loading a Traditional
    dictionary: ``jieba.set_dictionary('dict.txt.big')``.
    """

    def __init__(self) -> None:
        if not _JIEBA_AVAILABLE:
            raise ImportError(
                "jieba not installed. Install with: pip install jieba"
            )

    def tokenize(self, text: str) -> list[str]:
        """Split Chinese text into word tokens, removing stop characters."""
        words = jieba.cut(text, cut_all=False)
        return [
            w.strip()
            for w in words
            if w.strip() and w not in _ZH_STOP_WORDS and len(w.strip()) > 1
        ]

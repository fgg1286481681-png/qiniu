import re

from llm_client import LLMClient, get_env
from trace_utils import TraceTimer


class ReaderAgent:
    """Reader Agent: AI first, rule-based parser fallback."""

    name = "Reader Agent"

    def __init__(self, provider=None):
        self.provider = provider or build_reader_provider()

    def run(self, text):
        timer = TraceTimer(
            self.name,
            "reader",
            getattr(self.provider, "model", None),
        )
        source = "rule"
        attempts = 0
        usage = None
        fallback_reason = None
        try:
            if self.provider:
                provider_result = self.provider.parse(text)
                parse_result = provider_result["parse_result"]
                attempts = provider_result["attempts"]
                usage = provider_result["usage"]
                source = "llm"
            else:
                parse_result = parse_chapters(text)
        except Exception as exc:
            parse_result = parse_chapters(text)
            fallback_reason = str(exc)
            parse_result["warning"] = f"Reader AI 调用失败，已回退规则解析：{fallback_reason}"

        return {
            "parse_result": parse_result,
            "trace": timer.finish(
                status="degraded" if fallback_reason else "success",
                source=source,
                summary=f"识别到 {len(parse_result['chapters'])} 个章节，解析模式：{parse_result['mode']}，来源：{source}",
                attempts=attempts,
                fallback_reason=fallback_reason,
                usage=usage,
            ),
        }


class ReaderLLMProvider:
    def __init__(self, client=None, model=None):
        self.client = client or LLMClient()
        self.model = model or get_env("READER_MODEL", "")
        self.temperature = float(get_env("READER_TEMPERATURE", "0.1"))
        self.top_p = float(get_env("READER_TOP_P", "0.9"))
        self.max_tokens = int(get_env("READER_MAX_TOKENS", "8000"))

    @property
    def enabled(self):
        return self.client.enabled and bool(self.model)

    def parse(self, text):
        if not self.enabled:
            raise RuntimeError("Reader LLM 未配置")

        paragraphs = split_paragraphs(text)
        response = self.client.chat_json(
            model=self.model,
            system_prompt=READER_SYSTEM_PROMPT,
            user_prompt=build_reader_user_prompt(paragraphs),
            temperature=self.temperature,
            top_p=self.top_p,
            max_tokens=self.max_tokens,
        )
        return {
            "parse_result": normalize_llm_parse_result(response["data"], paragraphs),
            "usage": response["usage"],
            "attempts": response["attempts"],
        }


def build_reader_provider():
    provider = ReaderLLMProvider()
    return provider if provider.enabled else None


READER_SYSTEM_PROMPT = """你是 Novel2Script 的 Reader Agent。
你的任务是阅读短篇小说原文，只做章节理解与事实抽取，不改写、不扩写、不编造。
你必须输出严格 JSON，不要 Markdown，不要解释。
JSON 顶层结构必须是：
{
  "chapters": [
    {
      "title": "章节或段落标题",
      "summary": "本章节事实摘要",
      "paragraph_start": 1,
      "paragraph_end": 5,
      "key_events": ["事件1"],
      "characters": ["人物1"],
      "locations": ["地点1"]
    }
  ],
  "mode": "llm",
  "warning": null
}
如果原文没有明确章节，请按叙事阶段拆成 3 到 8 个章节。
paragraph_start 和 paragraph_end 是包含边界的段落编号，必须覆盖原文并保持顺序，不得重叠。
不要输出原文之外的新剧情。
"""


def split_paragraphs(text):
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    if len(paragraphs) < 3:
        paragraphs = [part.strip() for part in normalized.splitlines() if part.strip()]
    return paragraphs


def build_reader_user_prompt(paragraphs):
    numbered_text = "\n\n".join(
        f"[段落 {index}]\n{paragraph}"
        for index, paragraph in enumerate(paragraphs, start=1)
    )
    return f"""请解析下面的小说文本。
要求：
1. 如果有章节标题，优先按原章节拆分。
2. 如果没有章节标题，按叙事阶段拆成 3 到 8 个章节。
3. 不要在 JSON 中重复原文，只返回 paragraph_start 和 paragraph_end。
4. 保留标题、摘要、关键事件、人物、地点。
5. 段落范围必须从 1 开始，覆盖到段落 {len(paragraphs)}，保持连续且不重叠。
6. 只输出 JSON。

小说文本：
{numbered_text}
"""


def normalize_llm_parse_result(result, paragraphs):
    raw_chapters = result.get("chapters")
    if not isinstance(raw_chapters, list):
        raise ValueError("Reader LLM 输出缺少 chapters 数组")

    chapters = []
    last_end = 0
    for item in raw_chapters:
        if not isinstance(item, dict):
            continue
        try:
            start = int(item.get("paragraph_start"))
            end = int(item.get("paragraph_end"))
        except (TypeError, ValueError):
            continue
        start = max(1, start)
        end = min(len(paragraphs), end)
        if end < start or start != last_end + 1:
            raise ValueError("Reader LLM 返回的段落范围不连续")
        body = "\n\n".join(paragraphs[start - 1 : end]).strip()
        last_end = end

        chapters.append(
            {
                "chapter_id": f"chapter_{len(chapters) + 1:03d}",
                "order": len(chapters) + 1,
                "title": str(item.get("title") or f"AI 拆分章节 {len(chapters) + 1}").strip(),
                "text": body,
                "word_count": len(body),
            }
        )

    if len(chapters) < 3:
        raise ValueError("Reader LLM 拆分章节少于 3 个")
    if last_end != len(paragraphs):
        raise ValueError("Reader LLM 未覆盖全部原文段落")

    return {
        "chapters": chapters,
        "mode": result.get("mode") or "llm",
        "warning": result.get("warning"),
    }


def parse_inline_chapter_rest(rest):
    rest = rest.strip()
    if not rest:
        return "", ""

    spaced = rest.split(maxsplit=1)
    if len(spaced) == 2:
        return spaced[0], spaced[1].strip()

    punctuation = re.search(r"[。！？!?]", rest)
    if punctuation and punctuation.start() <= 24:
        return rest[: punctuation.start()].strip(), rest[punctuation.start() + 1 :].strip()

    return rest, ""


def parse_chapters(text):
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    marker_chars = "一二三四五六七八九十百千万零〇两0-9"
    pattern = re.compile(
        rf"(?m)^\s*((?:第[{marker_chars}]+[章节回幕])|(?:Chapter\s+\d+)|(?:章节\s*[{marker_chars}]+))(?P<rest>[^\n]*)",
        re.I,
    )
    matches = list(pattern.finditer(normalized))
    chapters = []

    if matches:
        for index, match in enumerate(matches):
            next_start = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
            marker = match.group(1).strip()
            title_tail, inline_body = parse_inline_chapter_rest(match.group("rest"))
            block_body = normalized[match.end() : next_start].strip()
            body = "\n".join(part for part in [inline_body, block_body] if part).strip()
            title = f"{marker} {title_tail}".strip()
            if body:
                chapters.append(
                    {
                        "chapter_id": f"chapter_{len(chapters) + 1:03d}",
                        "order": len(chapters) + 1,
                        "title": title,
                        "text": body,
                        "word_count": len(body),
                    }
                )

    if chapters:
        return {"chapters": chapters, "mode": "heading", "warning": None}

    if not normalized:
        return {"chapters": [], "mode": "empty", "warning": "未提供小说文本。"}

    chunks = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    size = max(1, len(chunks) // 3)
    grouped = ["\n\n".join(chunks[i : i + size]) for i in range(0, len(chunks), size)]
    fallback_chapters = [
        {
            "chapter_id": f"chapter_{index + 1:03d}",
            "order": index + 1,
            "title": f"自动拆分章节 {index + 1}",
            "text": body,
            "word_count": len(body),
        }
        for index, body in enumerate(grouped[:6])
    ]
    return {
        "chapters": fallback_chapters,
        "mode": "fallback",
        "warning": "未识别到标准章节标题，已按段落自动拆分；建议使用“第一章 标题 正文”或单独章节标题行。",
    }

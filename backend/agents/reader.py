import re


class ReaderAgent:
    """规则版 Reader，后续可替换为 AI 章节解析 Provider。"""

    name = "Reader Agent"

    def __init__(self, provider=None):
        self.provider = provider

    def run(self, text):
        parse_result = parse_chapters(text)
        return {
            "parse_result": parse_result,
            "trace": {
                "agent": self.name,
                "status": "success",
                "summary": f"识别到 {len(parse_result['chapters'])} 个章节，解析模式：{parse_result['mode']}",
            },
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

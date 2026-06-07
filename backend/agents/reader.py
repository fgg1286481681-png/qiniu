import re

from llm_client import LLMClient, get_env
from trace_utils import TraceTimer


class ReaderAgent:
    """Reader Agent: local chapter detection first, LLM only as low-confidence assist."""

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
        parse_result = parse_chapters(text)
        source = "rule"
        try:
            should_try_llm = (
                self.provider
                and (
                    parse_result.get("confidence", 0) < 0.45
                    or len(parse_result.get("chapters", [])) < 3
                )
            )
            if should_try_llm:
                provider_result = self.provider.parse(text)
                parse_result = provider_result["parse_result"]
                attempts = provider_result["attempts"]
                usage = provider_result["usage"]
                source = "llm"
        except Exception as exc:
            fallback_reason = str(exc)
            warnings = parse_result.setdefault("warnings", [])
            warnings.append(f"Reader AI 辅助识别失败，已保留本地识别结果：{fallback_reason}")
            parse_result["warning"] = "；".join(warnings) if warnings else parse_result.get("warning")

        return {
            "parse_result": parse_result,
            "trace": timer.finish(
                status="degraded" if fallback_reason else "success",
                source=source,
                summary=(
                    f"识别到 {len(parse_result['chapters'])} 个章节，"
                    f"处理 {parse_result.get('chunk_count', 1)} 个文本块，"
                    f"解析模式：{parse_result['mode']}，来源：{source}"
                ),
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
        self.chunk_chars = int(get_env("READER_CHUNK_CHARS", "12000"))

    @property
    def enabled(self):
        return self.client.enabled and bool(self.model)

    def parse(self, text):
        if not self.enabled:
            raise RuntimeError("Reader LLM 未配置")

        paragraphs = split_paragraphs(text)
        paragraph_chunks = chunk_paragraphs(paragraphs, self.chunk_chars)
        chapters = []
        total_attempts = 0
        usage_items = []
        paragraph_offset = 0

        for chunk_index, chunk in enumerate(paragraph_chunks, start=1):
            response = self.client.chat_json(
                model=self.model,
                system_prompt=READER_SYSTEM_PROMPT,
                user_prompt=build_reader_user_prompt(
                    chunk,
                    chunk_index=chunk_index,
                    chunk_count=len(paragraph_chunks),
                ),
                temperature=self.temperature,
                top_p=self.top_p,
                max_tokens=self.max_tokens,
            )
            normalized = normalize_llm_chapters(
                response["data"],
                chunk,
                paragraph_offset=paragraph_offset,
                chapter_offset=len(chapters),
            )
            chapters.extend(normalized)
            paragraph_offset += len(chunk)
            total_attempts += response["attempts"]
            if response["usage"]:
                usage_items.append(response["usage"])

        if len(chapters) < 3:
            raise ValueError("Reader LLM 拆分章节少于 3 个")

        parse_result = {
            "chapters": chapters,
            "mode": "llm_chunked" if len(paragraph_chunks) > 1 else "llm",
            "warning": None,
            "chunk_count": len(paragraph_chunks),
            "confidence": 0.9,
            "candidates": [],
            "warnings": [],
            "global_summary": merge_chapter_summaries(chapters),
        }
        return {
            "parse_result": parse_result,
            "usage": merge_usage(usage_items),
            "attempts": total_attempts,
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


def chunk_paragraphs(paragraphs, max_chars):
    normalized_paragraphs = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            normalized_paragraphs.append(paragraph)
            continue
        for start in range(0, len(paragraph), max_chars):
            normalized_paragraphs.append(paragraph[start : start + max_chars])

    chunks = []
    current = []
    current_chars = 0
    for paragraph in normalized_paragraphs:
        paragraph_chars = len(paragraph) + 2
        if current and current_chars + paragraph_chars > max_chars:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(paragraph)
        current_chars += paragraph_chars
    if current:
        chunks.append(current)
    return chunks


def build_reader_user_prompt(paragraphs, chunk_index=1, chunk_count=1):
    numbered_text = "\n\n".join(
        f"[段落 {index}]\n{paragraph}"
        for index, paragraph in enumerate(paragraphs, start=1)
    )
    return f"""请解析下面的小说文本块（第 {chunk_index}/{chunk_count} 块）。
要求：
1. 如果有章节标题，优先按原章节拆分。
2. 如果没有章节标题，按当前文本块的叙事阶段拆成 1 到 8 个章节。
3. 不要在 JSON 中重复原文，只返回 paragraph_start 和 paragraph_end。
4. 保留标题、摘要、关键事件、人物、地点。
5. 段落范围必须从 1 开始，覆盖到段落 {len(paragraphs)}，保持连续且不重叠。
6. 只输出 JSON。

小说文本：
{numbered_text}
"""


def normalize_llm_chapters(result, paragraphs, paragraph_offset=0, chapter_offset=0):
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
                "chapter_id": f"chapter_{chapter_offset + len(chapters) + 1:03d}",
                "order": chapter_offset + len(chapters) + 1,
                "title": str(
                    item.get("title") or f"AI 拆分章节 {chapter_offset + len(chapters) + 1}"
                ).strip(),
                "text": body,
                "word_count": len(body),
                "summary": str(item.get("summary") or body[:240]).strip(),
                "key_events": [str(value) for value in item.get("key_events") or []],
                "characters": [str(value) for value in item.get("characters") or []],
                "locations": [str(value) for value in item.get("locations") or []],
                "paragraph_start": paragraph_offset + start,
                "paragraph_end": paragraph_offset + end,
            }
        )

    if last_end != len(paragraphs):
        raise ValueError("Reader LLM 未覆盖全部原文段落")
    return chapters


def normalize_llm_parse_result(result, paragraphs):
    chapters = normalize_llm_chapters(result, paragraphs)
    if len(chapters) < 3:
        raise ValueError("Reader LLM 拆分章节少于 3 个")
    return {
        "chapters": chapters,
        "mode": result.get("mode") or "llm",
        "warning": result.get("warning"),
        "chunk_count": 1,
        "confidence": 0.9,
        "candidates": [],
        "warnings": [],
        "global_summary": merge_chapter_summaries(chapters),
    }


def merge_chapter_summaries(chapters, max_chars=3000):
    summaries = [
        f"{chapter['title']}：{chapter.get('summary') or chapter['text'][:240]}"
        for chapter in chapters
    ]
    return "\n".join(summaries)[:max_chars]


def merge_usage(items):
    if not items:
        return None
    merged = {}
    for item in items:
        for key, value in item.items():
            if isinstance(value, (int, float)):
                merged[key] = merged.get(key, 0) + value
    return merged or None


CHINESE_NUMBER_MAP = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
SPECIAL_CHAPTER_TITLES = {"序章", "楔子", "引子", "前言", "尾声", "后记"}
SOFT_SPECIAL_TITLES = {"番外"}
TERMINAL_PUNCTUATION = "。！？；?!;"
SECTION_TITLE_PATTERN = re.compile(
    r"^(?:第\s*[一二两三四五六七八九十百千万零〇\d]+\s*[部卷篇集]|[上下中前后终][部卷篇]|卷\s*[一二两三四五六七八九十百千万零〇\d]+)(?:\s+\S.*)?$"
)


def chinese_number_to_int(value):
    value = re.sub(r"\s+", "", str(value or ""))
    if not value:
        return None
    if value.isdigit():
        return int(value)
    if value in CHINESE_NUMBER_MAP:
        return CHINESE_NUMBER_MAP[value]
    if "十" in value:
        left, _, right = value.partition("十")
        tens = CHINESE_NUMBER_MAP.get(left, 1) if left else 1
        ones = CHINESE_NUMBER_MAP.get(right, 0) if right else 0
        return tens * 10 + ones
    total = 0
    for char in value:
        if char not in CHINESE_NUMBER_MAP:
            return None
        total = total * 10 + CHINESE_NUMBER_MAP[char]
    return total


def strip_heading_marks(line):
    text = re.sub(r"^\s*#{1,6}\s*", "", line.strip())
    text = re.sub(r"^[【\[\(（《<]\s*", "", text)
    text = re.sub(r"^\s*([^\]】）》>]+)[】\]\)）》>]\s*", r"\1 ", text)
    text = re.sub(r"\s*[】\]\)）》>]\s*$", "", text)
    return text.strip(" \t　")


def split_inline_body(rest):
    rest = rest.strip(" \t　:-—")
    if not rest:
        return "", ""
    punctuation = re.search(r"[。！？；?!;]", rest)
    if punctuation and punctuation.start() <= 30:
        return rest[: punctuation.start()].strip(), rest[punctuation.start() + 1 :].strip()
    return rest, ""


def score_candidate(line, kind, number, previous_blank, next_blank):
    score = 0.0
    if kind in {"standard", "chapter_word", "english"}:
        score += 0.62
    elif kind == "numbered":
        score += 0.42
    else:
        score += 0.5
    if number is not None:
        score += 0.12
    if len(line) <= 28:
        score += 0.14
    elif len(line) <= 45:
        score += 0.05
    else:
        score -= 0.35
    if previous_blank:
        score += 0.06
    if next_blank:
        score += 0.04
    if any(mark in line for mark in TERMINAL_PUNCTUATION):
        score -= 0.28
    if len(line) > 70:
        score -= 0.35
    return max(0.0, min(score, 1.0))


def looks_like_numeric_noise(line):
    text = line.strip()
    starts_numeric = bool(re.match(r"^\d", text))
    if ("%" in text or "％" in text) and starts_numeric:
        return "疑似百分比或统计行"
    if re.match(r"^\d+(?:\.\d+){1,3}$", text):
        return "疑似小数或日期"
    if re.match(r"^\d{3,4}[.．/-]\d{1,2}(?:[.．/-]\d{1,2})?(?:\D.*)?$", text):
        return "疑似日期"
    if re.match(r"^\d+[.．]\d+", text):
        return "疑似小数编号"
    return None


def build_rejected_candidate(line, line_index, offset, reason):
    return {
        "line_index": line_index,
        "offset": offset,
        "title": strip_heading_marks(line) or line.strip(),
        "number": None,
        "kind": "numeric_noise",
        "score": 0.0,
        "inline_body": "",
        "excluded": True,
        "reason": reason,
    }


def looks_like_section_heading(line):
    stripped = strip_heading_marks(line)
    if not stripped or len(stripped) > 28:
        return False
    if any(mark in stripped for mark in TERMINAL_PUNCTUATION):
        return False
    if SECTION_TITLE_PATTERN.match(stripped):
        return True
    if re.match(r"^[一二两三四五六七八九十百千万零〇\d]{1,4}[部卷篇集]\s*\S*$", stripped):
        return True
    return False


def build_candidate(line, line_index, offset, previous_blank, next_blank):
    stripped = strip_heading_marks(line)
    if not stripped:
        return None
    noise_reason = looks_like_numeric_noise(stripped)
    if noise_reason:
        return build_rejected_candidate(line, line_index, offset, noise_reason)

    patterns = [
        (
            "standard",
            re.compile(
                r"^(?:第\s*(?P<number>[一二两三四五六七八九十百千万零〇\d]+)\s*[章节回幕卷])(?P<rest>.*)$",
                re.I,
            ),
        ),
        (
            "chapter_word",
            re.compile(r"^(?:章节|章|节)\s*(?P<number>[一二两三四五六七八九十百千万零〇\d]+)(?P<rest>.*)$", re.I),
        ),
        (
            "english",
            re.compile(r"^(?:chapter|chap\.?)\s*(?P<number>\d+|one|two|three|four|five|six|seven|eight|nine|ten)(?P<rest>.*)$", re.I),
        ),
        (
            "numbered",
            re.compile(r"^(?P<number>\d{1,4}|[一二两三四五六七八九十]{1,3})(?:[、.．\)]|\s+)\s*(?P<rest>\S.*)?$"),
        ),
    ]

    kind = None
    number = None
    rest = ""
    for candidate_kind, pattern in patterns:
        match = pattern.match(stripped)
        if not match:
            continue
        kind = candidate_kind
        raw_number = match.group("number")
        english_numbers = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
        }
        number = english_numbers.get(str(raw_number).lower(), chinese_number_to_int(raw_number))
        rest = match.groupdict().get("rest") or ""
        if kind == "numbered":
            if number is not None and number > 300:
                return build_rejected_candidate(line, line_index, offset, "疑似年份或统计编号")
            if rest and len(rest.strip()) > 48:
                return build_rejected_candidate(line, line_index, offset, "数字编号后的标题过长，疑似正文或统计句")
        break

    special = stripped in SPECIAL_CHAPTER_TITLES or any(
        stripped.startswith(f"{title} ") for title in SPECIAL_CHAPTER_TITLES
    )
    soft_special = any(stripped.startswith(title) for title in SOFT_SPECIAL_TITLES)
    if kind is None and (special or soft_special):
        kind = "special" if special else "soft_special"
    if kind is None:
        return None

    title_tail, inline_body = split_inline_body(rest)
    title_prefix = stripped[: len(stripped) - len(rest)] if rest else stripped
    title = stripped if kind in {"special", "soft_special"} else re.sub(r"\s+", " ", f"{title_prefix}{title_tail}").strip()
    score = score_candidate(stripped, kind, number, previous_blank, next_blank)
    return {
        "line_index": line_index,
        "offset": offset,
        "title": title or stripped,
        "number": number,
        "kind": kind,
        "score": score,
        "inline_body": inline_body,
        "excluded": False,
        "reason": None,
    }


def detect_chapter_candidates(text):
    lines = text.splitlines(keepends=True)
    plain_lines = [line.rstrip("\r\n") for line in lines]
    candidates = []
    sections = []
    offset = 0
    for index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r\n")
        previous_blank = index == 0 or not plain_lines[index - 1].strip()
        next_blank = index + 1 >= len(plain_lines) or not plain_lines[index + 1].strip()
        candidate = build_candidate(line, index, offset, previous_blank, next_blank)
        if candidate:
            candidates.append(candidate)
        elif previous_blank and next_blank and looks_like_section_heading(line):
            sections.append(
                {
                    "line_index": index,
                    "offset": offset,
                    "title": strip_heading_marks(line),
                    "kind": "section",
                    "score": 0.7,
                    "excluded": True,
                    "reason": "识别为部/卷/篇层级标题",
                }
            )
        offset += len(raw_line)
    sections.extend(infer_plain_sections(plain_lines, candidates))
    attach_section_context(candidates, sections)
    return sorted([*candidates, *sections], key=lambda item: item["offset"])


def infer_plain_sections(lines, candidates):
    numbered_lines = {
        item["line_index"]
        for item in candidates
        if not item.get("excluded") and item.get("kind") == "numbered" and item.get("number") == 1
    }
    sections = []
    offset = 0
    line_offsets = []
    for line in lines:
        line_offsets.append(offset)
        offset += len(line) + 1

    for index, line in enumerate(lines):
        stripped = strip_heading_marks(line)
        previous_blank = index == 0 or not lines[index - 1].strip()
        next_blank = index + 1 >= len(lines) or not lines[index + 1].strip()
        if not (previous_blank and next_blank and stripped):
            continue
        if len(stripped) > 18 or any(mark in stripped for mark in TERMINAL_PUNCTUATION):
            continue
        if build_candidate(line, index, line_offsets[index], previous_blank, next_blank):
            continue
        lookahead = range(index + 1, min(len(lines), index + 8))
        if any(line_number in numbered_lines for line_number in lookahead):
            sections.append(
                {
                    "line_index": index,
                    "offset": line_offsets[index],
                    "title": stripped,
                    "kind": "section",
                    "score": 0.62,
                    "excluded": True,
                    "reason": "短标题后出现重新编号小节，识别为层级标题",
                }
            )
    return sections


def attach_section_context(candidates, sections):
    active_section = None
    section_items = sorted(sections, key=lambda item: item["offset"])
    section_index = 0
    for candidate in sorted(candidates, key=lambda item: item["offset"]):
        while section_index < len(section_items) and section_items[section_index]["offset"] < candidate["offset"]:
            active_section = section_items[section_index]["title"]
            section_index += 1
        if active_section and not candidate.get("excluded"):
            candidate["section"] = active_section


def select_chapter_boundaries(candidates, text_length):
    if not candidates:
        return [], 0.0, []

    warnings = []
    selected = [item for item in candidates if not item.get("excluded") and item["score"] >= 0.55]
    if len(selected) < 3:
        selected = [item for item in candidates if not item.get("excluded") and item["score"] >= 0.45]
    selected.sort(key=lambda item: item["offset"])

    filtered = []
    previous_number = None
    previous_section = None
    for candidate in selected:
        if filtered and candidate["offset"] - filtered[-1]["offset"] < 5:
            if candidate["score"] > filtered[-1]["score"]:
                filtered[-1] = candidate
            continue
        current_section = candidate.get("section")
        if current_section != previous_section:
            previous_number = None
            previous_section = current_section
        if previous_number is not None and candidate["number"] is not None:
            if candidate["number"] < previous_number:
                candidate = {**candidate, "score": max(0, candidate["score"] - 0.2)}
                warnings.append("章节编号出现回退，已降低相关标题置信度。")
            elif candidate["number"] > previous_number + 2:
                candidate = {**candidate, "score": max(0, candidate["score"] - 0.08)}
                warnings.append("章节编号存在跳跃，请检查是否漏识别章节。")
        if candidate["number"] is not None:
            previous_number = candidate["number"]
        filtered.append(candidate)

    if len(filtered) < 3:
        confidence = round(sum(item["score"] for item in filtered) / max(3, len(filtered)), 4)
        return [], confidence, ["识别到的章节标题少于 3 个，已改用智能切分。"]

    distances = [
        filtered[index + 1]["offset"] - filtered[index]["offset"]
        for index in range(len(filtered) - 1)
    ]
    if any(distance < 80 for distance in distances) and text_length > 1000:
        warnings.append("部分章节间距较短，可能存在误识别。")

    average_score = sum(item["score"] for item in filtered) / len(filtered)
    confidence = round(max(0.0, min(1.0, average_score + min(0.08, len(filtered) / 100))), 4)
    return filtered, confidence, warnings


def chapter_from_body(index, title, body):
    body = body.strip()
    return {
        "chapter_id": f"chapter_{index + 1:03d}",
        "order": index + 1,
        "title": title,
        "text": body,
        "word_count": len(body),
        "summary": body[:240],
        "key_events": [],
        "characters": [],
        "locations": [],
    }


def split_by_boundaries(text, boundaries):
    chapters = []
    for index, boundary in enumerate(boundaries):
        next_offset = boundaries[index + 1]["offset"] if index + 1 < len(boundaries) else len(text)
        line_end = text.find("\n", boundary["offset"], next_offset)
        if line_end == -1:
            line_end = min(next_offset, len(text))
        body_start = line_end + 1 if line_end < len(text) else line_end
        body = "\n".join(
            part
            for part in [boundary.get("inline_body", ""), text[body_start:next_offset].strip()]
            if part
        ).strip()
        if body:
            title = boundary["title"]
            if boundary.get("section") and not title.startswith(f"{boundary['section']} / "):
                title = f"{boundary['section']} / {title}"
            chapters.append(chapter_from_body(len(chapters), title, body))
    return chapters


def sanitize_candidates(candidates):
    return [
        {
            "line": item["line_index"] + 1,
            "title": item["title"],
            "kind": item["kind"],
            "score": round(item["score"], 4),
            "number": item.get("number"),
            "section": item.get("section"),
            "excluded": bool(item.get("excluded")),
            "reason": item.get("reason"),
        }
        for item in candidates[:30]
    ]


def smart_fallback_split(text):
    chunks = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(chunks) < 3:
        chunks = [part.strip() for part in text.splitlines() if part.strip()]
    if len(chunks) < 3 and text.strip():
        size = max(1, len(text) // 3)
        chunks = [text[index : index + size].strip() for index in range(0, len(text), size) if text[index : index + size].strip()]
    if not chunks:
        return []

    target_count = min(6, max(3, len(text) // 2500 + 1), len(chunks))
    target_chars = max(1, len(text) // target_count)
    groups = []
    current = []
    current_chars = 0
    for chunk_index, chunk in enumerate(chunks):
        current.append(chunk)
        current_chars += len(chunk)
        remaining_chunks = len(chunks) - chunk_index - 1
        remaining_groups = target_count - len(groups) - 1
        if (
            len(groups) < target_count - 1
            and current_chars >= target_chars
            and remaining_chunks >= remaining_groups
        ):
            groups.append("\n\n".join(current))
            current = []
            current_chars = 0
    if current:
        groups.append("\n\n".join(current))

    return [
        chapter_from_body(index, f"自动拆分章节 {index + 1}", body)
        for index, body in enumerate(groups[:6])
        if body.strip()
    ]


def parse_chapters(text):
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return {
            "chapters": [],
            "mode": "empty",
            "warning": "未提供小说文本。",
            "warnings": ["未提供小说文本。"],
            "confidence": 0.0,
            "candidates": [],
            "chunk_count": 0,
            "global_summary": "",
        }

    candidates = detect_chapter_candidates(normalized)
    boundaries, confidence, warnings = select_chapter_boundaries(candidates, len(normalized))
    if boundaries:
        chapters = split_by_boundaries(normalized, boundaries)
        if len(chapters) >= 3:
            mode = "heading" if all(
                item["kind"] in {"standard", "chapter_word", "english"}
                for item in boundaries
            ) else "soft_heading"
            return {
                "chapters": chapters,
                "mode": mode,
                "warning": "；".join(warnings) if warnings else None,
                "warnings": warnings,
                "confidence": confidence,
                "candidates": sanitize_candidates(candidates),
                "chunk_count": 1,
                "global_summary": merge_chapter_summaries(chapters),
            }

    fallback_warning = "未可靠识别到 3 个以上章节标题，已按段落和长度智能切分；建议检查章节标题格式。"
    fallback_chapters = smart_fallback_split(normalized)
    if 0 < len(fallback_chapters) < 3:
        size = max(1, len(normalized) // 3)
        fallback_chapters = [
            chapter_from_body(index, f"自动拆分章节 {index + 1}", body)
            for index, body in enumerate(
                normalized[start : start + size].strip()
                for start in range(0, len(normalized), size)
            )
            if body
        ][:3]
    return {
        "chapters": fallback_chapters,
        "mode": "smart_fallback",
        "warning": fallback_warning,
        "warnings": [fallback_warning, *warnings],
        "confidence": min(confidence, 0.45),
        "candidates": sanitize_candidates(candidates),
        "chunk_count": 1,
        "global_summary": merge_chapter_summaries(fallback_chapters),
    }

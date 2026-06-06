import json
import re

from llm_client import LLMClient, get_env
from trace_utils import TraceTimer


class PlannerAgent:
    """Planner Agent: AI planning first, rule-based fallback."""

    name = "Planner Agent"

    def __init__(self, provider=None):
        self.provider = provider or build_planner_provider()

    def run(self, chapters):
        if len(chapters) < 3:
            raise ValueError("至少需要 3 个章节以上的小说文本")

        timer = TraceTimer(
            self.name,
            "planner",
            getattr(self.provider, "model", None),
        )
        source = "rule"
        attempts = 0
        usage = None
        fallback_reason = None
        chunk_count = 1
        try:
            if self.provider:
                provider_result = self.provider.plan(chapters)
                plan = provider_result["plan"]
                attempts = provider_result["attempts"]
                usage = provider_result["usage"]
                chunk_count = provider_result.get("chunk_count", 1)
                source = "llm"
            else:
                plan = build_rule_plan(chapters)
        except Exception as exc:
            plan = build_rule_plan(chapters)
            fallback_reason = str(exc)
            plan["warning"] = f"Planner AI 调用失败，已回退规则规划：{fallback_reason}"

        return {
            "plan": plan,
            "trace": timer.finish(
                status="degraded" if fallback_reason else "success",
                source=source,
                summary=(
                    f"规划出 {len(plan['characters'])} 个人物、"
                    f"{len(plan['locations'])} 个地点、{len(plan['events'])} 个事件，"
                    f"处理 {chunk_count} 个章节批次，来源：{source}"
                ),
                attempts=attempts,
                fallback_reason=fallback_reason,
                usage=usage,
            ),
        }


class PlannerLLMProvider:
    def __init__(self, client=None, model=None):
        self.client = client or LLMClient()
        self.model = model or get_env("PLANNER_MODEL", "")
        self.temperature = float(get_env("PLANNER_TEMPERATURE", "0.3"))
        self.top_p = float(get_env("PLANNER_TOP_P", "0.95"))
        self.max_tokens = int(get_env("PLANNER_MAX_TOKENS", "8000"))
        self.chunk_chars = int(get_env("PLANNER_CHUNK_CHARS", "14000"))

    @property
    def enabled(self):
        return self.client.enabled and bool(self.model)

    def plan(self, chapters):
        if not self.enabled:
            raise RuntimeError("Planner LLM 未配置")

        chapter_batches = chunk_chapters(chapters, self.chunk_chars)
        raw_results = []
        usage_items = []
        total_attempts = 0
        for batch_index, batch in enumerate(chapter_batches, start=1):
            response = self.client.chat_json(
                model=self.model,
                system_prompt=PLANNER_SYSTEM_PROMPT,
                user_prompt=build_planner_prompt(
                    batch,
                    batch_index=batch_index,
                    batch_count=len(chapter_batches),
                ),
                temperature=self.temperature,
                top_p=self.top_p,
                max_tokens=self.max_tokens,
            )
            raw_results.append(response["data"])
            total_attempts += response["attempts"]
            if response["usage"]:
                usage_items.append(response["usage"])

        merged_result = merge_planner_results(raw_results)
        return {
            "plan": normalize_plan(merged_result, chapters),
            "usage": merge_usage(usage_items),
            "attempts": total_attempts,
            "chunk_count": len(chapter_batches),
        }


def build_planner_provider():
    provider = PlannerLLMProvider()
    return provider if provider.enabled else None


PLANNER_SYSTEM_PROMPT = """你是 Novel2Script 的 Planner Agent。
你负责根据 Reader 已拆分的小说章节，提取全局人物、地点、关键事件，并规划可供 Writer 使用的改编事实层。
只输出严格 JSON，不要 Markdown，不要解释，不得编造原文之外的主线事实。
顶层结构必须为：
{
  "characters": [
    {
      "name": "姓名",
      "aliases": [],
      "role": "protagonist|supporting|antagonist",
      "description": "人物简介",
      "goal": "人物目标"
    }
  ],
  "locations": [
    {"name": "地点", "description": "地点说明"}
  ],
  "events": [
    {
      "source_chapter": "chapter_001",
      "summary": "事件摘要",
      "conflict": "冲突",
      "emotional_shift": "情绪变化",
      "character_names": ["人物姓名"]
    }
  ]
}
人物名必须统一，事件必须引用输入中存在的 chapter_id。
"""


def chunk_chapters(chapters, max_chars):
    batches = []
    current = []
    current_chars = 0
    for chapter in chapters:
        chapter_chars = len(chapter.get("text", "")) + 200
        if current and current_chars + chapter_chars > max_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(chapter)
        current_chars += chapter_chars
    if current:
        batches.append(current)
    return batches


def build_planner_prompt(chapters, batch_index=1, batch_count=1):
    payload = [
        {
            "chapter_id": chapter["chapter_id"],
            "title": chapter["title"],
            "text": chapter["text"],
            "reader_summary": chapter.get("summary", ""),
        }
        for chapter in chapters
    ]
    return (
        f"这是第 {batch_index}/{batch_count} 个章节批次。"
        "请根据以下章节生成剧本改编事实层。每个章节至少提取一个关键事件；"
        "人物、地点应去重；不要写剧本对白。\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def merge_planner_results(results):
    merged = {"characters": [], "locations": [], "events": []}
    characters_by_name = {}
    locations_by_name = {}
    seen_events = set()

    for result in results:
        for item in result.get("characters") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            existing = characters_by_name.get(name)
            if existing is None:
                existing = dict(item)
                existing["aliases"] = list(item.get("aliases") or [])
                characters_by_name[name] = existing
                merged["characters"].append(existing)
            else:
                aliases = list(existing.get("aliases") or [])
                for alias in item.get("aliases") or []:
                    if alias not in aliases:
                        aliases.append(alias)
                existing["aliases"] = aliases
                for field in ["description", "goal"]:
                    if len(str(item.get(field) or "")) > len(str(existing.get(field) or "")):
                        existing[field] = item[field]

        for item in result.get("locations") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            if name not in locations_by_name:
                locations_by_name[name] = dict(item)
                merged["locations"].append(locations_by_name[name])

        for item in result.get("events") or []:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("source_chapter") or ""),
                str(item.get("summary") or ""),
            )
            if key not in seen_events:
                seen_events.add(key)
                merged["events"].append(dict(item))
    return merged


def merge_usage(items):
    if not items:
        return None
    merged = {}
    for item in items:
        for key, value in item.items():
            if isinstance(value, (int, float)):
                merged[key] = merged.get(key, 0) + value
    return merged or None


def normalize_plan(result, chapters):
    raw_characters = result.get("characters")
    raw_locations = result.get("locations")
    raw_events = result.get("events")
    if not all(isinstance(value, list) for value in [raw_characters, raw_locations, raw_events]):
        raise ValueError("Planner LLM 输出缺少 characters、locations 或 events 数组")

    characters = []
    name_to_id = {}
    for item in raw_characters:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in name_to_id:
            continue
        character_id = f"char_{len(characters) + 1:03d}"
        name_to_id[name] = character_id
        for alias in item.get("aliases") or []:
            name_to_id[str(alias).strip()] = character_id
        characters.append(
            {
                "id": character_id,
                "name": name,
                "aliases": [str(alias) for alias in item.get("aliases") or []],
                "role": str(item.get("role") or "supporting"),
                "description": str(item.get("description") or ""),
                "goal": str(item.get("goal") or ""),
            }
        )

    locations = []
    seen_locations = set()
    for item in raw_locations:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in seen_locations:
            continue
        seen_locations.add(name)
        locations.append(
            {
                "id": f"loc_{len(locations) + 1:03d}",
                "name": name,
                "description": str(item.get("description") or ""),
            }
        )

    chapter_ids = {chapter["chapter_id"] for chapter in chapters}
    events = []
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        source_chapter = str(item.get("source_chapter") or "").strip()
        if source_chapter not in chapter_ids:
            continue
        event_character_ids = []
        for name in item.get("character_names") or []:
            character_id = name_to_id.get(str(name).strip())
            if character_id and character_id not in event_character_ids:
                event_character_ids.append(character_id)
        events.append(
            {
                "id": f"event_{len(events) + 1:03d}",
                "source_chapter": source_chapter,
                "summary": str(item.get("summary") or ""),
                "conflict": str(item.get("conflict") or ""),
                "emotional_shift": str(item.get("emotional_shift") or ""),
                "characters": event_character_ids,
            }
        )

    if not characters or not locations or len(events) < len(chapters):
        raise ValueError("Planner LLM 输出内容不足")

    return {
        "chapters": chapters,
        "characters": characters,
        "locations": locations,
        "events": events,
    }


def build_rule_plan(chapters):
    characters = infer_characters(chapters)
    locations = infer_locations(chapters)
    events = build_events(chapters, characters)
    return {
        "chapters": chapters,
        "characters": characters,
        "locations": locations,
        "events": events,
    }


def split_sentences(text):
    return [item.strip() for item in re.split(r"(?<=[。！？!?])", text) if item.strip()]


def infer_characters(chapters):
    candidates = {}
    chapter_presence = {}
    contextual_names = set()
    ignored_words = {
        "第一", "第二", "第三", "这个", "那个", "他们", "我们", "时候", "声音",
        "会议", "办公室", "会议室", "街道", "学校", "医院", "车站", "房间",
        "大厅", "门口", "公司", "村庄", "教室", "问题", "方案", "事情",
        "人物", "情绪", "结果", "证据", "数据", "报告", "项目", "社区",
    }
    common_surnames = set(
        "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
        "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费"
        "廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于傅皮卞齐康伍余元卜顾孟平黄穆萧"
        "尹姚邵汪祁毛禹狄米贝明臧计伏成戴宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席"
        "季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管"
        "卢莫经房裘缪解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程嵇邢裴陆荣翁"
    )
    context_pattern = re.compile(
        r"(?:^|[，。！？；：“”\s])([\u4e00-\u9fa5]{2,4})"
        r"(?=说|问|回答|喊|解释|认为|决定|发现|走进|来到|赶到|推开|拿起|看见|抬头|坚持|担心|同意|承认|整理)"
    )

    def normalize_name(raw_name):
        surname_index = next(
            (index for index, char in enumerate(raw_name) if char in common_surnames),
            None,
        )
        if surname_index is None:
            return ""
        name = raw_name[surname_index : surname_index + 3]
        while len(name) > 2 and name[-1] in "的却也就才又便则并仍已正把被向在与和":
            name = name[:-1]
        return name

    for chapter in chapters:
        chapter_names = set()
        text = chapter["text"]
        for raw_name in context_pattern.findall(text):
            name = normalize_name(raw_name)
            if len(name) < 2 or name in ignored_words:
                continue
            candidates[name] = candidates.get(name, 0) + 4
            chapter_names.add(name)
            contextual_names.add(name)
        for index, char in enumerate(text[:-1]):
            if char not in common_surnames:
                continue
            for length in (2, 3):
                raw_name = text[index : index + length]
                if len(raw_name) != length or not raw_name.isalpha():
                    continue
                name = normalize_name(raw_name)
                if name in ignored_words or len(name) < 2:
                    continue
                count = text.count(name)
                if count < 2:
                    continue
                candidates[name] = candidates.get(name, 0) + count
                chapter_names.add(name)
        for name in chapter_names:
            chapter_presence[name] = chapter_presence.get(name, 0) + 1

    ranked = sorted(
        candidates,
        key=lambda name: (chapter_presence.get(name, 0), candidates[name], -len(name)),
        reverse=True,
    )
    sorted_names = []
    for name in ranked:
        if name[-1] in "时事物处地里中前后上下" and name not in contextual_names:
            continue
        if any(name.startswith(existing) or existing.startswith(name) for existing in sorted_names):
            continue
        sorted_names.append(name)
        if len(sorted_names) == 6:
            break
    if not sorted_names:
        sorted_names = ["主角", "对手", "旁观者"]

    roles = ["protagonist", "supporting", "antagonist", "supporting", "supporting", "supporting"]
    return [
        {
            "id": f"char_{index + 1:03d}",
            "name": name,
            "aliases": [],
            "role": roles[index] if index < len(roles) else "supporting",
            "description": f"{name} 是从原文中识别出的关键人物。",
            "goal": "推动当前情节发展。",
        }
        for index, name in enumerate(sorted_names)
    ]


def infer_locations(chapters):
    keywords = ["会议室", "办公室", "餐厅", "街道", "学校", "医院", "车站", "房间", "大厅", "门口", "公司", "村庄", "教室"]
    full_text = "\n".join(chapter["text"] for chapter in chapters)
    found = [word for word in keywords if word in full_text]
    if not found:
        found = ["主要场景", "转折场景"]

    return [
        {
            "id": f"loc_{index + 1:03d}",
            "name": name,
            "description": f"{name}，由小说文本中的叙事空间整理而来。",
        }
        for index, name in enumerate(found[:8])
    ]


def build_events(chapters, characters):
    events = []
    for chapter in chapters:
        sentences = split_sentences(chapter["text"])
        sample = sentences[:3] if sentences else [chapter["text"][:80]]
        events.append(
            {
                "id": f"event_{len(events) + 1:03d}",
                "source_chapter": chapter["chapter_id"],
                "summary": sample[0][:120],
                "conflict": (sample[1] if len(sample) > 1 else "人物关系出现变化")[:120],
                "emotional_shift": (sample[2] if len(sample) > 2 else "情绪从铺垫转向行动")[:120],
                "characters": [item["id"] for item in characters[: min(3, len(characters))]],
            }
        )
    return events

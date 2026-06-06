import json
import re

from llm_client import LLMClient, get_env


class PlannerAgent:
    """Planner Agent: AI planning first, rule-based fallback."""

    name = "Planner Agent"

    def __init__(self, provider=None):
        self.provider = provider or build_planner_provider()

    def run(self, chapters):
        if len(chapters) < 3:
            raise ValueError("至少需要 3 个章节以上的小说文本")

        source = "rule"
        try:
            if self.provider:
                plan = self.provider.plan(chapters)
                source = "llm"
            else:
                plan = build_rule_plan(chapters)
        except Exception as exc:
            plan = build_rule_plan(chapters)
            plan["warning"] = f"Planner AI 调用失败，已回退规则规划：{exc}"

        return {
            "plan": plan,
            "trace": {
                "agent": self.name,
                "status": "success",
                "summary": (
                    f"规划出 {len(plan['characters'])} 个人物、"
                    f"{len(plan['locations'])} 个地点、{len(plan['events'])} 个事件，来源：{source}"
                ),
            },
        }


class PlannerLLMProvider:
    def __init__(self, client=None, model=None):
        self.client = client or LLMClient()
        self.model = model or get_env("PLANNER_MODEL", "")
        self.temperature = float(get_env("PLANNER_TEMPERATURE", "0.3"))
        self.top_p = float(get_env("PLANNER_TOP_P", "0.95"))
        self.max_tokens = int(get_env("PLANNER_MAX_TOKENS", "8000"))

    @property
    def enabled(self):
        return self.client.enabled and bool(self.model)

    def plan(self, chapters):
        if not self.enabled:
            raise RuntimeError("Planner LLM 未配置")

        result = self.client.chat_json(
            model=self.model,
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=build_planner_prompt(chapters),
            temperature=self.temperature,
            top_p=self.top_p,
            max_tokens=self.max_tokens,
        )
        return normalize_plan(result, chapters)


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


def build_planner_prompt(chapters):
    payload = [
        {
            "chapter_id": chapter["chapter_id"],
            "title": chapter["title"],
            "text": chapter["text"],
        }
        for chapter in chapters
    ]
    return (
        "请根据以下章节生成剧本改编事实层。每个章节至少提取一个关键事件；"
        "人物、地点应去重；不要写剧本对白。\n"
        + json.dumps(payload, ensure_ascii=False)
    )


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
    ignored_prefixes = ("第一", "第二", "第三", "这个", "那个", "他们", "我们", "时候", "声音", "会议", "办公室")
    for chapter in chapters:
        for name in re.findall(r"[\u4e00-\u9fa5]{2,4}", chapter["text"]):
            if name.startswith(ignored_prefixes):
                continue
            candidates[name] = candidates.get(name, 0) + 1

    sorted_names = [name for name, _ in sorted(candidates.items(), key=lambda item: item[1], reverse=True)[:6]]
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


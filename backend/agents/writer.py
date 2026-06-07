import json
from copy import deepcopy
import time

import yaml

from cancellation import is_cancelled_exception
from llm_client import LLMClient, get_env
from trace_utils import TraceTimer


class WriterAgent:
    """Writer Agent: AI screenplay generation first, template fallback."""

    name = "Writer Agent"

    def __init__(self, provider=None):
        self.provider = provider or build_writer_provider()

    def run(self, plan, title="未命名小说"):
        timer = TraceTimer(
            self.name,
            "writer",
            getattr(self.provider, "model", None),
        )
        source = "rule"
        attempts = 0
        usage = None
        fallback_reason = None
        try:
            if self.provider:
                provider_result = self.provider.write(plan, title)
                script = provider_result["script"]
                attempts = provider_result["attempts"]
                usage = provider_result["usage"]
                source = "hybrid" if provider_result.get("batch_fallback_count") else "llm"
            else:
                script = build_rule_script(plan, title)
        except Exception as exc:
            if is_cancelled_exception(exc):
                raise
            fallback_reason = str(exc)
            script = build_rule_script(plan, title)
            script["adaptation_notes"]["next_steps"].append(
                f"Writer AI 调用失败，已回退规则生成：{fallback_reason}"
            )

        yaml_text = dump_script_yaml(script)
        return {
            "script": script,
            "yaml": yaml_text,
            "trace": timer.finish(
                status="degraded" if fallback_reason else "success",
                source=source,
                summary=f"生成 {len(script['scenes'])} 个场景并导出 YAML，来源：{source}",
                attempts=attempts,
                fallback_reason=fallback_reason,
                usage=usage,
            ),
        }

    def repair(self, script, plan, validation, round_number):
        timer = TraceTimer(
            self.name,
            "repair",
            getattr(self.provider, "model", None),
        )
        source = "rule"
        attempts = 0
        usage = None
        fallback_reason = None
        repaired = None

        try:
            if self.provider:
                provider_result = self.provider.repair(script, plan, validation)
                repaired = provider_result["script"]
                attempts = provider_result["attempts"]
                usage = provider_result["usage"]
                source = "llm"
        except Exception as exc:
            if is_cancelled_exception(exc):
                raise
            fallback_reason = str(exc)

        if repaired is None:
            repaired = repair_script_rules(script, plan)

        return {
            "script": repaired,
            "yaml": dump_script_yaml(repaired),
            "trace": timer.finish(
                status="repaired" if not fallback_reason else "degraded",
                source=source,
                summary=f"完成第 {round_number} 轮局部修复，来源：{source}",
                attempts=attempts,
                fallback_reason=fallback_reason,
                usage=usage,
            ),
        }


class WriterLLMProvider:
    def __init__(self, client=None, model=None):
        self.client = client or LLMClient()
        self.model = model or get_env("WRITER_MODEL", "")
        self.temperature = float(get_env("WRITER_TEMPERATURE", "0.65"))
        self.top_p = float(get_env("WRITER_TOP_P", "0.9"))
        self.max_tokens = int(get_env("WRITER_MAX_TOKENS", "8000"))
        self.presence_penalty = float(get_env("WRITER_PRESENCE_PENALTY", "0.2"))
        self.frequency_penalty = float(get_env("WRITER_FREQUENCY_PENALTY", "0.3"))
        self.input_chars = int(get_env("WRITER_INPUT_CHARS", "18000"))
        self.events_per_call = int(get_env("WRITER_EVENTS_PER_CALL", "1"))

    @property
    def enabled(self):
        return self.client.enabled and bool(self.model)

    def write(self, plan, title):
        if not self.enabled:
            raise RuntimeError("Writer LLM 未配置")

        scenes = []
        retained = []
        changed = []
        next_steps = []
        usage_items = []
        total_attempts = 0
        batch_fallback_count = 0

        event_batches = chunk_events(plan["events"], self.events_per_call)
        for batch_index, event_batch in enumerate(event_batches, start=1):
            batch_plan = {
                **plan,
                "chapters": chapters_for_events(plan["chapters"], event_batch),
                "events": event_batch,
            }
            try:
                response = self.client.chat_json(
                    model=self.model,
                    system_prompt=WRITER_COMPACT_SYSTEM_PROMPT,
                    user_prompt=build_writer_prompt(
                        batch_plan,
                        title,
                        self.input_chars,
                        batch_index=batch_index,
                        batch_count=len(event_batches),
                    ),
                    temperature=min(self.temperature, 0.1),
                    top_p=min(self.top_p, 0.8),
                    max_tokens=min(self.max_tokens, 1200),
                    response_format_json=True,
                )
                partial_script = normalize_script(response["data"], plan, title)
                scenes.extend(partial_script["scenes"])
                notes = partial_script.get("adaptation_notes") or {}
                retained.extend(notes.get("retained") or [])
                changed.extend(notes.get("changed") or [])
                next_steps.extend(notes.get("next_steps") or [])
                total_attempts += response["attempts"]
                if response["usage"]:
                    usage_items.append(response["usage"])
            except Exception as exc:
                if is_cancelled_exception(exc):
                    raise
                batch_fallback_count += 1
                scenes.extend(
                    build_scenes(
                        plan["chapters"],
                        plan["characters"],
                        plan["locations"],
                        event_batch,
                    )
                )
                next_steps.append(
                    f"Writer 第 {batch_index} 批 LLM 生成失败，已用规则补齐：{exc}"
                )

        script = build_script(
            title,
            plan["chapters"],
            plan["characters"],
            plan["locations"],
            plan["events"],
            renumber_scenes(scenes),
            {
                "retained": retained,
                "changed": changed,
                "next_steps": next_steps,
            },
        )
        return {
            "script": script,
            "usage": merge_usage(usage_items),
            "attempts": total_attempts,
            "batch_fallback_count": batch_fallback_count,
        }

    def repair(self, script, plan, validation):
        if not self.enabled:
            raise RuntimeError("Writer LLM 未配置")

        response = self.client.chat_json(
            model=self.model,
            system_prompt=WRITER_REPAIR_SYSTEM_PROMPT,
            user_prompt=json.dumps(
                {
                    "script": script,
                    "planner_facts": {
                        "characters": plan["characters"],
                        "locations": plan["locations"],
                        "events": plan["events"],
                    },
                    "validation": validation,
                },
                ensure_ascii=False,
            ),
            temperature=0.2,
            top_p=0.9,
            max_tokens=self.max_tokens,
            response_format_json=True,
        )
        repaired = normalize_script(
            response["data"],
            plan,
            script.get("metadata", {}).get("title", "未命名小说"),
        )
        return {
            "script": repaired,
            "usage": response["usage"],
            "attempts": response["attempts"],
        }


def build_writer_provider():
    provider = WriterLLMProvider()
    return provider if provider.enabled else None


WRITER_COMPACT_SYSTEM_PROMPT = """你是 Novel2Script 的 Writer Agent。
只输出严格 JSON，不要 Markdown，不要解释。
输出对象只能包含 scenes 和 adaptation_notes。
每个 scene 必须引用输入中已有的 chapter_id、character_id、location_id、event_id。
每个 scene 至少 3 个 elements，类型只能是 action、dialogue、narration、transition、sound、shot。
dialogue 必须带 character_id。"""


WRITER_SYSTEM_PROMPT = """你是 Novel2Script 的 Writer Agent。
请把 Planner 的结构化事实改编为可表演的中文短剧剧本。
只输出严格 JSON，不要 Markdown，不要解释。
不得新增改变故事主线的新事实。对白、动作和旁白必须服务于 Planner 的事件与冲突。

输出顶层必须包含：
{
  "scenes": [
    {
      "id": "scene_001",
      "source_chapters": ["chapter_001"],
      "heading": {"location_id": "loc_001", "time_of_day": "morning"},
      "purpose": "场景目的",
      "conflict": "场景冲突",
      "characters": ["char_001"],
      "elements": [
        {"type": "action", "text": "动作"},
        {"type": "dialogue", "character_id": "char_001", "text": "对白"},
        {"type": "narration", "text": "旁白"}
      ]
    }
  ],
  "adaptation_notes": {
    "retained": ["保留内容"],
    "changed": ["改写内容"],
    "next_steps": ["后续建议"]
  }
}

scene element type 只能是 action、dialogue、narration、transition、sound、shot。
每个场景至少 3 个 elements；dialogue 必须引用存在的 character_id；地点必须引用存在的 location_id。
每个 Planner 事件至少应在一个场景元素中通过 event_id 标记，便于验证事件覆盖率。
"""


WRITER_REPAIR_SYSTEM_PROMPT = """你是 Novel2Script 的 Writer Repair Agent。
输入包含当前剧本、Planner 事实层和 Validator 问题。
只修改 Validator 指出的场景及为修复引用错误所必需的字段，不得改变主线事实。
保持未涉及场景的内容不变。所有 ID 必须引用 Planner 事实层已有对象。
每个场景至少包含 3 个可拍摄元素。
只输出严格 JSON，结构与 Writer Agent 的 scenes 和 adaptation_notes 输出一致。
"""


def build_writer_prompt(
    plan,
    title,
    max_input_chars=18000,
    batch_index=1,
    batch_count=1,
):
    chapter_excerpts = []
    chapter_count = max(1, len(plan["chapters"]))
    excerpt_chars = max(160, min(900, max_input_chars // chapter_count))
    for chapter in plan["chapters"]:
        chapter_excerpts.append(
            {
                "id": chapter["chapter_id"],
                "title": chapter["title"],
                "summary": chapter.get("summary", "")[:360],
                "excerpt": chapter["text"][:excerpt_chars],
            }
        )

    payload = {
        "title": title,
        "chapters": chapter_excerpts,
        "characters": plan["characters"],
        "locations": plan["locations"],
        "events": plan["events"],
    }
    return (
        f"这是第 {batch_index}/{batch_count} 批 Writer 任务。"
        "请按 events 顺序生成剧本初稿：每个 event 生成 1 个 scene；"
        "每个 scene 保持简洁，包含 3 到 5 个 elements；"
        "elements 中至少一个必须带对应 event_id；"
        "对白要符合人物身份，动作必须可拍摄，避免直接复制长段小说叙述。\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def chunk_events(events, size):
    size = max(1, size)
    return [events[index : index + size] for index in range(0, len(events), size)] or [[]]


def chapters_for_events(chapters, events):
    wanted = {event.get("source_chapter") for event in events}
    selected = [chapter for chapter in chapters if chapter.get("chapter_id") in wanted]
    return selected or chapters[:1]


def renumber_scenes(scenes):
    renumbered = []
    for index, scene in enumerate(scenes, start=1):
        item = deepcopy(scene)
        item["id"] = f"scene_{index:03d}"
        renumbered.append(item)
    return renumbered


def merge_usage(usage_items):
    if not usage_items:
        return None
    merged = {}
    for usage in usage_items:
        for key, value in usage.items():
            if isinstance(value, (int, float)):
                merged[key] = merged.get(key, 0) + value
            elif isinstance(value, dict):
                nested = merged.setdefault(key, {})
                for nested_key, nested_value in value.items():
                    if isinstance(nested_value, (int, float)):
                        nested[nested_key] = nested.get(nested_key, 0) + nested_value
    return merged


def normalize_script(result, plan, title):
    scenes = result.get("scenes")
    notes = result.get("adaptation_notes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("Writer LLM 输出缺少 scenes")
    if isinstance(notes, list):
        notes = {"changed": notes}
    if not isinstance(notes, dict):
        notes = {}

    character_ids = {item["id"] for item in plan["characters"]}
    location_ids = {item["id"] for item in plan["locations"]}
    chapter_ids = {item["chapter_id"] for item in plan["chapters"]}
    normalized_scenes = []

    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        heading = scene.get("heading") if isinstance(scene.get("heading"), dict) else {}
        location_id = heading.get("location_id") or scene.get("location_id")
        if location_id not in location_ids:
            location_id = plan["locations"][index % len(plan["locations"])]["id"]

        raw_source_chapters = scene.get("source_chapters", [])
        if isinstance(raw_source_chapters, str):
            raw_source_chapters = [raw_source_chapters]
        if scene.get("chapter_id"):
            raw_source_chapters = [*raw_source_chapters, scene.get("chapter_id")]
        source_chapters = [item for item in raw_source_chapters if item in chapter_ids]
        if not source_chapters:
            source_chapters = [plan["chapters"][index % len(plan["chapters"])]["chapter_id"]]

        raw_scene_characters = scene.get("characters", scene.get("character_ids", []))
        if isinstance(raw_scene_characters, str):
            raw_scene_characters = [raw_scene_characters]
        scene_characters = [item for item in raw_scene_characters if item in character_ids]
        if not scene_characters:
            scene_characters = [plan["characters"][0]["id"]]

        elements = []
        scene_event_id = scene.get("event_id")
        for element in scene.get("elements", []):
            if not isinstance(element, dict):
                continue
            element_type = element.get("type")
            if element_type not in {"action", "dialogue", "narration", "transition", "sound", "shot"}:
                continue
            normalized = {
                "type": element_type,
                "text": str(element.get("text") or element.get("content") or "").strip(),
            }
            if not normalized["text"]:
                continue
            if element_type == "dialogue":
                character_id = element.get("character_id")
                normalized["character_id"] = (
                    character_id if character_id in character_ids else scene_characters[0]
                )
            event_id = element.get("event_id") or scene_event_id
            if event_id:
                normalized["event_id"] = event_id
            elements.append(normalized)

        while len(elements) < 3:
            event_id = scene_event_id
            fallback_event = next(
                (item for item in plan["events"] if item.get("id") == event_id),
                plan["events"][index % len(plan["events"])],
            )
            if len(elements) == 0:
                elements.append(
                    {
                        "type": "action",
                        "text": fallback_event.get("summary") or "人物进入当前场景并推动事件发展。",
                        "event_id": fallback_event["id"],
                    }
                )
            elif len(elements) == 1:
                elements.append(
                    {
                        "type": "dialogue",
                        "character_id": scene_characters[0],
                        "text": fallback_event.get("conflict") or "我们必须现在做出决定。",
                        "event_id": fallback_event["id"],
                    }
                )
            else:
                elements.append(
                    {
                        "type": "narration",
                        "text": fallback_event.get("emotional_shift") or "局面因这个选择发生变化。",
                        "event_id": fallback_event["id"],
                    }
                )

        normalized_scenes.append(
            {
                "id": f"scene_{len(normalized_scenes) + 1:03d}",
                "source_chapters": source_chapters,
                "heading": {
                    "location_id": location_id,
                    "time_of_day": str(heading.get("time_of_day") or "day"),
                },
                "purpose": str(scene.get("purpose") or "推动剧情发展"),
                "conflict": str(scene.get("conflict") or "人物目标发生冲突"),
                "characters": scene_characters,
                "elements": elements,
            }
        )

    if not normalized_scenes:
        raise ValueError("Writer LLM 未生成有效场景")

    return build_script(
        title,
        plan["chapters"],
        plan["characters"],
        plan["locations"],
        plan["events"],
        normalized_scenes,
        notes,
    )


def build_rule_script(plan, title):
    scenes = build_scenes(
        plan["chapters"],
        plan["characters"],
        plan["locations"],
        plan["events"],
    )
    return build_script(
        title,
        plan["chapters"],
        plan["characters"],
        plan["locations"],
        plan["events"],
        scenes,
    )


def repair_script_rules(script, plan):
    repaired = deepcopy(script)
    character_ids = {item["id"] for item in plan["characters"]}
    location_ids = {item["id"] for item in plan["locations"]}
    chapter_ids = {item["chapter_id"] for item in plan["chapters"]}
    event_ids = {item["id"] for item in plan["events"]}
    default_character = plan["characters"][0]["id"]
    default_location = plan["locations"][0]["id"]
    default_chapter = plan["chapters"][0]["chapter_id"]

    for scene in repaired.get("scenes", []):
        heading = scene.setdefault("heading", {})
        if heading.get("location_id") not in location_ids:
            heading["location_id"] = default_location
        if heading.get("time_of_day") not in {"dawn", "morning", "day", "afternoon", "evening", "night"}:
            heading["time_of_day"] = "day"

        scene["source_chapters"] = [
            item for item in scene.get("source_chapters", []) if item in chapter_ids
        ] or [default_chapter]
        scene["characters"] = [
            item for item in scene.get("characters", []) if item in character_ids
        ] or [default_character]

        elements = []
        for element in scene.get("elements", []):
            if not isinstance(element, dict) or not str(element.get("text") or "").strip():
                continue
            item = deepcopy(element)
            if item.get("type") == "dialogue" and item.get("character_id") not in character_ids:
                item["character_id"] = scene["characters"][0]
            if (
                item.get("type") == "dialogue"
                and item.get("character_id") not in scene["characters"]
            ):
                scene["characters"].append(item["character_id"])
            if item.get("event_id") not in event_ids:
                item.pop("event_id", None)
            elements.append(item)
        while len(elements) < 3:
            elements.append({"type": "action", "text": "人物根据当前冲突继续推进场景行动。"})
        scene["elements"] = elements
    return repaired


def build_scenes(chapters, characters, locations, events):
    character_by_id = {item["id"]: item for item in characters}
    scenes = []
    for index, event in enumerate(events):
        chapter = next(
            (item for item in chapters if item["chapter_id"] == event["source_chapter"]),
            chapters[index % len(chapters)],
        )
        location = locations[index % len(locations)]
        scene_characters = event["characters"] or [characters[0]["id"]]
        protagonist = scene_characters[0]
        opponent = scene_characters[1] if len(scene_characters) > 1 else protagonist
        protagonist_info = character_by_id.get(protagonist, {})
        opponent_info = character_by_id.get(opponent, {})
        scenes.append(
            {
                "id": f"scene_{index + 1:03d}",
                "source_chapters": [chapter["chapter_id"]],
                "heading": {
                    "location_id": location["id"],
                    "time_of_day": ["morning", "afternoon", "night"][index % 3],
                },
                "purpose": f"呈现《{chapter['title']}》中的核心转折",
                "conflict": event["conflict"],
                "characters": scene_characters,
                "elements": [
                    {
                        "type": "action",
                        "text": build_rule_action(location["name"], event, protagonist_info),
                        "event_id": event["id"],
                    },
                    {
                        "type": "dialogue",
                        "character_id": protagonist,
                        "text": build_rule_dialogue(protagonist_info, event, leading=True),
                    },
                    {
                        "type": "dialogue",
                        "character_id": opponent,
                        "text": build_rule_dialogue(opponent_info, event, leading=False),
                    },
                    {"type": "narration", "text": event["emotional_shift"]},
                ],
            }
        )
    return scenes


def compact_text(value, limit=42):
    text = str(value or "").strip().rstrip("。！？!?；;")
    return text[:limit] if text else ""


def build_rule_action(location_name, event, character):
    name = character.get("name") or "人物"
    summary = compact_text(event.get("summary"), 48) or "眼前的事件"
    return f"{location_name}里，{name}围绕“{summary}”采取行动，现场关系随之发生变化。"


def build_rule_dialogue(character, event, leading):
    name = character.get("name") or "我"
    goal = compact_text(character.get("goal"), 30)
    summary = compact_text(event.get("summary"), 34)
    conflict = compact_text(event.get("conflict"), 34)
    if leading:
        if goal:
            return f"我是{name}。为了{goal}，关于{summary or '这件事'}，我需要现在作出决定。"
        return f"关于{summary or '眼前的问题'}，我不能只等别人给出答案。"
    if conflict:
        return f"先解决“{conflict}”，否则你的决定只会让局面更复杂。"
    return f"我理解你的选择，但我们还需要确认它会给所有人带来什么结果。"


def build_script(title, chapters, characters, locations, events, scenes, notes=None):
    notes = notes or {}
    return {
        "schema_version": "1.0",
        "metadata": {
            "title": title or "未命名小说",
            "source_type": "novel",
            "language": "zh-CN",
            "adaptation_type": "short_drama",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "chapters": [
            {
                "id": item["chapter_id"],
                "title": item["title"],
                "order": item["order"],
                "word_count": item["word_count"],
            }
            for item in chapters
        ],
        "characters": characters,
        "locations": locations,
        "events": events,
        "scenes": scenes,
        "adaptation_notes": {
            "retained": [str(item) for item in notes.get("retained") or ["保留原文主要人物和核心冲突。"]],
            "changed": [str(item) for item in notes.get("changed") or ["将小说叙述改写为动作、对白和旁白。"]],
            "next_steps": [str(item) for item in notes.get("next_steps") or ["建议人工检查人物口吻和场景节奏。"]],
        },
    }


def dump_script_yaml(script):
    return yaml.safe_dump(script, allow_unicode=True, sort_keys=False, indent=2)

import json
import time

import yaml

from llm_client import LLMClient, get_env


class WriterAgent:
    """Writer Agent: AI screenplay generation first, template fallback."""

    name = "Writer Agent"

    def __init__(self, provider=None):
        self.provider = provider or build_writer_provider()

    def run(self, plan, title="未命名小说"):
        source = "rule"
        try:
            if self.provider:
                script = self.provider.write(plan, title)
                source = "llm"
            else:
                script = build_rule_script(plan, title)
        except Exception as exc:
            script = build_rule_script(plan, title)
            script["adaptation_notes"]["next_steps"].append(f"Writer AI 调用失败，已回退规则生成：{exc}")

        yaml_text = dump_script_yaml(script)
        return {
            "script": script,
            "yaml": yaml_text,
            "trace": {
                "agent": self.name,
                "status": "success",
                "summary": f"生成 {len(script['scenes'])} 个场景并导出 YAML，来源：{source}",
            },
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

    @property
    def enabled(self):
        return self.client.enabled and bool(self.model)

    def write(self, plan, title):
        if not self.enabled:
            raise RuntimeError("Writer LLM 未配置")

        result = self.client.chat_json(
            model=self.model,
            system_prompt=WRITER_SYSTEM_PROMPT,
            user_prompt=build_writer_prompt(plan, title),
            temperature=self.temperature,
            top_p=self.top_p,
            max_tokens=self.max_tokens,
            presence_penalty=self.presence_penalty,
            frequency_penalty=self.frequency_penalty,
        )
        return normalize_script(result, plan, title)


def build_writer_provider():
    provider = WriterLLMProvider()
    return provider if provider.enabled else None


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
"""


def build_writer_prompt(plan, title):
    chapter_excerpts = []
    for chapter in plan["chapters"]:
        chapter_excerpts.append(
            {
                "id": chapter["chapter_id"],
                "title": chapter["title"],
                "excerpt": chapter["text"][:1800],
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
        "请生成 6 到 15 个场景；优先覆盖全部事件；对白要符合人物身份，"
        "动作必须可拍摄，避免直接复制长段小说叙述。\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def normalize_script(result, plan, title):
    scenes = result.get("scenes")
    notes = result.get("adaptation_notes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("Writer LLM 输出缺少 scenes")
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
        location_id = heading.get("location_id")
        if location_id not in location_ids:
            location_id = plan["locations"][index % len(plan["locations"])]["id"]

        source_chapters = [
            item for item in scene.get("source_chapters", []) if item in chapter_ids
        ]
        if not source_chapters:
            source_chapters = [plan["chapters"][index % len(plan["chapters"])]["chapter_id"]]

        scene_characters = [
            item for item in scene.get("characters", []) if item in character_ids
        ]
        if not scene_characters:
            scene_characters = [plan["characters"][0]["id"]]

        elements = []
        for element in scene.get("elements", []):
            if not isinstance(element, dict):
                continue
            element_type = element.get("type")
            if element_type not in {"action", "dialogue", "narration", "transition", "sound", "shot"}:
                continue
            normalized = {
                "type": element_type,
                "text": str(element.get("text") or "").strip(),
            }
            if not normalized["text"]:
                continue
            if element_type == "dialogue":
                character_id = element.get("character_id")
                normalized["character_id"] = (
                    character_id if character_id in character_ids else scene_characters[0]
                )
            if element.get("event_id"):
                normalized["event_id"] = element["event_id"]
            elements.append(normalized)

        if len(elements) < 3:
            raise ValueError(f"Writer LLM 场景 {index + 1} 的剧本元素不足")

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


def build_scenes(chapters, characters, locations, events):
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
                    {"type": "action", "text": f"{location['name']}里，人物围绕新的矛盾展开行动。"},
                    {"type": "dialogue", "character_id": protagonist, "text": "这件事不能再拖下去了。"},
                    {"type": "dialogue", "character_id": opponent, "text": "你确定自己承担得起后果吗？"},
                    {"type": "narration", "text": event["emotional_shift"]},
                ],
            }
        )
    return scenes


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


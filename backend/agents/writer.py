import time

import yaml


class WriterAgent:
    """规则版 Writer，后续可替换为 AI 剧本写作 Provider。"""

    name = "Writer Agent"

    def __init__(self, provider=None):
        self.provider = provider

    def run(self, plan, title="未命名小说"):
        chapters = plan["chapters"]
        characters = plan["characters"]
        locations = plan["locations"]
        events = plan["events"]
        scenes = build_scenes(chapters, characters, locations, events)
        script = build_script(title, chapters, characters, locations, events, scenes)
        yaml_text = dump_script_yaml(script)
        return {
            "script": script,
            "yaml": yaml_text,
            "trace": {
                "agent": self.name,
                "status": "success",
                "summary": f"生成 {len(scenes)} 个场景并导出 YAML",
            },
        }


def build_scenes(chapters, characters, locations, events):
    scenes = []
    for index, event in enumerate(events):
        chapter = chapters[index]
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
                    {
                        "type": "action",
                        "text": f"{location['name']}里，人物围绕新的矛盾展开行动。",
                    },
                    {
                        "type": "dialogue",
                        "character_id": protagonist,
                        "text": "这件事不能再拖下去了。",
                    },
                    {
                        "type": "dialogue",
                        "character_id": opponent,
                        "text": "你确定自己承担得起后果吗？",
                    },
                    {
                        "type": "narration",
                        "text": event["emotional_shift"],
                    },
                ],
            }
        )
    return scenes


def build_script(title, chapters, characters, locations, events, scenes):
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
            "retained": ["保留原文主要人物、章节顺序和核心冲突。"],
            "changed": ["将心理描写压缩为可表演的动作、对白和旁白。"],
            "next_steps": ["建议人工检查人物口吻，并补充更具体的场景调度。"],
        },
    }


def dump_script_yaml(script):
    return yaml.safe_dump(script, allow_unicode=True, sort_keys=False, indent=2)

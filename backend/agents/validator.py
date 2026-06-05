import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator


BASE_DIR = Path(__file__).resolve().parents[1]
SCHEMA_PATH = BASE_DIR / "schema.json"


class ValidatorAgent:
    """规则版 Validator，后续可替换为 AI 质量审阅 Provider。"""

    name = "Validator Agent"

    def __init__(self, provider=None):
        self.provider = provider

    def run(self, script):
        validation = validate_script(script)
        status = "success" if validation["valid"] else "warning"
        summary = "Schema 和引用校验通过" if validation["valid"] else f"发现 {len(validation['errors'])} 个校验问题"
        return {
            "validation": validation,
            "trace": {
                "agent": self.name,
                "status": status,
                "summary": summary,
            },
        }


def load_schema():
    with SCHEMA_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


SCRIPT_SCHEMA = load_schema()
SCHEMA_VALIDATOR = Draft202012Validator(SCRIPT_SCHEMA)


def parse_yaml_script(yaml_text):
    try:
        script = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败：{exc}") from exc

    if not isinstance(script, dict):
        raise ValueError("YAML 顶层必须是对象")
    return script


def path_to_string(parts):
    path = ""
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path = f"{path}.{part}" if path else str(part)
    return path or "$"


def validation_error_to_tip(error):
    if error.validator == "required":
        missing = error.message.split("'")[1] if "'" in error.message else ", ".join(error.validator_value)
        return f"补充必填字段：{missing}"
    if error.validator == "type":
        return f"调整为 {error.validator_value} 类型"
    if error.validator == "enum":
        return f"改为允许值之一：{', '.join(map(str, error.validator_value))}"
    if error.validator == "minItems":
        return f"至少保留 {error.validator_value} 项"
    return "按 schema.json 调整该字段结构"


def schema_errors(script):
    errors = []
    for error in sorted(SCHEMA_VALIDATOR.iter_errors(script), key=lambda item: list(item.path)):
        errors.append(
            {
                "path": path_to_string(error.path),
                "message": error.message,
                "suggestion": validation_error_to_tip(error),
            }
        )
    return errors


def business_errors(script):
    errors = []
    character_ids = {item.get("id") for item in script.get("characters", []) if isinstance(item, dict)}
    location_ids = {item.get("id") for item in script.get("locations", []) if isinstance(item, dict)}
    chapter_ids = {item.get("id") for item in script.get("chapters", []) if isinstance(item, dict)}
    event_ids = {item.get("id") for item in script.get("events", []) if isinstance(item, dict)}

    for event_index, event in enumerate(script.get("events", [])):
        source_chapter = event.get("source_chapter")
        if source_chapter not in chapter_ids:
            errors.append(
                {
                    "path": f"events[{event_index}].source_chapter",
                    "message": "引用了不存在的章节 ID",
                    "suggestion": "改为 chapters 中已有的 id，或补充对应章节。",
                }
            )
        for character_index, character_id in enumerate(event.get("characters", [])):
            if character_id not in character_ids:
                errors.append(
                    {
                        "path": f"events[{event_index}].characters[{character_index}]",
                        "message": "事件引用了不存在的人物 ID",
                        "suggestion": "改为 characters 中已有的 id，或补充对应人物。",
                    }
                )

    for scene_index, scene in enumerate(script.get("scenes", [])):
        location_id = scene.get("heading", {}).get("location_id")
        if location_id not in location_ids:
            errors.append(
                {
                    "path": f"scenes[{scene_index}].heading.location_id",
                    "message": "引用了不存在的地点 ID",
                    "suggestion": "改为 locations 中已有的 id，或补充对应地点。",
                }
            )
        for chapter_index, chapter_id in enumerate(scene.get("source_chapters", [])):
            if chapter_id not in chapter_ids:
                errors.append(
                    {
                        "path": f"scenes[{scene_index}].source_chapters[{chapter_index}]",
                        "message": "场景引用了不存在的章节 ID",
                        "suggestion": "改为 chapters 中已有的 id。",
                    }
                )
        for character_index, character_id in enumerate(scene.get("characters", [])):
            if character_id not in character_ids:
                errors.append(
                    {
                        "path": f"scenes[{scene_index}].characters[{character_index}]",
                        "message": "场景引用了不存在的人物 ID",
                        "suggestion": "改为 characters 中已有的 id。",
                    }
                )
        for element_index, element in enumerate(scene.get("elements", [])):
            if element.get("type") == "dialogue" and element.get("character_id") not in character_ids:
                errors.append(
                    {
                        "path": f"scenes[{scene_index}].elements[{element_index}].character_id",
                        "message": "对白引用了不存在的人物 ID",
                        "suggestion": "改为 characters 中已有的 id，或补充对应人物。",
                    }
                )
            if element.get("event_id") and element.get("event_id") not in event_ids:
                errors.append(
                    {
                        "path": f"scenes[{scene_index}].elements[{element_index}].event_id",
                        "message": "剧本元素引用了不存在的事件 ID",
                        "suggestion": "改为 events 中已有的 id，或移除 event_id。",
                    }
                )
    return errors


def validate_script(script):
    errors = schema_errors(script)
    if not errors:
        errors.extend(business_errors(script))
    return {"valid": len(errors) == 0, "errors": errors, "script": script if len(errors) == 0 else None}

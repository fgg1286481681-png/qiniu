from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import json
import re
import time
import uuid

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


HOST = "127.0.0.1"
PORT = 8000
PROJECTS = {}
BASE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = BASE_DIR / "schema.json"


def load_schema():
    with SCHEMA_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


SCRIPT_SCHEMA = load_schema()
SCHEMA_VALIDATOR = Draft202012Validator(SCRIPT_SCHEMA)


def json_response(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw)


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
        return {
            "chapters": chapters,
            "mode": "heading",
            "warning": None,
        }

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
            "description": f"{name} 是从原文中识别出的关键人物，参与推动主要冲突。",
            "goal": "在当前情节中争取主动并推动剧情变化。",
        }
        for index, name in enumerate(sorted_names)
    ]


def infer_locations(chapters):
    keywords = ["会议室", "办公室", "餐厅", "街道", "学校", "医院", "车站", "房间", "大厅", "门口", "公司"]
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
        for index, name in enumerate(found[:5])
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
                "summary": sample[0][:90],
                "conflict": (sample[1] if len(sample) > 1 else "人物关系出现变化")[:90],
                "emotional_shift": (sample[2] if len(sample) > 2 else "情绪从铺垫转向行动")[:90],
                "characters": [item["id"] for item in characters[: min(3, len(characters))]],
            }
        )
    return events


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


def dump_script_yaml(script):
    return yaml.safe_dump(script, allow_unicode=True, sort_keys=False, indent=2)


def create_script(text, title="未命名小说"):
    parse_result = parse_chapters(text)
    chapters = parse_result["chapters"]
    if len(chapters) < 3:
        raise ValueError("至少需要 3 个章节以上的小说文本")

    characters = infer_characters(chapters)
    locations = infer_locations(chapters)
    events = build_events(chapters, characters)
    scenes = build_scenes(chapters, characters, locations, events)
    script = {
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
    return script, dump_script_yaml(script), parse_result


def parse_yaml_script(yaml_text):
    try:
        script = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败：{exc}") from exc

    if not isinstance(script, dict):
        raise ValueError("YAML 顶层必须是对象")
    return script


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


class AppHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            json_response(self, 200, {"ok": True, "projects": len(PROJECTS)})
        else:
            json_response(self, 404, {"error": "接口不存在"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = read_json(self)
            if path == "/api/analyze":
                parse_result = parse_chapters(payload.get("text", ""))
                chapters = parse_result["chapters"]
                json_response(
                    self,
                    200,
                    {
                        "chapter_count": len(chapters),
                        "chapters": [
                            {
                                "chapter_id": item["chapter_id"],
                                "title": item["title"],
                                "order": item["order"],
                                "word_count": item["word_count"],
                            }
                            for item in chapters
                        ],
                        "parse_mode": parse_result["mode"],
                        "parse_warning": parse_result["warning"],
                    },
                )
            elif path == "/api/generate":
                project_id = str(uuid.uuid4())
                script, yaml_text, parse_result = create_script(
                    payload.get("text", ""),
                    payload.get("title", "未命名小说"),
                )
                validation = validate_script(script)
                PROJECTS[project_id] = {"script": script, "yaml": yaml_text, "validation": validation}
                json_response(
                    self,
                    200,
                    {
                        "project_id": project_id,
                        "script": script,
                        "yaml": yaml_text,
                        "validation": validation,
                        "parse_mode": parse_result["mode"],
                        "parse_warning": parse_result["warning"],
                    },
                )
            elif path == "/api/validate":
                yaml_text = payload.get("yaml")
                if isinstance(yaml_text, str):
                    script = parse_yaml_script(yaml_text)
                else:
                    script = payload.get("script")
                    if not isinstance(script, dict):
                        json_response(self, 400, {"error": "请传入 yaml 文本或 script 对象"})
                        return
                json_response(self, 200, validate_script(script))
            else:
                json_response(self, 404, {"error": "接口不存在"})
        except ValueError as exc:
            json_response(self, 400, {"error": str(exc)})
        except ValidationError as exc:
            json_response(self, 400, {"error": exc.message})
        except Exception as exc:
            json_response(self, 500, {"error": f"服务异常：{exc}"})

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Novel2Script backend running at http://{HOST}:{PORT}")
    server.serve_forever()

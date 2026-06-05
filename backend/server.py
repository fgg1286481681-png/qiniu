from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import json
import re
import time
import uuid


HOST = "127.0.0.1"
PORT = 8000
PROJECTS = {}


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


def parse_chapters(text):
    normalized = text.replace("\r\n", "\n").strip()
    pattern = re.compile(r"(?m)^\s*((第[一二三四五六七八九十百千万0-9]+[章节回幕][^\n]*)|(Chapter\s+\d+[^\n]*)|(章节\s*[一二三四五六七八九十百千万0-9]+[^\n]*))\s*$", re.I)
    matches = list(pattern.finditer(normalized))
    chapters = []

    if matches:
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
            title = match.group(1).strip()
            body = normalized[start:end].strip()
            if body:
                chapters.append(
                    {
                        "chapter_id": f"chapter_{index + 1:03d}",
                        "order": index + 1,
                        "title": title,
                        "text": body,
                        "word_count": len(body),
                    }
                )

    if not chapters and normalized:
        chunks = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
        size = max(1, len(chunks) // 3)
        grouped = ["\n\n".join(chunks[i : i + size]) for i in range(0, len(chunks), size)]
        chapters = [
            {
                "chapter_id": f"chapter_{index + 1:03d}",
                "order": index + 1,
                "title": f"自动拆分章节 {index + 1}",
                "text": body,
                "word_count": len(body),
            }
            for index, body in enumerate(grouped[:6])
        ]

    return chapters


def split_sentences(text):
    return [item.strip() for item in re.split(r"(?<=[。！？!?])", text) if item.strip()]


def infer_characters(chapters):
    candidates = {}
    for chapter in chapters:
        text = chapter["text"]
        for name in re.findall(r"[\u4e00-\u9fa5]{2,4}", text):
            if name.startswith(("第", "这个", "那个", "他们", "我们", "时候", "声音", "会议", "餐厅")):
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
    keywords = ["会议室", "办公室", "餐厅", "街道", "学校", "医院", "车站", "房间", "大厅", "门口"]
    found = []
    full_text = "\n".join(chapter["text"] for chapter in chapters)
    for word in keywords:
        if word in full_text:
            found.append(word)
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


def yaml_scalar(value):
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def dump_yaml(data, indent=0):
    lines = []
    prefix = " " * indent
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(dump_yaml(value, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {yaml_scalar(value)}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(dump_yaml(item, indent + 2))
            else:
                lines.append(f"{prefix}- {yaml_scalar(item)}")
    return lines


def create_script(text, title="未命名小说"):
    chapters = parse_chapters(text)
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
    yaml_text = "\n".join(dump_yaml(script)) + "\n"
    return script, yaml_text


def validate_script(script):
    errors = []
    for field in ["schema_version", "metadata", "characters", "locations", "scenes"]:
        if field not in script:
            errors.append({"path": field, "message": "缺少必填字段"})
    character_ids = {item.get("id") for item in script.get("characters", [])}
    location_ids = {item.get("id") for item in script.get("locations", [])}
    for scene_index, scene in enumerate(script.get("scenes", [])):
        if scene.get("heading", {}).get("location_id") not in location_ids:
            errors.append({"path": f"scenes[{scene_index}].heading.location_id", "message": "引用了不存在的地点 ID"})
        if len(scene.get("elements", [])) < 3:
            errors.append({"path": f"scenes[{scene_index}].elements", "message": "每个场景至少需要 3 个剧本元素"})
        for element_index, element in enumerate(scene.get("elements", [])):
            if element.get("type") == "dialogue" and element.get("character_id") not in character_ids:
                errors.append({"path": f"scenes[{scene_index}].elements[{element_index}].character_id", "message": "对白引用了不存在的人物 ID"})
    return {"valid": len(errors) == 0, "errors": errors}


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
                chapters = parse_chapters(payload.get("text", ""))
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
                    },
                )
            elif path == "/api/generate":
                project_id = str(uuid.uuid4())
                script, yaml_text = create_script(payload.get("text", ""), payload.get("title", "未命名小说"))
                validation = validate_script(script)
                PROJECTS[project_id] = {"script": script, "yaml": yaml_text, "validation": validation}
                json_response(self, 200, {"project_id": project_id, "script": script, "yaml": yaml_text, "validation": validation})
            elif path == "/api/validate":
                script = payload.get("script")
                if not isinstance(script, dict):
                    json_response(self, 400, {"error": "第一版校验接口需要传入 script 对象"})
                    return
                json_response(self, 200, validate_script(script))
            else:
                json_response(self, 404, {"error": "接口不存在"})
        except ValueError as exc:
            json_response(self, 400, {"error": str(exc)})
        except Exception as exc:
            json_response(self, 500, {"error": f"服务异常：{exc}"})

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Novel2Script backend running at http://{HOST}:{PORT}")
    server.serve_forever()


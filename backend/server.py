from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import json

from jsonschema.exceptions import ValidationError

from agents.reader import parse_chapters
from agents.validator import parse_yaml_script, validate_script
from orchestrator import generate_project


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
                result = generate_project(
                    payload.get("text", ""),
                    payload.get("title", "未命名小说"),
                )
                PROJECTS[result["project_id"]] = {
                    "script": result["script"],
                    "yaml": result["yaml"],
                    "validation": result["validation"],
                    "agent_trace": result["agent_trace"],
                }
                json_response(self, 200, result)
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

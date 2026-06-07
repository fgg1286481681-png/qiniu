from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
import json
from time import perf_counter
import uuid

from jsonschema.exceptions import ValidationError

from agents.reader import parse_chapters
from agents.validator import parse_yaml_script, validate_script
from demo_service import import_demo_project
from llm_client import LLMClient, get_env
from orchestrator import generate_project
from project_store import ProjectStore
from quality_metrics import calculate_quality_metrics
from trace_utils import utc_now


HOST = "127.0.0.1"
PORT = 8000
PROJECT_STORE = ProjectStore()
PROJECT_STORE.interrupt_processing_projects()
TASK_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="novel2script")


class GenerationCancelled(Exception):
    pass


def check_ai_model(model):
    configured = bool(
        get_env("LLM_API_BASE_URL", "")
        and get_env("LLM_API_KEY", "")
        and model
    )
    result = {
        "model": model or None,
        "configured": configured,
        "reachable": False,
        "duration_ms": None,
        "error": None,
    }
    if not configured:
        result["error"] = "API 地址、Key 或模型名称未完整配置"
        return result

    started = perf_counter()
    try:
        client = LLMClient(
            timeout_seconds=int(get_env("LLM_HEALTH_TIMEOUT_SECONDS", "20")),
            max_retries=1,
        )
        response = client.probe(model=model)
        result["reachable"] = True
        result["resolved_model"] = response["model"]
    except Exception as exc:
        result["error"] = str(exc)[:300]
    result["duration_ms"] = round((perf_counter() - started) * 1000)
    return result


def check_all_ai_agents():
    models = {
        "Reader Agent": get_env("READER_MODEL", ""),
        "Planner Agent": get_env("PLANNER_MODEL", ""),
        "Writer Agent": get_env("WRITER_MODEL", ""),
        "Validator Agent": get_env("VALIDATOR_MODEL", ""),
    }
    model_results = {}
    for model in dict.fromkeys(models.values()):
        model_results[model] = check_ai_model(model)

    results = [
        {
            **model_results[model],
            "agent": agent,
            "shared_check": sum(
                configured_model == model
                for configured_model in models.values()
            )
            > 1,
        }
        for agent, model in models.items()
    ]
    return {
        "ready": all(item["reachable"] for item in results),
        "agents": results,
    }


def json_response(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
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


def project_id_from_path(path):
    prefix = "/api/projects/"
    if not path.startswith(prefix):
        return None
    project_id = path[len(prefix) :].strip("/")
    return project_id or None


def public_result(result):
    return {key: value for key, value in result.items() if key != "_artifacts"}


def run_generation(project_id, source_text, title):
    def ensure_not_cancelled():
        if PROJECT_STORE.is_cancel_requested(project_id):
            raise GenerationCancelled("用户取消了生成任务")

    def report_progress(step, progress, agent_trace):
        ensure_not_cancelled()
        trace = list(agent_trace)
        if step in {"reader", "planner", "writer", "validator", "repair"}:
            agent_names = {
                "reader": "Reader Agent",
                "planner": "Planner Agent",
                "writer": "Writer Agent",
                "validator": "Validator Agent",
                "repair": "Writer Agent",
            }
            trace.append(
                {
                    "agent": agent_names[step],
                    "stage": step,
                    "status": "running",
                    "source": "pending",
                    "model": None,
                    "started_at": utc_now(),
                    "ended_at": None,
                    "duration_ms": None,
                    "attempts": 0,
                    "fallback_reason": None,
                    "usage": None,
                    "summary": "正在执行",
                }
            )
        PROJECT_STORE.update_progress(project_id, step, progress, trace)

    try:
        ensure_not_cancelled()
        result = generate_project(
            source_text,
            title,
            project_id=project_id,
            progress_callback=report_progress,
        )
        ensure_not_cancelled()
        artifacts = result["_artifacts"]
        PROJECT_STORE.complete_project(project_id, result, artifacts)
        return public_result(result)
    except GenerationCancelled:
        PROJECT_STORE.mark_cancelled(project_id)
        return None
    except Exception as exc:
        PROJECT_STORE.fail_project(project_id, exc)
        raise


class AppHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/health":
                json_response(
                    self,
                    200,
                    {"ok": True, "projects": PROJECT_STORE.count_projects()},
                )
                return

            if path == "/api/projects":
                json_response(self, 200, {"projects": PROJECT_STORE.list_projects()})
                return

            project_id = project_id_from_path(path)
            if project_id:
                project = PROJECT_STORE.get_project(project_id)
                if project is None:
                    json_response(self, 404, {"error": "项目不存在"})
                else:
                    json_response(self, 200, project)
                return

            json_response(self, 404, {"error": "接口不存在"})
        except ValueError as exc:
            json_response(self, 400, {"error": str(exc)})
        except Exception as exc:
            json_response(self, 500, {"error": f"服务异常：{exc}"})

    def do_POST(self):
        path = urlparse(self.path).path
        project_id = None
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
                        "confidence": parse_result.get("confidence"),
                        "warnings": parse_result.get("warnings", []),
                        "candidate_count": len(parse_result.get("candidates", [])),
                        "candidates": parse_result.get("candidates", []),
                    },
                )
                return

            if path == "/api/generate":
                source_text = payload.get("text", "")
                title = payload.get("title") or "未命名小说"
                project_id = str(uuid.uuid4())
                PROJECT_STORE.create_project(
                    project_id,
                    title,
                    source_text,
                    payload.get("source_filename"),
                )

                result = run_generation(project_id, source_text, title)
                json_response(self, 200, result)
                return

            if path == "/api/generate-async":
                source_text = payload.get("text", "")
                title = payload.get("title") or "未命名小说"
                project_id = str(uuid.uuid4())
                PROJECT_STORE.create_project(
                    project_id,
                    title,
                    source_text,
                    payload.get("source_filename"),
                )
                TASK_EXECUTOR.submit(run_generation, project_id, source_text, title)
                json_response(
                    self,
                    202,
                    {
                        "project_id": project_id,
                        "status": "processing",
                        "current_step": "queued",
                    },
                )
                return

            if path == "/api/demo/import":
                project = import_demo_project(PROJECT_STORE)
                json_response(self, 200, project)
                return

            if path == "/api/ai/health":
                json_response(self, 200, check_all_ai_agents())
                return

            if path.startswith("/api/projects/") and path.endswith("/cancel"):
                cancel_project_id = path[
                    len("/api/projects/") : -len("/cancel")
                ].strip("/")
                result = PROJECT_STORE.request_cancel(cancel_project_id)
                if result is None:
                    json_response(self, 404, {"error": "项目不存在"})
                elif result is False:
                    json_response(self, 409, {"error": "项目当前状态无法取消"})
                else:
                    json_response(
                        self,
                        202,
                        {
                            "project_id": cancel_project_id,
                            "status": "processing",
                            "current_step": "cancelling",
                        },
                    )
                return

            if path == "/api/validate":
                yaml_text = payload.get("yaml")
                if isinstance(yaml_text, str):
                    script = parse_yaml_script(yaml_text)
                else:
                    script = payload.get("script")
                    if not isinstance(script, dict):
                        json_response(self, 400, {"error": "请传入 yaml 文本或 script 对象"})
                        return
                validation = validate_script(script)
                response = dict(validation)
                response["quality_metrics"] = calculate_quality_metrics(
                    script,
                    validation,
                )
                json_response(self, 200, response)
                return

            json_response(self, 404, {"error": "接口不存在"})
        except ValueError as exc:
            if project_id:
                PROJECT_STORE.fail_project(project_id, exc)
            json_response(self, 400, {"error": str(exc), "project_id": project_id})
        except ValidationError as exc:
            if project_id:
                PROJECT_STORE.fail_project(project_id, exc.message)
            json_response(self, 400, {"error": exc.message, "project_id": project_id})
        except Exception as exc:
            if project_id:
                PROJECT_STORE.fail_project(project_id, exc)
            json_response(
                self,
                500,
                {"error": f"服务异常：{exc}", "project_id": project_id},
            )

    def do_DELETE(self):
        path = urlparse(self.path).path
        try:
            project_id = project_id_from_path(path)
            if not project_id:
                json_response(self, 404, {"error": "接口不存在"})
                return

            if PROJECT_STORE.delete_project(project_id):
                json_response(self, 200, {"deleted": True, "project_id": project_id})
            else:
                json_response(self, 404, {"error": "项目不存在"})
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

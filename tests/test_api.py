import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "backend"))
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_API_BASE_URL"] = ""
os.environ["READER_MODEL"] = ""
os.environ["PLANNER_MODEL"] = ""
os.environ["WRITER_MODEL"] = ""
os.environ["VALIDATOR_MODEL"] = ""

import server
from project_store import ProjectStore


SOURCE = """第一章 开始
林清走进旧车站，见到了等候多时的周原。

第二章 分歧
两人因为是否公开一封旧信发生争执。

第三章 决定
他们最终决定将旧信交给档案馆保存。"""


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        server.PROJECT_STORE = ProjectStore(root / "test.db", root / "projects")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.AppHandler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self.temp_dir.cleanup()

    def request(self, method, path, payload=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_async_generate_and_poll(self):
        status, created = self.request(
            "POST",
            "/api/generate-async",
            {"title": "异步测试", "text": SOURCE},
        )
        self.assertEqual(status, 202)
        project_id = created["project_id"]

        deadline = time.time() + 10
        project = None
        while time.time() < deadline:
            _, project = self.request("GET", f"/api/projects/{project_id}")
            if project["status"] != "processing":
                break
            time.sleep(0.1)

        self.assertEqual(project["status"], "completed")
        self.assertEqual(project["progress"], 100)
        self.assertTrue(project["validation"]["valid"])
        self.assertIsNotNone(project["quality_metrics"])
        self.assertTrue(project["agent_trace"])

    def test_demo_import_endpoint(self):
        status, project = self.request("POST", "/api/demo/import", {})
        self.assertEqual(status, 200)
        self.assertEqual(project["title"], "停电前的最后一单")
        self.assertEqual(project["repair_count"], 1)


if __name__ == "__main__":
    unittest.main()

import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "backend"))
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_API_BASE_URL"] = ""
os.environ["READER_MODEL"] = ""
os.environ["PLANNER_MODEL"] = ""
os.environ["WRITER_MODEL"] = ""
os.environ["VALIDATOR_MODEL"] = ""

from agents.planner import PlannerLLMProvider, build_rule_plan, chunk_chapters
from agents.reader import ReaderAgent, ReaderLLMProvider, chunk_paragraphs, parse_chapters
from agents.validator import ValidatorAgent, validate_script
from agents.writer import build_rule_script, dump_script_yaml
from demo_service import import_demo_project
from orchestrator import Orchestrator
from project_store import ProjectStore
from quality_metrics import calculate_quality_metrics


SOURCE = """第一章 开始
林清走进旧车站，见到了等候多时的周原。

第二章 分歧
两人因为是否公开一封旧信发生争执。

第三章 决定
他们最终决定将旧信交给档案馆保存。"""


def valid_script():
    reader = parse_chapters(SOURCE)
    plan = build_rule_plan(reader["chapters"])
    return plan, build_rule_script(plan, "测试剧本")


class BrokenReaderProvider:
    model = "broken-model"

    def parse(self, text):
        raise RuntimeError("provider unavailable")


class ReaderChunkClient:
    enabled = True

    def __init__(self):
        self.calls = 0

    def chat_json(self, **kwargs):
        self.calls += 1
        paragraph_count = kwargs["user_prompt"].count("[段落 ")
        return {
            "data": {
                "chapters": [
                    {
                        "title": f"分块 {self.calls}",
                        "summary": "分块摘要",
                        "paragraph_start": 1,
                        "paragraph_end": paragraph_count,
                        "key_events": ["事件"],
                        "characters": ["林清"],
                        "locations": ["车站"],
                    }
                ]
            },
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "attempts": 1,
            "model": "reader-test",
        }


class PlannerChunkClient:
    enabled = True

    def __init__(self):
        self.calls = 0

    def chat_json(self, **kwargs):
        self.calls += 1
        marker = "不要写剧本对白。\n"
        chapters = json.loads(kwargs["user_prompt"].split(marker, 1)[1])
        return {
            "data": {
                "characters": [
                    {
                        "name": "林清",
                        "aliases": [],
                        "role": "protagonist",
                        "description": "调查者",
                        "goal": "查明真相",
                    }
                ],
                "locations": [{"name": "车站", "description": "旧车站"}],
                "events": [
                    {
                        "source_chapter": chapter["chapter_id"],
                        "summary": f"{chapter['title']}事件",
                        "conflict": "是否公开",
                        "emotional_shift": "从犹豫到坚定",
                        "character_names": ["林清"],
                    }
                    for chapter in chapters
                ],
            },
            "usage": {"prompt_tokens": 20, "completion_tokens": 10},
            "attempts": 1,
            "model": "planner-test",
        }


class RepairingWriter:
    def __init__(self):
        self.repair_calls = 0

    def run(self, plan, title):
        script = build_rule_script(plan, title)
        script["scenes"][0]["elements"] = script["scenes"][0]["elements"][:2]
        return {
            "script": script,
            "yaml": dump_script_yaml(script),
            "trace": {"agent": "Writer Agent", "stage": "writer", "status": "success"},
        }

    def repair(self, script, plan, validation, round_number):
        self.repair_calls += 1
        repaired = deepcopy(script)
        if self.repair_calls == 2:
            repaired["scenes"][0]["elements"].append(
                {"type": "action", "text": "第二轮补充动作。"}
            )
        return {
            "script": repaired,
            "yaml": dump_script_yaml(repaired),
            "trace": {"agent": "Writer Agent", "stage": "repair", "status": "repaired"},
        }


class CoreTests(unittest.TestCase):
    def test_reader_chunks_long_text_and_merges_usage(self):
        paragraphs = [f"段落 {index} " + "内容" * 80 for index in range(6)]
        client = ReaderChunkClient()
        provider = ReaderLLMProvider(client=client, model="reader-test")
        provider.chunk_chars = 400
        result = provider.parse("\n\n".join(paragraphs))
        self.assertGreater(result["parse_result"]["chunk_count"], 1)
        self.assertEqual(
            len(result["parse_result"]["chapters"]),
            result["parse_result"]["chunk_count"],
        )
        self.assertEqual(
            result["usage"]["prompt_tokens"],
            result["parse_result"]["chunk_count"] * 10,
        )

    def test_planner_chunks_and_deduplicates_global_entities(self):
        chapters = parse_chapters(SOURCE)["chapters"]
        for chapter in chapters:
            chapter["text"] *= 8
        client = PlannerChunkClient()
        provider = PlannerLLMProvider(client=client, model="planner-test")
        provider.chunk_chars = 150
        result = provider.plan(chapters)
        self.assertGreater(result["chunk_count"], 1)
        self.assertEqual(len(result["plan"]["characters"]), 1)
        self.assertEqual(len(result["plan"]["locations"]), 1)
        self.assertEqual(len(result["plan"]["events"]), len(chapters))

    def test_chunk_helpers_respect_character_budget(self):
        paragraph_chunks = chunk_paragraphs(["甲" * 50, "乙" * 50], 60)
        self.assertEqual(len(paragraph_chunks), 2)
        chapter_batches = chunk_chapters(
            [{"text": "甲" * 50}, {"text": "乙" * 50}],
            240,
        )
        self.assertEqual(len(chapter_batches), 2)

    def test_schema_requires_three_scene_elements(self):
        _, script = valid_script()
        script["scenes"][0]["elements"] = script["scenes"][0]["elements"][:2]
        validation = validate_script(script)
        self.assertFalse(validation["valid"])
        self.assertTrue(
            any("至少保留 3 项" in item["suggestion"] for item in validation["errors"])
        )

    def test_quality_metrics_are_deterministic(self):
        _, script = valid_script()
        validation = validate_script(script)
        metrics = calculate_quality_metrics(script, validation, repair_count=1)
        self.assertEqual(metrics["chapter_coverage"], 1.0)
        self.assertEqual(metrics["event_coverage"], 1.0)
        self.assertEqual(metrics["reference_consistency"], 1.0)
        self.assertEqual(metrics["scene_completeness"], 1.0)
        self.assertTrue(metrics["repair_triggered"])

    def test_reader_fallback_is_marked_degraded(self):
        result = ReaderAgent(provider=BrokenReaderProvider()).run(SOURCE)
        self.assertEqual(result["trace"]["status"], "degraded")
        self.assertEqual(result["trace"]["source"], "rule")
        self.assertIn("provider unavailable", result["trace"]["fallback_reason"])

    def test_orchestrator_repairs_at_most_two_rounds(self):
        writer = RepairingWriter()
        result = Orchestrator(
            writer=writer,
            validator=ValidatorAgent(provider=None),
        ).generate(SOURCE, "修复测试")
        self.assertEqual(result["repair_count"], 2)
        self.assertEqual(writer.repair_calls, 2)
        self.assertEqual(result["final_status"], "completed")
        self.assertTrue(result["validation"]["valid"])

    def test_project_store_status_and_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ProjectStore(root / "test.db", root / "projects")
            project_id = "00000000-0000-4000-8000-000000000010"
            store.create_project(project_id, "测试", SOURCE, "test.txt")
            store.update_progress(project_id, "writer", 50, [])
            project = store.get_project(project_id)
            self.assertEqual(project["current_step"], "writer")
            self.assertEqual(project["progress"], 50)
            self.assertEqual(store.interrupt_processing_projects(), 1)
            self.assertEqual(store.get_project(project_id)["status"], "interrupted")

    def test_demo_import_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ProjectStore(root / "test.db", root / "projects")
            first = import_demo_project(store)
            second = import_demo_project(store)
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(store.count_projects(), 1)
            self.assertTrue(second["validation"]["valid"])
            self.assertEqual(second["repair_count"], 1)
            self.assertIsNotNone(second["quality_metrics"])


if __name__ == "__main__":
    unittest.main()

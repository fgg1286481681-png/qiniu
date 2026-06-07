import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "backend"))
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_API_BASE_URL"] = ""
os.environ["READER_MODEL"] = ""
os.environ["PLANNER_MODEL"] = ""
os.environ["WRITER_MODEL"] = ""
os.environ["VALIDATOR_MODEL"] = ""

from agents.planner import (
    PlannerLLMProvider,
    build_rule_plan,
    chunk_chapters,
    infer_characters,
)
from agents.reader import ReaderAgent, ReaderLLMProvider, chunk_paragraphs, parse_chapters
from agents.validator import ValidatorAgent, normalize_ai_review, validate_script
from agents.writer import build_rule_script, dump_script_yaml
from demo_service import import_demo_project
from llm_client import LLMClient
from orchestrator import Orchestrator
from project_store import ProjectStore
from quality_metrics import calculate_quality_metrics


SOURCE = """第一章 开始
林清走进旧车站，见到了等候多时的周原。

第二章 分歧
两人因为是否公开一封旧信发生争执。

第三章 决定
他们最终决定将旧信交给档案馆保存。"""


class LLMClientTests(unittest.TestCase):
    def test_probe_accepts_plain_text_response(self):
        client = LLMClient(
            api_base_url="https://example.test/v1",
            api_key="test-key",
        )
        with patch.object(
            client,
            "_post_chat_completions",
            return_value={
                "content": "OK",
                "model": "test-model",
                "usage": {"total_tokens": 3},
            },
        ) as post:
            result = client.probe(model="test-model")

        self.assertEqual(result["content"], "OK")
        self.assertEqual(result["model"], "test-model")
        self.assertEqual(post.call_count, 1)


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


class HighIssueValidatorProvider:
    model = "validator-test"

    def __init__(self):
        self.calls = 0

    def review(self, script):
        self.calls += 1
        issues = []
        if self.calls == 1:
            issues = [
                {
                    "type": "performability",
                    "severity": "high",
                    "scene_id": script["scenes"][0]["id"],
                    "message": "场景缺少可执行动作",
                    "suggestion": "补充人物推动冲突的动作",
                    "rewrite_scope": [script["scenes"][0]["id"]],
                }
            ]
        return {
            "review": normalize_ai_review(
                {
                    "scores": {
                        "fidelity": 80,
                        "performability": 60 if issues else 82,
                        "character_dialogue_consistency": 76,
                    },
                    "issues": issues,
                    "requires_rewrite": bool(issues),
                    "rewrite_scope": [
                        script["scenes"][0]["id"]
                    ] if issues else [],
                }
            ),
            "usage": {"total_tokens": 20},
            "attempts": 1,
        }


class AiIssueRepairingWriter:
    def __init__(self):
        self.repair_calls = 0

    def run(self, plan, title):
        script = build_rule_script(plan, title)
        return {
            "script": script,
            "yaml": dump_script_yaml(script),
            "trace": {"agent": "Writer Agent", "stage": "writer", "status": "success"},
        }

    def repair(self, script, plan, validation, round_number):
        self.repair_calls += 1
        repaired = deepcopy(script)
        return {
            "script": repaired,
            "yaml": dump_script_yaml(repaired),
            "trace": {"agent": "Writer Agent", "stage": "repair", "status": "repaired"},
        }


class CoreTests(unittest.TestCase):
    def test_parse_chapters_supports_common_heading_variants(self):
        text = """【第一章】雨夜
林夏在雨里找到钥匙。

# 第二章 后台
沈砚打开旧剧场的门。

Chapter 3 Tape
录音带播放出真相。"""
        result = parse_chapters(text)
        self.assertIn(result["mode"], {"heading", "soft_heading"})
        self.assertEqual(len(result["chapters"]), 3)
        self.assertGreaterEqual(result["confidence"], 0.55)
        self.assertIn("雨夜", result["chapters"][0]["title"])

    def test_parse_chapters_supports_numbered_soft_headings(self):
        text = """01 雨夜
第一段剧情。

02 后台
第二段剧情。

03 录音
第三段剧情。"""
        result = parse_chapters(text)
        self.assertEqual(result["mode"], "soft_heading")
        self.assertEqual(len(result["chapters"]), 3)

    def test_parse_chapters_filters_numeric_noise(self):
        text = """第一部 测试

一、开端
第一段剧情。

2.5
这是一个小数，不是章节。

1999.3.5
这是一个日期，不是章节。

92.1%的公民同意本计划。
这是统计句，不是章节。

二、发展
第二段剧情。

三、结尾
第三段剧情。"""
        result = parse_chapters(text)
        titles = [item["title"] for item in result["chapters"]]
        self.assertIn("第一部 测试 / 一、开端", titles)
        self.assertIn("第一部 测试 / 二、发展", titles)
        self.assertNotIn("2.5", titles)
        self.assertNotIn("1999.3.5", titles)
        self.assertTrue(any(item.get("excluded") for item in result["candidates"]))

    def test_parse_chapters_attaches_plain_section_context(self):
        text = """接过世界

一、纪元初两小时
第一段剧情。

二、第五代
第二段剧情。

三、最高领导人
第三段剧情。"""
        result = parse_chapters(text)
        titles = [item["title"] for item in result["chapters"]]
        self.assertEqual(titles[0], "接过世界 / 一、纪元初两小时")
        self.assertEqual(titles[1], "接过世界 / 二、第五代")
        self.assertEqual(titles[2], "接过世界 / 三、最高领导人")

    def test_parse_chapters_avoids_inline_false_positive(self):
        text = """林夏翻到书里的第一章，发现那只是教材目录，不是故事标题。
她继续往下读，雨声越来越急。

沈砚把纸箱搬到门口，里面有票根和录音带。
两人决定去旧剧场寻找后台入口。

录音带播放出周予的声音，真相终于被确认。
天亮时，旧书店门口亮起灯。"""
        result = parse_chapters(text)
        self.assertEqual(result["mode"], "smart_fallback")
        self.assertGreaterEqual(len(result["chapters"]), 3)
        self.assertLessEqual(result["confidence"], 0.45)

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

    def test_schema_requires_at_least_three_chapters(self):
        _, script = valid_script()
        script["chapters"] = script["chapters"][:2]
        validation = validate_script(script)
        self.assertFalse(validation["valid"])
        self.assertTrue(
            any(
                item["path"] == "chapters" and "至少保留 3 项" in item["suggestion"]
                for item in validation["errors"]
            )
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

    def test_ai_review_uses_three_mvp_dimensions(self):
        review = normalize_ai_review(
            {
                "scores": {
                    "fidelity": 90,
                    "performability": 80,
                    "character_dialogue_consistency": 70,
                },
                "issues": [
                    {
                        "type": "dialogue",
                        "severity": "critical",
                        "scene_id": "scene_001",
                        "message": "人物关系与前文冲突",
                        "suggested_fix": "恢复原人物关系",
                    }
                ],
            }
        )
        self.assertEqual(review["ai_draft_score"], 82)
        self.assertEqual(review["score"], 82)
        self.assertEqual(review["issues"][0]["severity"], "high")
        self.assertEqual(review["issues"][0]["suggestion"], "恢复原人物关系")
        self.assertEqual(review["rewrite_scope"], ["scene_001"])
        self.assertTrue(review["requires_rewrite"])

    def test_dialogue_character_must_belong_to_scene(self):
        _, script = valid_script()
        other_character = deepcopy(script["characters"][0])
        other_character["id"] = "char_999"
        other_character["name"] = "旁观者"
        script["characters"].append(other_character)
        dialogue = next(
            element
            for element in script["scenes"][0]["elements"]
            if element["type"] == "dialogue"
        )
        dialogue["character_id"] = "char_999"

        validation = validate_script(script)

        self.assertFalse(validation["valid"])
        self.assertTrue(
            any(
                item["message"] == "对白人物未列入当前场景人物"
                for item in validation["errors"]
            )
        )

    def test_rule_writer_uses_event_specific_dialogue(self):
        _, script = valid_script()
        dialogue = [
            element["text"]
            for scene in script["scenes"]
            for element in scene["elements"]
            if element["type"] == "dialogue"
        ]
        self.assertNotIn("这件事不能再拖下去了。", dialogue)
        self.assertNotIn("你确定自己承担得起后果吗？", dialogue)
        self.assertGreater(len(set(dialogue)), 2)

    def test_rule_character_extraction_prefers_behavior_context(self):
        chapters = [
            {
                "text": (
                    "会议室里讨论方案和数据。林清走进会议室，林清说要核对证据。"
                    "周原回答需要查看报告，周原决定一起调查。"
                )
            }
        ]
        names = [item["name"] for item in infer_characters(chapters)]
        self.assertIn("林清", names)
        self.assertIn("周原", names)
        self.assertNotIn("会议室", names)

    def test_rule_character_extraction_trims_connective_words(self):
        chapters = [
            {
                "text": (
                    "许澄却发现订单异常，许澄整理柜台。"
                    "并由周岚决定是否签名，周岚赶到书店。"
                )
            }
        ]
        names = [item["name"] for item in infer_characters(chapters)]
        self.assertIn("许澄", names)
        self.assertIn("周岚", names)
        self.assertNotIn("许澄却", names)
        self.assertNotIn("并由周岚", names)

    def test_reader_local_high_confidence_skips_broken_provider(self):
        result = ReaderAgent(provider=BrokenReaderProvider()).run(SOURCE)
        self.assertEqual(result["trace"]["status"], "success")
        self.assertEqual(result["trace"]["source"], "rule")
        self.assertIsNone(result["trace"]["fallback_reason"])

    def test_reader_low_confidence_llm_failure_keeps_local_result(self):
        low_confidence_source = "没有标题的第一段。\n\n没有标题的第二段。\n\n没有标题的第三段。"
        result = ReaderAgent(provider=BrokenReaderProvider()).run(low_confidence_source)
        self.assertEqual(result["trace"]["status"], "degraded")
        self.assertEqual(result["trace"]["source"], "rule")
        self.assertIn("provider unavailable", result["trace"]["fallback_reason"])
        self.assertGreaterEqual(len(result["parse_result"]["chapters"]), 3)

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

    def test_orchestrator_records_mvp_repair_metrics(self):
        writer = AiIssueRepairingWriter()
        validator = ValidatorAgent(provider=HighIssueValidatorProvider())
        result = Orchestrator(
            writer=writer,
            validator=validator,
        ).generate(SOURCE, "AI 修复记录测试")

        history = result["_artifacts"]["repair_history"]
        self.assertEqual(result["repair_count"], 1)
        self.assertEqual(result["final_status"], "completed")
        self.assertEqual(history[0]["rewrite_scope"], ["scene_001"])
        self.assertIn("场景缺少可执行动作", history[0]["reason"])
        self.assertIn("before_metrics", history[0])
        self.assertIn("after_metrics", history[0])

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

    def test_project_store_cancel_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ProjectStore(root / "test.db", root / "projects")
            project_id = "00000000-0000-4000-8000-000000000011"
            store.create_project(project_id, "取消测试", SOURCE, "test.txt")
            self.assertTrue(store.request_cancel(project_id))
            self.assertTrue(store.is_cancel_requested(project_id))
            store.mark_cancelled(project_id)
            project = store.get_project(project_id)
            self.assertEqual(project["status"], "cancelled")
            self.assertEqual(project["current_step"], "cancelled")

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

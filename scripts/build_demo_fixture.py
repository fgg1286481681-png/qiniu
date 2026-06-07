import json
import sys
from copy import deepcopy
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = BASE_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from agents.planner import build_rule_plan
from agents.reader import parse_chapters
from agents.validator import validate_script
from agents.writer import build_rule_script, dump_script_yaml, repair_script_rules
from quality_metrics import calculate_quality_metrics


def trace(agent, stage, status, summary):
    return {
        "agent": agent,
        "stage": stage,
        "status": status,
        "source": "offline_demo",
        "model": None,
        "started_at": "2026-06-06T10:00:00.000+00:00",
        "ended_at": "2026-06-06T10:00:01.000+00:00",
        "duration_ms": 1000,
        "attempts": 0,
        "fallback_reason": None,
        "usage": None,
        "summary": summary,
    }


def main():
    source_text = (BASE_DIR / "demo" / "source.txt").read_text(encoding="utf-8")
    reader = parse_chapters(source_text)
    planner = build_rule_plan(reader["chapters"])
    final_script = build_rule_script(planner, "停电前的最后一单")

    initial_script = deepcopy(final_script)
    initial_script["scenes"][0]["heading"]["location_id"] = "loc_missing"
    initial_script["scenes"][0]["elements"] = initial_script["scenes"][0]["elements"][:2]
    initial_validation = validate_script(initial_script)
    initial_validation["ai_review"] = {
        "score": 66,
        "ai_draft_score": 66,
        "scores": {
            "fidelity": 82,
            "performability": 48,
            "character_dialogue_consistency": 66,
        },
        "issues": [
            {
                "type": "performability",
                "severity": "high",
                "scene_id": "scene_001",
                "message": "首场景元素不足，无法形成完整的动作与对白推进。",
                "suggestion": "补充围绕重复订单冲突的可执行动作。",
                "rewrite_scope": ["scene_001"],
            }
        ],
        "requires_rewrite": True,
        "rewrite_scope": ["scene_001"],
    }

    repaired_script = repair_script_rules(initial_script, planner)
    final_validation = validate_script(repaired_script)
    final_validation["ai_review"] = {
        "score": 82,
        "ai_draft_score": 82,
        "scores": {
            "fidelity": 88,
            "performability": 78,
            "character_dialogue_consistency": 76,
        },
        "issues": [
            {
                "type": "character_dialogue_consistency",
                "severity": "medium",
                "scene_id": "scene_002",
                "message": "部分对白仍偏直接说明，可继续增强人物口吻差异。",
                "suggestion": "人工润色周岚与陈放的表达方式。",
                "rewrite_scope": [],
            }
        ],
        "requires_rewrite": False,
        "rewrite_scope": [],
    }
    initial_metrics = calculate_quality_metrics(
        initial_script,
        initial_validation,
        repair_count=0,
    )
    metrics = calculate_quality_metrics(repaired_script, final_validation, repair_count=1)

    package = {
        "fixture_version": "2.1",
        "project_id": "00000000-0000-4000-8000-000000000001",
        "title": "停电前的最后一单",
        "source_filename": "原创离线演示小说.txt",
        "source_text": source_text,
        "result": {
            "project_id": "00000000-0000-4000-8000-000000000001",
            "script": repaired_script,
            "yaml": dump_script_yaml(repaired_script),
            "validation": final_validation,
            "quality_metrics": metrics,
            "repair_count": 1,
            "final_status": "completed",
            "parse_mode": reader["mode"],
            "parse_warning": reader["warning"],
            "agent_trace": [
                trace("Reader Agent", "reader", "success", "识别到 4 个章节，离线演示产物"),
                trace("Planner Agent", "planner", "success", "完成事实层规划，离线演示产物"),
                trace("Writer Agent", "writer", "success", "生成初始剧本，离线演示产物"),
                trace("Validator Agent", "validator", "failed", "发现结构和引用问题"),
                trace("Writer Agent", "repair", "repaired", "完成第 1 轮局部修复"),
                trace("Validator Agent", "validator", "success", "修复后 Schema 和引用校验通过"),
            ],
        },
        "artifacts": {
            "reader": reader,
            "planner": planner,
            "initial_script": initial_script,
            "initial_validation": initial_validation,
            "repair_history": [
                {
                    "round": 1,
                    "reason": "Schema 校验失败，且首场景缺少完整动作推进",
                    "rewrite_scope": ["scene_001"],
                    "before_script": initial_script,
                    "before_validation": initial_validation,
                    "before_metrics": initial_metrics,
                    "after_script": repaired_script,
                    "after_validation": final_validation,
                    "after_metrics": metrics,
                }
            ],
        },
    }
    output_path = BASE_DIR / "demo" / "original_project.json"
    output_path.write_text(
        json.dumps(package, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(output_path)


if __name__ == "__main__":
    main()

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "backend"))
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_API_BASE_URL"] = ""
os.environ["READER_MODEL"] = ""
os.environ["PLANNER_MODEL"] = ""
os.environ["WRITER_MODEL"] = ""
os.environ["VALIDATOR_MODEL"] = ""

from orchestrator import generate_project


def average(values):
    return round(sum(values) / len(values), 4) if values else 0


def main():
    cases = json.loads(
        (BASE_DIR / "evaluation" / "cases.json").read_text(encoding="utf-8")
    )
    results = []

    for case in cases:
        started = perf_counter()
        result = generate_project(case["text"], case["title"])
        duration_ms = round((perf_counter() - started) * 1000)
        metrics = result["quality_metrics"]
        chapter_count = len(result["script"]["chapters"])
        passed = (
            chapter_count >= case["minimum_chapters"]
            and result["validation"]["valid"]
            and metrics["chapter_coverage"] == 1
            and metrics["reference_consistency"] == 1
            and metrics["scene_completeness"] == 1
        )
        results.append(
            {
                "id": case["id"],
                "title": case["title"],
                "passed": passed,
                "duration_ms": duration_ms,
                "chapter_count": chapter_count,
                "scene_count": len(result["script"]["scenes"]),
                "parse_mode": result["parse_mode"],
                "quality_metrics": metrics,
            }
        )

    summary = {
        "case_count": len(results),
        "passed_count": sum(item["passed"] for item in results),
        "pass_rate": average([int(item["passed"]) for item in results]),
        "schema_pass_rate": average(
            [int(item["quality_metrics"]["schema_valid"]) for item in results]
        ),
        "average_chapter_coverage": average(
            [item["quality_metrics"]["chapter_coverage"] for item in results]
        ),
        "average_event_coverage": average(
            [item["quality_metrics"]["event_coverage"] for item in results]
        ),
        "average_reference_consistency": average(
            [item["quality_metrics"]["reference_consistency"] for item in results]
        ),
        "average_scene_completeness": average(
            [item["quality_metrics"]["scene_completeness"] for item in results]
        ),
        "average_duration_ms": round(
            sum(item["duration_ms"] for item in results) / len(results)
        ),
    }
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "offline_rule_baseline",
        "summary": summary,
        "cases": results,
    }

    reports_dir = BASE_DIR / "reports"
    reports_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    output_path = reports_dir / f"{timestamp}_核心能力评测报告.json"
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(output_path)
    if summary["passed_count"] != summary["case_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

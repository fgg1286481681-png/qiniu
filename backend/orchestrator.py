import uuid

from agents.reader import ReaderAgent
from agents.planner import PlannerAgent
from agents.writer import WriterAgent
from agents.validator import ValidatorAgent
from quality_metrics import calculate_quality_metrics


class Orchestrator:
    def __init__(self, reader=None, planner=None, writer=None, validator=None):
        self.reader = reader or ReaderAgent()
        self.planner = planner or PlannerAgent()
        self.writer = writer or WriterAgent()
        self.validator = validator or ValidatorAgent()

    def generate(self, text, title="未命名小说", project_id=None, progress_callback=None):
        agent_trace = []
        repair_history = []

        def progress(step, percent):
            if progress_callback:
                progress_callback(step, percent, list(agent_trace))

        progress("reader", 10)
        reader_result = self.reader.run(text)
        agent_trace.append(reader_result["trace"])
        parse_result = reader_result["parse_result"]

        progress("planner", 30)
        planner_result = self.planner.run(parse_result["chapters"])
        agent_trace.append(planner_result["trace"])

        progress("writer", 50)
        writer_result = self.writer.run(planner_result["plan"], title)
        agent_trace.append(writer_result["trace"])
        initial_script = writer_result["script"]

        progress("validator", 70)
        validator_result = self.validator.run(writer_result["script"])
        agent_trace.append(validator_result["trace"])
        initial_validation = validator_result["validation"]

        repair_count = 0
        while repair_count < 2 and needs_repair(validator_result["validation"]):
            repair_count += 1
            progress("repair", 70 + repair_count * 8)
            before_script = writer_result["script"]
            before_validation = validator_result["validation"]
            before_metrics = calculate_quality_metrics(
                before_script,
                before_validation,
                repair_count - 1,
            )
            writer_result = self.writer.repair(
                before_script,
                planner_result["plan"],
                before_validation,
                repair_count,
            )
            agent_trace.append(writer_result["trace"])
            validator_result = self.validator.run(writer_result["script"])
            agent_trace.append(validator_result["trace"])
            after_metrics = calculate_quality_metrics(
                writer_result["script"],
                validator_result["validation"],
                repair_count,
            )
            repair_history.append(
                {
                    "round": repair_count,
                    "reason": repair_reason(before_validation),
                    "rewrite_scope": (
                        before_validation.get("ai_review") or {}
                    ).get("rewrite_scope", []),
                    "before_script": before_script,
                    "before_validation": before_validation,
                    "before_metrics": before_metrics,
                    "after_script": writer_result["script"],
                    "after_validation": validator_result["validation"],
                    "after_metrics": after_metrics,
                }
            )

        progress("metrics", 92)
        metrics = calculate_quality_metrics(
            writer_result["script"],
            validator_result["validation"],
            repair_count,
        )
        final_status = project_status(validator_result["validation"])

        return {
            "project_id": project_id or str(uuid.uuid4()),
            "script": writer_result["script"],
            "yaml": writer_result["yaml"],
            "validation": validator_result["validation"],
            "quality_metrics": metrics,
            "repair_count": repair_count,
            "final_status": final_status,
            "parse_mode": parse_result["mode"],
            "parse_warning": parse_result["warning"],
            "agent_trace": agent_trace,
            "_artifacts": {
                "reader": parse_result,
                "planner": planner_result["plan"],
                "initial_script": initial_script,
                "initial_validation": initial_validation,
                "repair_history": repair_history,
            },
        }


def needs_repair(validation):
    if not validation.get("valid"):
        return True
    return bool((validation.get("ai_review") or {}).get("requires_rewrite"))


def project_status(validation):
    if not validation.get("valid"):
        return "failed"
    if (validation.get("ai_review") or {}).get("requires_rewrite"):
        return "completed_with_warnings"
    return "completed"


def repair_reason(validation):
    if not validation.get("valid"):
        return "Schema 或引用校验失败"
    issues = (validation.get("ai_review") or {}).get("issues") or []
    high_issues = [
        issue.get("message")
        for issue in issues
        if issue.get("severity") in {"high", "critical"}
        and issue.get("message")
    ]
    return "；".join(high_issues) or "Validator 判定需要局部修复"


def generate_project(text, title="未命名小说", project_id=None, progress_callback=None):
    return Orchestrator().generate(
        text,
        title,
        project_id=project_id,
        progress_callback=progress_callback,
    )

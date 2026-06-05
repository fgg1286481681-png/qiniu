import uuid

from agents.reader import ReaderAgent
from agents.planner import PlannerAgent
from agents.writer import WriterAgent
from agents.validator import ValidatorAgent


class Orchestrator:
    def __init__(self, reader=None, planner=None, writer=None, validator=None):
        self.reader = reader or ReaderAgent()
        self.planner = planner or PlannerAgent()
        self.writer = writer or WriterAgent()
        self.validator = validator or ValidatorAgent()

    def generate(self, text, title="未命名小说"):
        agent_trace = []

        reader_result = self.reader.run(text)
        agent_trace.append(reader_result["trace"])
        parse_result = reader_result["parse_result"]

        planner_result = self.planner.run(parse_result["chapters"])
        agent_trace.append(planner_result["trace"])

        writer_result = self.writer.run(planner_result["plan"], title)
        agent_trace.append(writer_result["trace"])

        validator_result = self.validator.run(writer_result["script"])
        agent_trace.append(validator_result["trace"])

        return {
            "project_id": str(uuid.uuid4()),
            "script": writer_result["script"],
            "yaml": writer_result["yaml"],
            "validation": validator_result["validation"],
            "parse_mode": parse_result["mode"],
            "parse_warning": parse_result["warning"],
            "agent_trace": agent_trace,
        }


def generate_project(text, title="未命名小说"):
    return Orchestrator().generate(text, title)

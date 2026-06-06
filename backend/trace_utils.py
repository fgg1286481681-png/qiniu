from datetime import datetime, timezone
from time import perf_counter


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class TraceTimer:
    def __init__(self, agent, stage, model=None):
        self.agent = agent
        self.stage = stage
        self.model = model or None
        self.started_at = utc_now()
        self.started_counter = perf_counter()

    def finish(
        self,
        *,
        status,
        source,
        summary,
        attempts=0,
        fallback_reason=None,
        usage=None,
    ):
        ended_at = utc_now()
        return {
            "agent": self.agent,
            "stage": self.stage,
            "status": status,
            "source": source,
            "model": self.model,
            "started_at": self.started_at,
            "ended_at": ended_at,
            "duration_ms": round((perf_counter() - self.started_counter) * 1000),
            "attempts": attempts,
            "fallback_reason": fallback_reason,
            "usage": usage,
            "summary": summary,
        }

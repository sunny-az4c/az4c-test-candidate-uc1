"""One trace file per run: every model call, tool call and decision, in order."""
import json
import sys
import time
import uuid
from datetime import datetime, timezone

from . import config


class Trace:
    def __init__(self, case_id: str):
        self.trace_id = f"{case_id}-{uuid.uuid4().hex[:8]}"
        self.case_id = case_id
        self.started = time.time()
        self.events = []

    def _add(self, kind: str, agent: str, **data) -> None:
        self.events.append({"t": round(time.time() - self.started, 2), "kind": kind, "agent": agent, **data})

    def model_call(self, agent: str, model: str, usage, seconds: float) -> None:
        self._add("model_call", agent, model=model, seconds=round(seconds, 2),
                  prompt_tokens=getattr(usage, "prompt_tokens", None),
                  completion_tokens=getattr(usage, "completion_tokens", None))

    def tool_call(self, agent: str, name: str, args: dict, ok: bool) -> None:
        self._add("tool_call", agent, tool=name, args=args, ok=ok)

    def event(self, agent: str, what: str, detail=None) -> None:
        self._add("event", agent, what=what, detail=detail)
        print(f"  [{self.case_id}] {agent}: {what}", file=sys.stderr)

    def tokens(self) -> int:
        return sum((e.get("prompt_tokens") or 0) + (e.get("completion_tokens") or 0)
                   for e in self.events if e["kind"] == "model_call")

    def save(self, result: dict | None = None) -> None:
        config.TRACES_DIR.mkdir(parents=True, exist_ok=True)
        doc = {
            "trace_id": self.trace_id,
            "case_id": self.case_id,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "seconds": round(time.time() - self.started, 2),
            "model_calls": sum(1 for e in self.events if e["kind"] == "model_call"),
            "tokens": self.tokens(),
            "events": self.events,
            "result": result,
        }
        path = config.TRACES_DIR / f"{self.trace_id}.json"
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

"""Groq calls: retries, JSON replies and the tool loop. Every call is traced."""
import json
import time

import groq

from . import config
from .trace import Trace

_client = None


def client() -> groq.Groq:
    """The groq SDK reads GROQ_API_KEY and GROQ_BASE_URL from the environment.
    It retries rate limits (429) and server errors itself, honouring Retry-After."""
    global _client
    if _client is None:
        _client = groq.Groq(max_retries=6, timeout=120)
    return _client


# Groq rejects a reply it can't parse as a tool call or as JSON; asking again usually works.
RETRYABLE_400 = ("tool_use_failed", "json_validate_failed")


def chat(trace: Trace, agent: str, messages: list, tools: list | None = None, json_mode: bool = False):
    kwargs = {"model": config.MODEL, "messages": messages, "temperature": 0}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    for attempt in range(3):
        start = time.time()
        try:
            resp = client().chat.completions.create(**kwargs)
        except groq.BadRequestError as e:
            if attempt < 2 and any(code in str(e) for code in RETRYABLE_400):
                trace.event(agent, "model reply rejected by Groq, asking again", str(e)[:200])
                continue
            raise
        except groq.NotFoundError as e:
            raise RuntimeError(f"Model {config.MODEL!r} not found on Groq. Set GROQ_MODEL in code/.env.") from e
        trace.model_call(agent, config.MODEL, resp.usage, time.time() - start)
        return resp.choices[0].message
    raise RuntimeError(f"{agent}: Groq rejected the reply three times")


def parse_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the reply")
    return json.loads(text[start:end + 1])


def ask_json(trace: Trace, agent: str, system: str, user: str) -> dict:
    """One JSON reply from a model with no tools. A reply that doesn't parse gets one more try."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(2):
        msg = chat(trace, agent, messages, json_mode=True)
        try:
            return parse_json(msg.content or "")
        except ValueError as e:
            trace.event(agent, "reply was not valid JSON, asking again", str(e))
            messages += [{"role": "assistant", "content": msg.content or ""},
                         {"role": "user", "content": "That was not valid JSON. Reply with the JSON object only."}]
    raise RuntimeError(f"{agent}: no valid JSON after 2 tries")


def run_with_tools(trace: Trace, agent: str, system: str, user: str, tools: dict) -> dict:
    """Let the model call tools until it gives a final JSON answer.

    tools maps name -> (function, JSON schema). Tool errors go back to the model as data."""
    schemas = [{"type": "function", "function": schema} for _, schema in tools.values()]
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for _ in range(config.MAX_TOOL_ROUNDS):
        msg = chat(trace, agent, messages, tools=schemas)
        if not msg.tool_calls:
            try:
                return parse_json(msg.content or "")
            except ValueError as e:
                trace.event(agent, "final answer was not valid JSON, asking again", str(e))
                messages += [{"role": "assistant", "content": msg.content or ""},
                             {"role": "user", "content": "Give your final answer as the JSON object only."}]
                continue
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [{"id": tc.id, "type": "function",
                                         "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                                        for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            name, args = tc.function.name, {}
            try:
                args = json.loads(tc.function.arguments or "{}")
                out = tools[name][0](**args) if name in tools else {"error": f"unknown tool {name}"}
            except Exception as e:  # bad arguments or a failing tool: tell the model, don't crash
                out = {"error": f"{type(e).__name__}: {e}"}
            ok = not (isinstance(out, dict) and "error" in out)
            trace.tool_call(agent, name, args, ok)
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(out, ensure_ascii=False, default=str)})
    raise RuntimeError(f"{agent}: no final answer after {config.MAX_TOOL_ROUNDS} rounds")

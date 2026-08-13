"""Provider-agnostic agent/tool execution for XuniHub.

This module connects a caller-supplied model step to an explicit allowlist of tools.
It deliberately does not instantiate or pretend to have an AI provider, sandbox,
terminal, filesystem, or deployment runtime. Real capabilities must be registered as
adapters by the application.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional


class AgentExecutionError(RuntimeError):
    """Raised when a bounded agent run cannot complete safely."""


ToolHandler = Callable[[Dict[str, Any]], Any]
ModelStep = Callable[[Dict[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class ToolExecutionResult:
    name: str
    ok: bool
    output: Optional[Any] = None
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
        }


class ToolRegistry:
    """Explicit allowlist for tools available to an agent run."""

    def __init__(self) -> None:
        self._handlers: Dict[str, ToolHandler] = {}

    def register(self, name: str, handler: ToolHandler) -> None:
        normalized = name.strip()
        if not normalized:
            raise ValueError("tool name is required")
        if not callable(handler):
            raise TypeError("tool handler must be callable")
        self._handlers[normalized] = handler

    def names(self) -> List[str]:
        return sorted(self._handlers)

    def execute(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> ToolExecutionResult:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolExecutionResult(name=name, ok=False, error="tool is not registered")
        args = arguments or {}
        if not isinstance(args, dict):
            return ToolExecutionResult(name=name, ok=False, error="tool arguments must be an object")
        try:
            return ToolExecutionResult(name=name, ok=True, output=handler(args))
        except Exception as exc:
            return ToolExecutionResult(
                name=name,
                ok=False,
                error="%s: %s" % (exc.__class__.__name__, exc),
            )


@dataclass(frozen=True)
class AgentRunResult:
    final: Any
    steps: int
    transcript: List[Dict[str, Any]]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "final": self.final,
            "steps": self.steps,
            "transcript": self.transcript,
        }


class AgentToolLoop:
    """Bounded model -> tool -> observation loop.

    The model adapter receives an immutable-by-convention context dictionary with the
    objective, caller payload, registered tool names, and transcript so far. A model
    response must contain either `final` or a non-empty `tool_calls` list.
    """

    def __init__(self, model_step: ModelStep, tools: ToolRegistry, *, max_steps: int = 12):
        if not callable(model_step):
            raise TypeError("model_step must be callable")
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self.model_step = model_step
        self.tools = tools
        self.max_steps = int(max_steps)

    @staticmethod
    def _parse_tool_calls(response: Mapping[str, Any]) -> List[Dict[str, Any]]:
        calls = response.get("tool_calls")
        if calls is None:
            return []
        if not isinstance(calls, list) or not calls:
            raise AgentExecutionError("tool_calls must be a non-empty list when present")
        parsed: List[Dict[str, Any]] = []
        for call in calls:
            if not isinstance(call, Mapping):
                raise AgentExecutionError("each tool call must be an object")
            name = call.get("name")
            if not isinstance(name, str) or not name.strip():
                raise AgentExecutionError("each tool call requires a tool name")
            arguments = call.get("arguments", {})
            if not isinstance(arguments, dict):
                raise AgentExecutionError("tool call arguments must be an object")
            parsed.append({"name": name.strip(), "arguments": arguments})
        return parsed

    def run(
        self,
        objective: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        project_id: Optional[str] = None,
    ) -> AgentRunResult:
        if not objective.strip():
            raise ValueError("objective is required")

        transcript: List[Dict[str, Any]] = []
        body = payload or {}
        if not isinstance(body, dict):
            raise TypeError("payload must be an object")

        for step in range(1, self.max_steps + 1):
            context: Dict[str, Any] = {
                "objective": objective,
                "project_id": project_id,
                "payload": body,
                "available_tools": self.tools.names(),
                "transcript": list(transcript),
                "step": step,
                "max_steps": self.max_steps,
            }
            response = self.model_step(context)
            if not isinstance(response, Mapping):
                raise AgentExecutionError("model_step must return an object")

            if "final" in response:
                transcript.append({"type": "final", "step": step, "content": response.get("final")})
                return AgentRunResult(final=response.get("final"), steps=step, transcript=transcript)

            calls = self._parse_tool_calls(response)
            if not calls:
                raise AgentExecutionError("model response must contain final or tool_calls")

            transcript.append({"type": "tool_calls", "step": step, "calls": calls})
            for call in calls:
                result = self.tools.execute(call["name"], call["arguments"])
                transcript.append(
                    {
                        "type": "tool_result",
                        "step": step,
                        "result": result.as_dict(),
                    }
                )

        raise AgentExecutionError("agent exceeded max_steps without producing a final result")


class AgentTaskHandler:
    """TaskExecutor-compatible wrapper around AgentToolLoop.

    Expected payload: {"objective": "...", "project_id": "...", ...}. The wrapper
    returns a JSON-friendly run record that TaskStore can persist as the task result.
    """

    def __init__(self, loop: AgentToolLoop):
        self.loop = loop

    def __call__(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("agent task payload must be an object")
        objective = payload.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("agent task payload requires objective")
        project_id = payload.get("project_id")
        if project_id is not None and not isinstance(project_id, str):
            raise TypeError("project_id must be a string when provided")
        result = self.loop.run(objective, payload=payload, project_id=project_id)
        return result.as_dict()

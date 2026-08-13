import pytest

from superagi.xunihub.agent_execution import (
    AgentExecutionError,
    AgentTaskHandler,
    AgentToolLoop,
    ToolRegistry,
)
from superagi.xunihub.task_runtime import TaskExecutor, TaskStatus, TaskStore


def test_tool_loop_executes_registered_tool_then_finishes():
    tools = ToolRegistry()
    tools.register("add", lambda args: args["a"] + args["b"])

    def model(context):
        results = [item for item in context["transcript"] if item["type"] == "tool_result"]
        if not results:
            return {"tool_calls": [{"name": "add", "arguments": {"a": 7, "b": 3}}]}
        return {"final": {"answer": results[-1]["result"]["output"]}}

    result = AgentToolLoop(model, tools).run("calculate", project_id="project-1")

    assert result.final == {"answer": 10}
    assert result.steps == 2
    assert result.transcript[1]["result"]["ok"] is True


def test_unknown_tool_is_observed_without_execution_and_model_can_recover():
    tools = ToolRegistry()

    def model(context):
        results = [item for item in context["transcript"] if item["type"] == "tool_result"]
        if not results:
            return {"tool_calls": [{"name": "unavailable_tool", "arguments": {"value": 1}}]}
        assert results[-1]["result"]["ok"] is False
        assert results[-1]["result"]["error"] == "tool is not registered"
        return {"final": "blocked unavailable tool"}

    result = AgentToolLoop(model, tools).run("do something")
    assert result.final == "blocked unavailable tool"


def test_tool_exception_is_structured_as_observation():
    tools = ToolRegistry()

    def explode(_args):
        raise RuntimeError("boom")

    tools.register("explode", explode)

    def model(context):
        results = [item for item in context["transcript"] if item["type"] == "tool_result"]
        if not results:
            return {"tool_calls": [{"name": "explode", "arguments": {}}]}
        return {"final": results[-1]["result"]["error"]}

    result = AgentToolLoop(model, tools).run("exercise failure")
    assert result.final == "RuntimeError: boom"


def test_max_steps_is_enforced():
    tools = ToolRegistry()
    tools.register("noop", lambda _args: "ok")

    def model(_context):
        return {"tool_calls": [{"name": "noop", "arguments": {}}]}

    with pytest.raises(AgentExecutionError, match="exceeded max_steps"):
        AgentToolLoop(model, tools, max_steps=2).run("never finish")


def test_agent_task_handler_runs_through_durable_task_executor(tmp_path):
    store = TaskStore(str(tmp_path / "tasks.sqlite3"))
    tools = ToolRegistry()
    tools.register("echo", lambda args: {"echo": args["value"]})

    def model(context):
        results = [item for item in context["transcript"] if item["type"] == "tool_result"]
        if not results:
            return {"tool_calls": [{"name": "echo", "arguments": {"value": "live"}}]}
        return {"final": results[-1]["result"]["output"]}

    executor = TaskExecutor(store)
    executor.register("agent.run", AgentTaskHandler(AgentToolLoop(model, tools)))
    task = store.enqueue(
        "project-1",
        "agent.run",
        {"objective": "echo a value", "project_id": "project-1"},
    )

    finished = executor.run_once("worker-1")

    assert finished is not None
    assert finished.task_id == task.task_id
    assert finished.status == TaskStatus.SUCCEEDED
    assert finished.result["final"] == {"echo": "live"}
    assert finished.result["steps"] == 2


def test_invalid_model_response_fails_durable_task(tmp_path):
    store = TaskStore(str(tmp_path / "tasks.sqlite3"))

    def invalid_model(_context):
        return {"message": "not a valid execution response"}

    executor = TaskExecutor(store)
    executor.register(
        "agent.run",
        AgentTaskHandler(AgentToolLoop(invalid_model, ToolRegistry(), max_steps=1)),
    )
    store.enqueue(
        "project-1",
        "agent.run",
        {"objective": "fail safely", "project_id": "project-1"},
        max_attempts=1,
    )

    finished = executor.run_once("worker-1")

    assert finished is not None
    assert finished.status == TaskStatus.FAILED
    assert "AgentExecutionError" in finished.last_error

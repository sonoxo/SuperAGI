from pathlib import Path

import pytest

from superagi.xunihub.agent_execution import ToolRegistry
from superagi.xunihub.project_tools import LocalWorkspaceTools, WorkspaceToolError


def test_file_tools_round_trip(tmp_path: Path):
    tools = LocalWorkspaceTools(tmp_path)
    tools.write_file({"path": "src/app.txt", "content": "hello xuni"})
    assert tools.read_file({"path": "src/app.txt"})["content"] == "hello xuni"
    entries = tools.list_files({"path": ".", "recursive": True})["entries"]
    assert [item["path"] for item in entries] == ["src", "src/app.txt"]
    assert tools.delete_file({"path": "src/app.txt"})["deleted"] is True


def test_workspace_rejects_path_escape(tmp_path: Path):
    tools = LocalWorkspaceTools(tmp_path)
    with pytest.raises(WorkspaceToolError):
        tools.write_file({"path": "../outside.txt", "content": "nope"})


def test_read_limit(tmp_path: Path):
    tools = LocalWorkspaceTools(tmp_path, max_read_bytes=4)
    (tmp_path / "large.txt").write_text("12345", encoding="utf-8")
    with pytest.raises(WorkspaceToolError):
        tools.read_file({"path": "large.txt"})


def test_registry_registration(tmp_path: Path):
    registry = ToolRegistry()
    tools = LocalWorkspaceTools(tmp_path, include_command=False) if False else LocalWorkspaceTools(tmp_path)
    tools.register(registry, include_command=False)
    assert registry.names() == ["delete_file", "list_files", "read_file", "write_file"]
    result = registry.execute("write_file", {"path": "index.txt", "content": "registered"})
    assert result.ok is True

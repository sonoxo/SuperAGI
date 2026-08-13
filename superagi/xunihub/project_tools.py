"""Safe, real project-tool adapters for XuniHub.

This module provides a filesystem/command adapter that operates inside one explicit
workspace root. It is suitable for CI and local/sandbox runtimes and is intentionally
conservative: paths cannot escape the workspace, commands execute without a shell,
and executable names must be allowlisted by the caller.

It does not create a remote sandbox or claim deployment capability. Production
runtimes can implement the same tool contract behind a stronger isolation boundary.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from superagi.xunihub.agent_execution import ToolRegistry


class WorkspaceToolError(RuntimeError):
    """Raised when a project tool request violates the workspace contract."""


class LocalWorkspaceTools:
    """Filesystem and bounded command tools rooted at one project directory."""

    DEFAULT_MAX_READ_BYTES = 1_000_000

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        allowed_commands: Optional[Iterable[str]] = None,
        command_timeout_seconds: float = 30.0,
        max_read_bytes: int = DEFAULT_MAX_READ_BYTES,
        base_env: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.allowed_commands = frozenset(str(name) for name in (allowed_commands or ()))
        if command_timeout_seconds <= 0:
            raise ValueError("command_timeout_seconds must be positive")
        if max_read_bytes < 1:
            raise ValueError("max_read_bytes must be positive")
        self.command_timeout_seconds = float(command_timeout_seconds)
        self.max_read_bytes = int(max_read_bytes)
        self.base_env = dict(base_env or {})

    def _resolve(self, raw_path: str, *, allow_root: bool = False) -> Path:
        if not isinstance(raw_path, str):
            raise TypeError("path must be a string")
        if "\x00" in raw_path:
            raise WorkspaceToolError("path contains a null byte")
        candidate = (self.root / raw_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceToolError("path escapes workspace root") from exc
        if candidate == self.root and not allow_root:
            raise WorkspaceToolError("operation requires a path below workspace root")
        return candidate

    def list_files(self, args: Dict[str, Any]) -> Dict[str, Any]:
        raw_path = args.get("path", ".")
        target = self._resolve(raw_path, allow_root=True)
        if not target.exists():
            raise FileNotFoundError(raw_path)
        if not target.is_dir():
            raise NotADirectoryError(raw_path)
        recursive = bool(args.get("recursive", False))
        iterator = target.rglob("*") if recursive else target.iterdir()
        entries: List[Dict[str, Any]] = []
        for path in sorted(iterator, key=lambda item: str(item.relative_to(self.root))):
            entries.append(
                {
                    "path": path.relative_to(self.root).as_posix(),
                    "type": "directory" if path.is_dir() else "file",
                    "size": path.stat().st_size if path.is_file() else None,
                }
            )
        return {"entries": entries}

    def read_file(self, args: Dict[str, Any]) -> Dict[str, Any]:
        raw_path = args.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("read_file requires path")
        target = self._resolve(raw_path)
        if not target.is_file():
            raise FileNotFoundError(raw_path)
        size = target.stat().st_size
        if size > self.max_read_bytes:
            raise WorkspaceToolError(
                "file exceeds max_read_bytes (%s > %s)" % (size, self.max_read_bytes)
            )
        encoding = args.get("encoding", "utf-8")
        if encoding != "utf-8":
            raise WorkspaceToolError("only utf-8 text reads are supported")
        return {"path": target.relative_to(self.root).as_posix(), "content": target.read_text(encoding="utf-8")}

    def write_file(self, args: Dict[str, Any]) -> Dict[str, Any]:
        raw_path = args.get("path")
        content = args.get("content")
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("write_file requires path")
        if not isinstance(content, str):
            raise TypeError("write_file content must be a string")
        target = self._resolve(raw_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {
            "path": target.relative_to(self.root).as_posix(),
            "bytes": len(content.encode("utf-8")),
        }

    def delete_file(self, args: Dict[str, Any]) -> Dict[str, Any]:
        raw_path = args.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("delete_file requires path")
        target = self._resolve(raw_path)
        if not target.exists():
            return {"path": target.relative_to(self.root).as_posix(), "deleted": False}
        if not target.is_file() and not target.is_symlink():
            raise WorkspaceToolError("delete_file only deletes files")
        target.unlink()
        return {"path": target.relative_to(self.root).as_posix(), "deleted": True}

    def run_command(self, args: Dict[str, Any]) -> Dict[str, Any]:
        argv = args.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("run_command requires a non-empty argv string list")
        executable = Path(argv[0]).name
        if executable not in self.allowed_commands:
            raise WorkspaceToolError("command is not allowlisted: %s" % executable)
        cwd_raw = args.get("cwd", ".")
        cwd = self._resolve(cwd_raw, allow_root=True)
        if not cwd.is_dir():
            raise NotADirectoryError(cwd_raw)
        requested_timeout = args.get("timeout_seconds", self.command_timeout_seconds)
        timeout = min(float(requested_timeout), self.command_timeout_seconds)
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        env = os.environ.copy()
        env.update(self.base_env)
        extra_env = args.get("env") or {}
        if not isinstance(extra_env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in extra_env.items()):
            raise TypeError("env must be an object of string values")
        env.update(extra_env)
        try:
            completed = subprocess.run(
                argv,
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                shell=False,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceToolError("command timed out after %.2fs" % timeout) from exc
        return {
            "argv": argv,
            "cwd": cwd.relative_to(self.root).as_posix() or ".",
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "ok": completed.returncode == 0,
        }

    def register(self, registry: ToolRegistry, *, include_command: bool = True) -> ToolRegistry:
        """Register this workspace's concrete tools into an agent ToolRegistry."""

        registry.register("list_files", self.list_files)
        registry.register("read_file", self.read_file)
        registry.register("write_file", self.write_file)
        registry.register("delete_file", self.delete_file)
        if include_command:
            registry.register("run_command", self.run_command)
        return registry

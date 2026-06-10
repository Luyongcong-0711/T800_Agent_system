from __future__ import annotations

import fnmatch
import os
import shutil
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.core.settings import Settings, get_settings

READONLY_TOOLS = {"read_file", "list_directory", "file_search"}
WRITE_TOOLS = {"write_file", "copy_file", "move_file", "file_delete"}
READONLY_APPROVAL_POLICY = "outside_local_file_root_read_requires_approval"
SELECTED_WRITE_FILE_TOOLS = [
    "write_file",
    "copy_file",
    "move_file",
    "file_delete",
]
MAX_FILE_SEARCH_RESULTS = 200


class ReadFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(description="File path to read. Relative paths are under the workspace.")


class ListDirectoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dir_path: str = Field(default=".", description="Directory path to list.")


class FileSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(description="Filename glob pattern, for example '*.md'.")
    dir_path: str = Field(default=".", description="Directory path to search from.")


class WriteFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(description="Workspace file path to write.")
    text: str = Field(description="Text to write to the file.")
    append: bool = Field(default=False, description="Whether to append to an existing file.")


class CopyFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(description="Workspace source file path.")
    destination_path: str = Field(description="Workspace destination file path.")


class MoveFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(description="Workspace source file path.")
    destination_path: str = Field(description="Workspace destination file path.")


class DeleteFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(description="Workspace file path to delete.")


def build_local_file_tools(settings: Settings | None = None) -> list[BaseTool]:
    active_settings = settings or get_settings()
    if not active_settings.local_file_tools_enabled:
        return []

    root_dir = Path(active_settings.local_file_tools_root).expanduser().resolve()
    host_root_dir = (
        Path(active_settings.local_file_host_root).expanduser().resolve()
        if active_settings.local_file_host_root
        else None
    )
    root_dir.mkdir(parents=True, exist_ok=True)

    try:
        from langchain_community.agent_toolkits.file_management import FileManagementToolkit
    except ModuleNotFoundError:
        write_tools = _build_fallback_write_file_tools(root_dir)
    else:
        toolkit = FileManagementToolkit(
            root_dir=str(root_dir),
            selected_tools=SELECTED_WRITE_FILE_TOOLS,
        )
        write_tools = toolkit.get_tools()
    tools = [
        *_build_readonly_file_tools(root_dir, host_root_dir),
        *write_tools,
    ]
    for tool in tools:
        _attach_local_file_metadata(tool, root_dir)
    return tools


def _build_readonly_file_tools(
    root_dir: Path,
    host_root_dir: Path | None = None,
) -> list[BaseTool]:
    def read_file(file_path: str) -> str:
        path, _ = _resolve_read_path(file_path, root_dir, host_root_dir)
        if not path.is_file():
            raise ValueError(f"File was not found: {path}")
        return path.read_text(encoding="utf-8", errors="replace")

    def list_directory(dir_path: str = ".") -> str:
        path, display_path = _resolve_read_path(dir_path, root_dir, host_root_dir)
        if not path.is_dir():
            raise ValueError(f"Directory was not found: {path}")
        entries: list[str] = []
        for child in sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            suffix = "/" if child.is_dir() else ""
            entries.append(f"{child.name}{suffix}")
        if not entries:
            return f"Directory: {display_path}\nNo files found."
        return f"Directory: {display_path}\n" + "\n".join(entries)

    def file_search(pattern: str, dir_path: str = ".") -> str:
        if not pattern.strip():
            raise ValueError("Search pattern is required.")
        start, _ = _resolve_read_path(dir_path, root_dir, host_root_dir)
        matches = _search_paths(start, pattern.strip(), max_results=MAX_FILE_SEARCH_RESULTS)
        if not matches:
            return f"No files matched {pattern!r} under {start}."
        suffix = "" if len(matches) < MAX_FILE_SEARCH_RESULTS else "\nResult limit reached."
        return "\n".join(
            _display_read_path(path, host_root_dir) for path in matches
        ) + suffix

    return [
        StructuredTool.from_function(
            func=read_file,
            name="read_file",
            description=(
                "Read a text file. Relative paths are under the configured local workspace. "
                "Absolute paths or paths outside the workspace require user approval."
            ),
            args_schema=ReadFileInput,
        ),
        StructuredTool.from_function(
            func=list_directory,
            name="list_directory",
            description=(
                "List files in a directory. Relative paths are under the configured local workspace. "
                "Absolute paths or paths outside the workspace require user approval."
            ),
            args_schema=ListDirectoryInput,
        ),
        StructuredTool.from_function(
            func=file_search,
            name="file_search",
            description=(
                "Search file and directory names with a glob pattern. Relative paths are under the "
                "configured local workspace. Absolute paths or paths outside the workspace require "
                "user approval."
            ),
            args_schema=FileSearchInput,
        ),
    ]


def _build_fallback_write_file_tools(root_dir: Path) -> list[BaseTool]:
    def write_file(file_path: str, text: str, append: bool = False) -> str:
        path = _resolve_workspace_write_path(file_path, root_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with path.open(mode, encoding="utf-8") as file:
            file.write(text)
        return f"File written: {path}"

    def copy_file(source_path: str, destination_path: str) -> str:
        source = _resolve_workspace_write_path(source_path, root_dir)
        destination = _resolve_workspace_write_path(destination_path, root_dir)
        if not source.is_file():
            raise ValueError(f"Source file was not found: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return f"File copied: {source} -> {destination}"

    def move_file(source_path: str, destination_path: str) -> str:
        source = _resolve_workspace_write_path(source_path, root_dir)
        destination = _resolve_workspace_write_path(destination_path, root_dir)
        if not source.is_file():
            raise ValueError(f"Source file was not found: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return f"File moved: {source} -> {destination}"

    def file_delete(file_path: str) -> str:
        path = _resolve_workspace_write_path(file_path, root_dir)
        if not path.is_file():
            raise ValueError(f"File was not found: {path}")
        path.unlink()
        return f"File deleted: {path}"

    return [
        StructuredTool.from_function(
            func=write_file,
            name="write_file",
            description="Write text to a file under the configured local workspace.",
            args_schema=WriteFileInput,
        ),
        StructuredTool.from_function(
            func=copy_file,
            name="copy_file",
            description="Copy a file under the configured local workspace.",
            args_schema=CopyFileInput,
        ),
        StructuredTool.from_function(
            func=move_file,
            name="move_file",
            description="Move a file under the configured local workspace.",
            args_schema=MoveFileInput,
        ),
        StructuredTool.from_function(
            func=file_delete,
            name="file_delete",
            description="Delete a file under the configured local workspace.",
            args_schema=DeleteFileInput,
        ),
    ]


def _attach_local_file_metadata(tool: BaseTool, root_dir: Path) -> None:
    existing = getattr(tool, "metadata", None)
    metadata: dict[str, Any] = existing if isinstance(existing, dict) else {}
    write_tool = tool.name in WRITE_TOOLS
    readonly_tool = tool.name in READONLY_TOOLS
    tool.metadata = {
        **metadata,
        "source": "langchain_file_management",
        "risk_level": "high" if write_tool else "low",
        "requires_approval": write_tool,
        "local_file_root": str(root_dir),
        **({"approval_policy": READONLY_APPROVAL_POLICY} if readonly_tool else {}),
    }
    if readonly_tool:
        tool.description = (
            f"{tool.description} Paths outside the configured local workspace pause for approval."
        )
    elif write_tool:
        tool.description = (
            f"{tool.description} This modifies files under the configured local file root and "
            "requires user approval before execution."
        )


def _resolve_user_path(raw_path: str, root_dir: Path) -> Path:
    candidate = Path(raw_path or ".").expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (root_dir / candidate).resolve()


def _resolve_read_path(
    raw_path: str,
    root_dir: Path,
    host_root_dir: Path | None,
) -> tuple[Path, str]:
    raw_text = str(raw_path or ".")
    candidate = Path(raw_text).expanduser()
    host_style_absolute = raw_text.startswith(("/", "\\")) and not candidate.is_absolute()
    if not candidate.is_absolute() and not host_style_absolute:
        path = (root_dir / candidate).resolve()
        return path, str(path)

    path = candidate.resolve() if not host_style_absolute else Path(raw_text).resolve()
    if _path_is_inside(path, root_dir):
        return path, str(path)
    if host_root_dir is None:
        return path, str(path)
    if _path_is_inside(path, host_root_dir):
        return path, _display_read_path(path, host_root_dir)
    mapped = (host_root_dir / raw_text.lstrip("/\\")).resolve()
    raw_display = raw_text
    if raw_display.startswith("\\"):
        raw_display = "/" + raw_display.lstrip("\\")
    return mapped, raw_display


def _display_read_path(path: Path, host_root_dir: Path | None) -> str:
    if host_root_dir is None:
        return str(path)
    try:
        relative = path.resolve().relative_to(host_root_dir)
    except ValueError:
        return str(path)
    relative_text = relative.as_posix()
    return "/" if relative_text == "." else f"/{relative_text}"


def _resolve_workspace_write_path(raw_path: str, root_dir: Path) -> Path:
    path = _resolve_user_path(raw_path, root_dir)
    if not _path_is_inside(path, root_dir):
        raise ValueError(f"Write path escapes local workspace: {path}")
    return path


def _path_is_inside(path: Path, root_dir: Path) -> bool:
    try:
        return path == root_dir or path.is_relative_to(root_dir)
    except ValueError:
        return False


def _search_paths(start: Path, pattern: str, *, max_results: int) -> list[Path]:
    if start.is_file():
        return [start] if fnmatch.fnmatch(start.name, pattern) else []
    if not start.is_dir():
        raise ValueError(f"Search directory was not found: {start}")

    matches: list[Path] = []

    def on_error(_: OSError) -> None:
        return None

    for current_root, dir_names, file_names in os.walk(start, onerror=on_error):
        current = Path(current_root)
        for name in sorted([*dir_names, *file_names], key=str.lower):
            if fnmatch.fnmatch(name, pattern):
                matches.append(current / name)
                if len(matches) >= max_results:
                    return matches
    return matches

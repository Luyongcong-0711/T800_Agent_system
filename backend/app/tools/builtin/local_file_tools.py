from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool

from app.core.settings import Settings, get_settings

READONLY_TOOLS = {"read_file", "list_directory", "file_search"}
WRITE_TOOLS = {"write_file", "copy_file", "move_file", "file_delete"}
SELECTED_FILE_TOOLS = [
    "read_file",
    "list_directory",
    "file_search",
    "write_file",
    "copy_file",
    "move_file",
    "file_delete",
]


def build_local_file_tools(settings: Settings | None = None) -> list[BaseTool]:
    active_settings = settings or get_settings()
    if not active_settings.local_file_tools_enabled:
        return []

    root_dir = Path(active_settings.local_file_tools_root).expanduser().resolve()
    root_dir.mkdir(parents=True, exist_ok=True)

    try:
        from langchain_community.agent_toolkits.file_management import FileManagementToolkit
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "langchain-community is required for local file tools."
        ) from exc

    toolkit = FileManagementToolkit(
        root_dir=str(root_dir),
        selected_tools=SELECTED_FILE_TOOLS,
    )
    tools = toolkit.get_tools()
    for tool in tools:
        _attach_local_file_metadata(tool, root_dir)
    return tools


def _attach_local_file_metadata(tool: BaseTool, root_dir: Path) -> None:
    existing = getattr(tool, "metadata", None)
    metadata: dict[str, Any] = existing if isinstance(existing, dict) else {}
    write_tool = tool.name in WRITE_TOOLS
    tool.metadata = {
        **metadata,
        "source": "langchain_file_management",
        "risk_level": "high" if write_tool else "low",
        "requires_approval": write_tool,
        "local_file_root": str(root_dir),
    }
    if tool.name in READONLY_TOOLS:
        tool.description = (
            f"{tool.description} Only paths under the configured local file root are allowed."
        )
    elif write_tool:
        tool.description = (
            f"{tool.description} This modifies files under the configured local file root and "
            "requires user approval before execution."
        )

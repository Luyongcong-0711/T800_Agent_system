from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from app.core.errors import AgentSystemError
from app.runtime.tools import redact_runtime_value
from app.storage.object_store import JsonObjectStore, ObjectStore

MAX_MODEL_STREAM_BYTES = 65536
MAX_RAW_STREAM_BYTES = 1024 * 1024
ALLOWED_RUNTIMES = {None, "python", "python3"}
ALLOWED_IMPORT_MODULES = {
    "datetime",
    "decimal",
    "json",
    "math",
    "re",
    "statistics",
    "typing",
}
FORBIDDEN_IMPORT_MODULES = {
    "ctypes",
    "http",
    "httpx",
    "importlib",
    "multiprocessing",
    "os",
    "pathlib",
    "requests",
    "shutil",
    "socket",
    "ssl",
    "subprocess",
    "sys",
    "urllib",
}
FORBIDDEN_NAMES = {
    "__import__",
    "breakpoint",
    "compile",
    "eval",
    "exec",
    "globals",
    "input",
    "locals",
    "open",
    "vars",
}


class SkillScriptRunner:
    def __init__(self, object_store: ObjectStore) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)

    def validate_entrypoint(self, entrypoint: dict[str, Any]) -> dict[str, Any]:
        runtime = entrypoint.get("runtime")
        if runtime not in ALLOWED_RUNTIMES:
            raise AgentSystemError(
                "skill_script_runtime_unsupported",
                "Skill script runtime is not supported.",
                400,
            )
        script = self._read_verified_script(entrypoint)
        self._validate_ast(script)
        return {
            "ok": True,
            "script_checksum": entrypoint.get("script_checksum"),
            "sandbox_profile": entrypoint.get("sandbox_profile") or "skill_script_readonly",
        }

    def run_readonly(
        self,
        *,
        entrypoint: dict[str, Any],
        args: dict[str, Any],
        artifacts: dict[str, str],
    ) -> dict[str, Any]:
        script = self._read_verified_script(entrypoint)
        self._validate_ast(script)
        timeout_ms = int(entrypoint.get("timeout_ms") or 30000)
        timeout_seconds = max(1, (timeout_ms + 999) // 1000)
        with tempfile.TemporaryDirectory(prefix="agent_skill_") as temp_dir:
            temp_path = Path(temp_dir)
            script_path = temp_path / "skill_script.py"
            input_path = temp_path / "input.json"
            result_path = temp_path / "result.json"
            bootstrap_path = temp_path / "bootstrap.py"
            script_path.write_text(script, encoding="utf-8")
            input_path.write_text(
                json.dumps(redact_runtime_value(args), ensure_ascii=False),
                encoding="utf-8",
            )
            bootstrap_path.write_text(_BOOTSTRAP, encoding="utf-8")
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        str(bootstrap_path),
                        str(script_path),
                        str(input_path),
                        str(result_path),
                    ],
                    capture_output=True,
                    cwd=temp_path,
                    env={"PYTHONIOENCODING": "utf-8"},
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                stdout = self._stream_preview(exc.stdout)
                stderr = self._stream_preview(exc.stderr)
                self._write_stream_artifacts(artifacts, stdout=stdout, stderr=stderr)
                return {
                    "ok": False,
                    "error_type": "skill_script_timeout",
                    "message_for_model": "Skill script timed out and was stopped.",
                    "data": {},
                    "stdout_preview": stdout,
                    "stderr_preview": stderr,
                }

            stdout = self._stream_preview(completed.stdout)
            stderr = self._stream_preview(completed.stderr)
            self._write_stream_artifacts(artifacts, stdout=stdout, stderr=stderr)
            payload = self._read_result_file(result_path)
            if completed.returncode != 0 or not payload.get("ok"):
                return {
                    "ok": False,
                    "error_type": payload.get("error_type") or "skill_script_failed",
                    "message_for_model": (
                        "Skill script failed. Inspect the redacted stderr preview."
                    ),
                    "data": redact_runtime_value(payload.get("data") or {}),
                    "stdout_preview": stdout,
                    "stderr_preview": stderr,
                }
            return {
                "ok": True,
                "error_type": None,
                "message_for_model": "Skill script completed in read-only sandbox.",
                "data": redact_runtime_value(payload.get("data") or {}),
                "stdout_preview": stdout,
                "stderr_preview": stderr,
            }

    def _read_verified_script(self, entrypoint: dict[str, Any]) -> str:
        script_key = entrypoint.get("script_object_key")
        checksum = str(entrypoint.get("script_checksum") or "")
        if not script_key or not checksum.startswith("sha256:"):
            raise AgentSystemError(
                "skill_script_missing",
                "Skill script object or checksum is missing.",
                400,
            )
        script = self.object_store.read_text(str(script_key))
        actual = hashlib.sha256(script.encode("utf-8")).hexdigest()
        if checksum != f"sha256:{actual}":
            raise AgentSystemError(
                "skill_script_checksum_mismatch",
                "Skill script checksum does not match the stored script.",
                409,
            )
        return script

    def _validate_ast(self, script: str) -> None:
        try:
            tree = ast.parse(script)
        except SyntaxError as exc:
            raise AgentSystemError(
                "skill_script_syntax_error",
                "Skill script has invalid Python syntax.",
                400,
            ) from exc
        if not any(
            isinstance(node, ast.FunctionDef) and node.name == "main"
            for node in tree.body
        ):
            raise AgentSystemError(
                "skill_script_missing_main",
                "Skill script must define main(args).",
                400,
            )
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                module_names = [alias.name.split(".", 1)[0] for alias in node.names]
                self._validate_import_modules(module_names)
            elif isinstance(node, ast.ImportFrom):
                self._validate_import_modules([(node.module or "").split(".", 1)[0]])
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
                raise AgentSystemError(
                    "skill_script_forbidden_name",
                    "Skill script uses a forbidden runtime capability.",
                    400,
                )
            elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
                raise AgentSystemError(
                    "skill_script_forbidden_attribute",
                    "Skill script uses a forbidden runtime capability.",
                    400,
                )

    @staticmethod
    def _validate_import_modules(module_names: list[str]) -> None:
        if any(
            name in FORBIDDEN_IMPORT_MODULES or name not in ALLOWED_IMPORT_MODULES
            for name in module_names
        ):
            raise AgentSystemError(
                "skill_script_forbidden_import",
                "Skill script imports a forbidden module.",
                400,
            )

    @staticmethod
    def _stream_preview(value: str | bytes | None) -> str:
        if value is None:
            return ""
        text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
        text = text[:MAX_RAW_STREAM_BYTES]
        redacted = str(redact_runtime_value(text))
        return redacted[:MAX_MODEL_STREAM_BYTES]

    def _write_stream_artifacts(
        self,
        artifacts: dict[str, str],
        *,
        stdout: str,
        stderr: str,
    ) -> None:
        self.object_store.write_text(artifacts["stdout_object_key"], stdout)
        self.object_store.write_text(artifacts["stderr_object_key"], stderr)

    @staticmethod
    def _read_result_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"ok": False, "error_type": "skill_script_no_result", "data": {}}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"ok": False, "error_type": "skill_script_invalid_result", "data": {}}
        return value if isinstance(value, dict) else {"ok": False, "data": {}}


_BOOTSTRAP = textwrap.dedent(
    r'''
    import json
    import sys

    ALLOWED_IMPORT_MODULES = {
        "datetime",
        "decimal",
        "json",
        "math",
        "re",
        "statistics",
        "typing",
    }

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = name.split(".", 1)[0]
        if level != 0 or root not in ALLOWED_IMPORT_MODULES:
            raise ImportError("Import is not allowed in skill scripts.")
        return __import__(name, globals, locals, fromlist, level)

    SAFE_BUILTINS = {
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "Exception": Exception,
        "float": float,
        "int": int,
        "isinstance": isinstance,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "print": print,
        "range": range,
        "round": round,
        "RuntimeError": RuntimeError,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "ValueError": ValueError,
        "__import__": safe_import,
    }

    script_path, input_path, result_path = sys.argv[1:4]
    try:
        code = open(script_path, "r", encoding="utf-8").read()
        args = json.loads(open(input_path, "r", encoding="utf-8").read())
        globals_dict = {"__builtins__": SAFE_BUILTINS}
        exec(compile(code, "skill_script.py", "exec"), globals_dict, globals_dict)
        main = globals_dict.get("main")
        if not callable(main):
            raise RuntimeError("Skill script must define main(args).")
        result = main(args)
        with open(result_path, "w", encoding="utf-8") as handle:
            json.dump({"ok": True, "data": result}, handle, ensure_ascii=False)
    except Exception as exc:
        with open(result_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "ok": False,
                    "error_type": "skill_script_failed",
                    "data": {
                        "exception_type": exc.__class__.__name__,
                        "message": str(exc)[:500],
                    },
                },
                handle,
                ensure_ascii=False,
            )
        raise
    '''
).strip()

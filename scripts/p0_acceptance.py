from __future__ import annotations

import argparse
import json
import locale
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


PASS = "pass"
FAIL = "fail"
SKIPPED = "skipped"
ACCEPTANCE_REPORT_SCHEMA_VERSION = 2
FINAL_HANDOFF_CONTRACT_VERSION = 3
FINAL_HANDOFF_CONTRACT_ID = "p0-final-handoff-2026-06-01"

FRONTEND_WORKSPACE_ROUTES: tuple[tuple[str, str], ...] = (
    ("/", "Chat"),
    ("/jobs", "Jobs"),
    ("/knowledge", "Knowledge"),
    ("/memory", "Memory"),
    ("/skills", "Skills"),
    ("/subagents", "SubAgents"),
    ("/mcp", "MCP Tools"),
    ("/logs", "Logs"),
    ("/readiness", "P0 Readiness"),
    ("/settings", "Settings"),
    ("/settings?tab=models", "Settings Model APIs"),
    ("/settings?tab=databases", "Settings Databases"),
    ("/settings?tab=secrets", "Settings Secrets"),
)

FRONTEND_ROUTE_RENDER_MARKERS: dict[str, tuple[str, ...]] = {
    "/": ("Agent System", "Chat runtime"),
    "/jobs": ("Agent System", "Jobs"),
    "/knowledge": ("Agent System", "Knowledge"),
    "/memory": ("Agent System", "Memory"),
    "/skills": ("Agent System", "Skills"),
    "/subagents": ("Agent System", "SubAgents"),
    "/mcp": ("Agent System", "MCP"),
    "/logs": ("Agent System", "Logs"),
    "/readiness": ("Agent System", "P0 readiness"),
    "/settings": ("Agent System", "Settings", "Model APIs"),
    "/settings?tab=models": ("Agent System", "Settings", "Model APIs"),
    "/settings?tab=databases": ("Agent System", "Settings", "Databases"),
    "/settings?tab=secrets": ("Agent System", "Settings", "Secrets"),
}

FINAL_HANDOFF_CHECK_IDS: tuple[str, ...] = (
    "code.backend_python_env",
    "code.root_e2e_contracts",
    "code.backend_p0_contracts",
    "code.frontend_p0_contracts",
    "runtime.model_config.main_chat_smoke",
    "runtime.model_config.graphrag_llm_smoke",
    "runtime.model_config.embedding_smoke",
    "runtime.docker_compose_ps",
    "runtime.database_live_health",
    "runtime.job_worker_status",
    "runtime.mcp_live_smoke",
    "runtime.frontend_route_smoke",
    "runtime.frontend_browser_smoke",
    "runtime.p0_readiness_after_report",
)

FINAL_HANDOFF_REQUIRED_FLAGS: tuple[str, ...] = (
    "--include-root-e2e",
    "--include-p0-contracts",
    "--include-runtime-http",
    "--include-model-smoke",
    "--include-docker",
    "--mcp-server-name",
    "--require-final-handoff",
)


@dataclass
class CheckResult:
    check_id: str
    status: str
    summary: str
    duration_ms: int = 0
    command: list[str] | None = None
    cwd: str | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    next_action: str = ""


def py313_next_action() -> str:
    return (
        "Activate the Miniconda py313 environment or set AGENT_BACKEND_PYTHON "
        "to the py313 python.exe path before running acceptance."
    )


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def tail_text(value: str, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


def ensure_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode(locale.getpreferredencoding(False), errors="replace")
    return value


def command_available(command: str) -> bool:
    return shutil.which(command) is not None


def conda_py313_python_candidates() -> list[Path]:
    candidates: list[Path] = []
    conda_prefix = os.getenv("CONDA_PREFIX")
    if conda_prefix:
        prefix = Path(conda_prefix)
        candidates.extend([prefix / "python.exe", prefix / "bin" / "python"])

    roots: list[Path] = []
    for env_name in ("CONDA_EXE", "_CONDA_EXE", "MAMBA_EXE"):
        conda_exe = os.getenv(env_name)
        if not conda_exe:
            continue
        for parent in Path(conda_exe).parents:
            if parent.name.lower() in {"miniconda", "miniconda3", "anaconda", "anaconda3"}:
                roots.append(parent)
                break

    roots.extend(
        [
            Path.home() / ".conda",
            Path.home() / "miniconda3",
            Path.home() / "Miniconda3",
            Path.home() / "anaconda3",
            Path.home() / "Anaconda3",
            Path("C:/ProgramData/miniconda3"),
            Path("C:/ProgramData/Miniconda3"),
            Path("C:/ProgramData/anaconda3"),
            Path("C:/ProgramData/Anaconda3"),
            Path("C:/miniconda3"),
            Path("C:/Miniconda3"),
            Path("D:/all-app/code-app/miniconda"),
            Path("D:/all-app/code-app/miniconda3"),
            Path("D:/all-app/code-app/Miniconda3"),
        ]
    )

    seen: set[str] = set()
    for root in roots:
        env_dir = root / "envs" / "py313"
        for candidate in (env_dir / "python.exe", env_dir / "bin" / "python"):
            key = str(candidate).lower()
            if key not in seen:
                candidates.append(candidate)
                seen.add(key)
    return candidates


def backend_python(root: Path) -> str:
    configured = os.getenv("AGENT_BACKEND_PYTHON")
    if configured and Path(configured).exists():
        return configured

    if os.getenv("CONDA_DEFAULT_ENV") == "py313":
        return sys.executable

    for candidate in conda_py313_python_candidates():
        if candidate.exists() and "py313" in {part.lower() for part in candidate.parts}:
            return str(candidate)
    return sys.executable


def backend_python_env_check(root: Path, timeout_seconds: int) -> CheckResult:
    python = backend_python(root)
    return run_command(
        "code.backend_python_env",
        [
            python,
            "-c",
            (
                "import os,sys;"
                "from pathlib import Path;"
                "exe=Path(sys.executable);"
                "is_py313_env=(os.getenv('CONDA_DEFAULT_ENV')=='py313' "
                "or exe.parent.name.lower()=='py313' "
                "or (len(exe.parents)>1 and exe.parents[1].name.lower()=='py313'));"
                "print(sys.executable);"
                "print(sys.version);"
                "print('CONDA_DEFAULT_ENV=' + str(os.getenv('CONDA_DEFAULT_ENV')));"
                "print('IS_PY313_ENV=' + str(is_py313_env));"
                "raise SystemExit(0 if sys.version_info[:2] >= (3, 13) and is_py313_env else 1)"
            ),
        ],
        root,
        timeout_seconds,
        next_action=py313_next_action(),
    )


def skipped_for_backend_python_env(check_id: str) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        status=SKIPPED,
        summary="Skipped because the selected backend Python is not the py313/Python 3.13 environment.",
        next_action=py313_next_action(),
    )


def pnpm_command() -> str:
    if platform.system().lower().startswith("win"):
        return "pnpm.cmd"
    return "pnpm"


def run_command(
    check_id: str,
    command: Sequence[str],
    cwd: Path,
    timeout_seconds: int,
    *,
    next_action: str,
) -> CheckResult:
    started = time.perf_counter()
    if shutil.which(command[0]) is None and not Path(command[0]).exists():
        return CheckResult(
            check_id=check_id,
            status=SKIPPED,
            summary=f"Command not found: {command[0]}",
            command=list(command),
            cwd=str(cwd),
            next_action=next_action,
        )
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(cwd),
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return CheckResult(
            check_id=check_id,
            status=FAIL,
            summary=f"Timed out after {timeout_seconds}s.",
            duration_ms=int((time.perf_counter() - started) * 1000),
            command=list(command),
            cwd=str(cwd),
            stdout_tail=tail_text(ensure_text(exc.stdout)),
            stderr_tail=tail_text(ensure_text(exc.stderr)),
            next_action=next_action,
        )
    status = PASS if completed.returncode == 0 else FAIL
    return CheckResult(
        check_id=check_id,
        status=status,
        summary="Command passed." if status == PASS else f"Command failed: {completed.returncode}",
        duration_ms=int((time.perf_counter() - started) * 1000),
        command=list(command),
        cwd=str(cwd),
        stdout_tail=tail_text(ensure_text(completed.stdout)),
        stderr_tail=tail_text(ensure_text(completed.stderr)),
        next_action="" if status == PASS else next_action,
    )


def http_check(
    check_id: str,
    url: str,
    timeout_seconds: int,
    *,
    expect_json: bool = True,
    required_keys: Sequence[str] = (),
    expected_values: dict[str, Any] | None = None,
    minimum_values: dict[str, int | float] | None = None,
) -> CheckResult:
    started = time.perf_counter()
    try:
        request = Request(url, headers={"Accept": "application/json" if expect_json else "*/*"})
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
            status_code = response.status
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return CheckResult(
            check_id=check_id,
            status=FAIL,
            summary=f"HTTP request failed: status {exc.code}",
            duration_ms=int((time.perf_counter() - started) * 1000),
            stdout_tail=tail_text(body),
            next_action="Inspect the service response body and logs, then rerun this check.",
        )
    except URLError as exc:
        return CheckResult(
            check_id=check_id,
            status=FAIL,
            summary=f"HTTP request failed: {exc.reason}",
            duration_ms=int((time.perf_counter() - started) * 1000),
            next_action="Start the backend/frontend service, then rerun with runtime HTTP checks.",
        )
    except Exception as exc:  # noqa: BLE001 - acceptance reports the boundary failure.
        return CheckResult(
            check_id=check_id,
            status=FAIL,
            summary=f"HTTP request failed: {exc.__class__.__name__}",
            duration_ms=int((time.perf_counter() - started) * 1000),
            next_action="Inspect the service logs and rerun this check.",
        )
    if status_code < 200 or status_code >= 300:
        return CheckResult(
            check_id=check_id,
            status=FAIL,
            summary=f"Unexpected HTTP status: {status_code}",
            duration_ms=int((time.perf_counter() - started) * 1000),
            stdout_tail=tail_text(body),
            next_action="Inspect the service response and logs.",
        )
    if expect_json:
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return CheckResult(
                check_id=check_id,
                status=FAIL,
                summary="Response is not valid JSON.",
                duration_ms=int((time.perf_counter() - started) * 1000),
                stdout_tail=tail_text(body),
                next_action="Check the API route response model.",
            )
        missing = [key for key in required_keys if key not in parsed]
        if missing:
            return CheckResult(
                check_id=check_id,
                status=FAIL,
                summary=f"Missing response keys: {', '.join(missing)}",
                duration_ms=int((time.perf_counter() - started) * 1000),
                stdout_tail=tail_text(body),
                next_action="Check the API contract and frontend schema.",
            )
        mismatched = [
            f"{key}={parsed.get(key)!r}"
            for key, expected in (expected_values or {}).items()
            if parsed.get(key) != expected
        ]
        if mismatched:
            return CheckResult(
                check_id=check_id,
                status=FAIL,
                summary=f"Unexpected response values: {', '.join(mismatched)}",
                duration_ms=int((time.perf_counter() - started) * 1000),
                stdout_tail=tail_text(body),
                next_action="Open the P0 Readiness page and resolve required blockers.",
            )
        below_minimum = [
            f"{key}={parsed.get(key)!r} < {minimum}"
            for key, minimum in (minimum_values or {}).items()
            if not isinstance(parsed.get(key), int | float) or parsed.get(key) < minimum
        ]
        if below_minimum:
            return CheckResult(
                check_id=check_id,
                status=FAIL,
                summary=f"Response values below minimum: {', '.join(below_minimum)}",
                duration_ms=int((time.perf_counter() - started) * 1000),
                stdout_tail=tail_text(body),
                next_action="Configure and refresh the target service before final acceptance.",
            )
    return CheckResult(
        check_id=check_id,
        status=PASS,
        summary=f"HTTP {status_code} from {url}",
        duration_ms=int((time.perf_counter() - started) * 1000),
        stdout_tail=tail_text(body, 1000),
    )


def http_json_request_check(
    check_id: str,
    url: str,
    timeout_seconds: int,
    *,
    body: dict[str, Any],
    method: str = "POST",
    required_keys: Sequence[str] = (),
    require_ok_field: bool = False,
    next_action: str,
) -> CheckResult:
    started = time.perf_counter()
    try:
        request = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method=method,
        )
        with urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            status_code = response.status
    except HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        return CheckResult(
            check_id=check_id,
            status=FAIL,
            summary=f"HTTP request failed: status {exc.code}",
            duration_ms=int((time.perf_counter() - started) * 1000),
            stdout_tail=tail_text(response_body),
            next_action=next_action,
        )
    except URLError as exc:
        return CheckResult(
            check_id=check_id,
            status=FAIL,
            summary=f"HTTP request failed: {exc.reason}",
            duration_ms=int((time.perf_counter() - started) * 1000),
            next_action=next_action,
        )
    except Exception as exc:  # noqa: BLE001 - acceptance reports the boundary failure.
        return CheckResult(
            check_id=check_id,
            status=FAIL,
            summary=f"HTTP request failed: {exc.__class__.__name__}",
            duration_ms=int((time.perf_counter() - started) * 1000),
            next_action=next_action,
        )
    if status_code < 200 or status_code >= 300:
        return CheckResult(
            check_id=check_id,
            status=FAIL,
            summary=f"Unexpected HTTP status: {status_code}",
            duration_ms=int((time.perf_counter() - started) * 1000),
            stdout_tail=tail_text(response_body),
            next_action=next_action,
        )
    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError:
        return CheckResult(
            check_id=check_id,
            status=FAIL,
            summary="Response is not valid JSON.",
            duration_ms=int((time.perf_counter() - started) * 1000),
            stdout_tail=tail_text(response_body),
            next_action=next_action,
        )
    missing = [key for key in required_keys if key not in parsed]
    if missing:
        return CheckResult(
            check_id=check_id,
            status=FAIL,
            summary=f"Missing response keys: {', '.join(missing)}",
            duration_ms=int((time.perf_counter() - started) * 1000),
            stdout_tail=tail_text(response_body),
            next_action=next_action,
        )
    if require_ok_field and parsed.get("ok") is not True:
        return CheckResult(
            check_id=check_id,
            status=FAIL,
            summary=f"Runtime smoke returned ok={parsed.get('ok')!r}.",
            duration_ms=int((time.perf_counter() - started) * 1000),
            stdout_tail=tail_text(response_body),
            next_action=next_action,
        )
    return CheckResult(
        check_id=check_id,
        status=PASS,
        summary=f"HTTP {status_code} from {url}",
        duration_ms=int((time.perf_counter() - started) * 1000),
        stdout_tail=tail_text(response_body, 1000),
    )


def frontend_route_smoke(
    frontend_url: str,
    timeout_seconds: int,
) -> CheckResult:
    started = time.perf_counter()
    base_url = frontend_url.rstrip("/")
    route_results: list[str] = []
    failures: list[str] = []
    for path, title in FRONTEND_WORKSPACE_ROUTES:
        url = f"{base_url}{path}"
        try:
            request = Request(url, headers={"Accept": "text/html,*/*"})
            with urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                status_code = response.status
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            failures.append(f"{path}: HTTP {exc.code}")
            route_results.append(
                f"FAIL {path} {title}: HTTP {exc.code}, body={tail_text(body, 500)}"
            )
            continue
        except URLError as exc:
            reason = str(getattr(exc, "reason", exc))
            failures.append(f"{path}: request failed: {reason}")
            route_results.append(f"FAIL {path} {title}: request failed: {reason}")
            continue
        except Exception as exc:  # noqa: BLE001 - smoke reports the boundary failure.
            failures.append(f"{path}: request failed: {exc.__class__.__name__}")
            route_results.append(
                f"FAIL {path} {title}: request failed: {exc.__class__.__name__}"
            )
            continue
        body_size = len(body.encode("utf-8", errors="replace"))
        if status_code < 200 or status_code >= 300:
            failures.append(f"{path}: HTTP {status_code}")
            route_results.append(f"FAIL {path} {title}: HTTP {status_code}, bytes={body_size}")
            continue
        if body_size < 128:
            failures.append(f"{path}: response body too small ({body_size} bytes)")
            route_results.append(f"FAIL {path} {title}: HTTP {status_code}, bytes={body_size}")
            continue
        route_results.append(f"PASS {path} {title}: HTTP {status_code}, bytes={body_size}")
    status = PASS if not failures else FAIL
    passed_count = len(FRONTEND_WORKSPACE_ROUTES) - len(failures)
    return CheckResult(
        check_id="runtime.frontend_route_smoke",
        status=status,
        summary=(
            f"Frontend workspace route smoke passed: {passed_count}/"
            f"{len(FRONTEND_WORKSPACE_ROUTES)} routes."
            if status == PASS
            else (
                f"Frontend workspace route smoke failed: {len(failures)} route(s) failed."
            )
        ),
        duration_ms=int((time.perf_counter() - started) * 1000),
        stdout_tail=tail_text("\n".join(route_results), 4000),
        next_action=""
        if status == PASS
        else "Start the frontend service, inspect failed routes, then rerun runtime HTTP checks.",
    )


def frontend_route_render_markers(path: str, title: str) -> tuple[str, ...]:
    return FRONTEND_ROUTE_RENDER_MARKERS.get(path, ("Agent System", title))


def missing_render_markers(dom: str, path: str, title: str) -> list[str]:
    normalized_dom = " ".join(dom.casefold().split())
    return [
        marker
        for marker in frontend_route_render_markers(path, title)
        if " ".join(marker.casefold().split()) not in normalized_dom
    ]


def browser_command_candidates() -> list[str]:
    candidates = [
        "msedge",
        "msedge.exe",
        "google-chrome",
        "google-chrome-stable",
        "chrome",
        "chrome.exe",
        "chromium",
        "chromium-browser",
    ]
    if platform.system().lower().startswith("win"):
        candidates.extend(
            [
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ]
        )
    return candidates


def find_browser_command() -> str | None:
    for candidate in browser_command_candidates():
        path = Path(candidate)
        if path.exists():
            return str(path)
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def frontend_browser_smoke(
    frontend_url: str,
    timeout_seconds: int,
) -> CheckResult:
    started = time.perf_counter()
    browser = find_browser_command()
    if not browser:
        return CheckResult(
            check_id="runtime.frontend_browser_smoke",
            status=SKIPPED,
            summary="No supported headless browser command was found.",
            next_action=(
                "Install Microsoft Edge, Chrome, or Chromium and rerun final acceptance."
            ),
        )
    base_url = frontend_url.rstrip("/")
    route_results: list[str] = []
    failures: list[str] = []
    per_route_timeout = max(5, min(timeout_seconds, 30))
    for path, title in FRONTEND_WORKSPACE_ROUTES:
        url = f"{base_url}{path}"
        command = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--dump-dom",
            url,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=per_route_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            failures.append(f"{path}: browser timed out")
            route_results.append(
                f"FAIL {path} {title}: timeout, stdout={tail_text(ensure_text(exc.stdout), 300)}"
            )
            continue
        stdout = ensure_text(completed.stdout)
        stderr = ensure_text(completed.stderr)
        body_size = len(stdout.encode("utf-8", errors="replace"))
        lowered = stdout.lower()
        if completed.returncode != 0:
            failures.append(f"{path}: browser exit {completed.returncode}")
            route_results.append(
                f"FAIL {path} {title}: exit={completed.returncode}, stderr={tail_text(stderr, 500)}"
            )
            continue
        if body_size < 256:
            failures.append(f"{path}: rendered DOM too small ({body_size} bytes)")
            route_results.append(f"FAIL {path} {title}: bytes={body_size}")
            continue
        if "application error" in lowered or "internal server error" in lowered:
            failures.append(f"{path}: rendered error page")
            route_results.append(
                f"FAIL {path} {title}: rendered error page, bytes={body_size}"
            )
            continue
        missing_markers = missing_render_markers(stdout, path, title)
        if missing_markers:
            failures.append(f"{path}: missing rendered markers")
            route_results.append(
                f"FAIL {path} {title}: missing_markers={missing_markers}, bytes={body_size}"
            )
            continue
        route_results.append(
            f"PASS {path} {title}: browser_rendered, markers=ok, bytes={body_size}"
        )
    status = PASS if not failures else FAIL
    passed_count = len(FRONTEND_WORKSPACE_ROUTES) - len(failures)
    return CheckResult(
        check_id="runtime.frontend_browser_smoke",
        status=status,
        summary=(
            f"Frontend browser smoke passed: {passed_count}/"
            f"{len(FRONTEND_WORKSPACE_ROUTES)} routes."
            if status == PASS
            else f"Frontend browser smoke failed: {len(failures)} route(s) failed."
        ),
        duration_ms=int((time.perf_counter() - started) * 1000),
        command=[browser, "--headless=new", "--dump-dom", "<route>"],
        stdout_tail=tail_text("\n".join(route_results), 4000),
        next_action=""
        if status == PASS
        else (
            "Start the frontend service, inspect browser-rendered failed routes, "
            "then rerun final acceptance."
        ),
    )


def code_checks(root: Path, timeout_seconds: int) -> list[CheckResult]:
    python = backend_python(root)
    pnpm = pnpm_command()
    backend = root / "backend"
    frontend = root / "frontend"
    env_check = backend_python_env_check(root, timeout_seconds)
    results = [env_check]
    if env_check.status == PASS:
        results.extend(
            [
                run_command(
                    "code.backend_py_compile",
                    [
                        python,
                        "-m",
                        "py_compile",
                        "app/api/health.py",
                        "app/core/settings.py",
                        "app/mcp/service.py",
                        "app/mcp/configured_provider.py",
                        "app/mcp_client/invocation.py",
                        "app/schemas/health.py",
                        "app/secret_store/secret_resolver.py",
                        "app/secret_store/secret_service.py",
                    ],
                    backend,
                    timeout_seconds,
                    next_action="Fix backend syntax/type import errors before acceptance.",
                ),
                run_command(
                    "code.acceptance_helper_compile",
                    [python, "-m", "py_compile", "scripts/p0_acceptance.py"],
                    root,
                    timeout_seconds,
                    next_action="Fix acceptance helper syntax/import errors before final acceptance.",
                ),
                run_command(
                    "code.backend_health_tests",
                    [python, "-m", "pytest", "tests/unit/test_health.py"],
                    backend,
                    timeout_seconds,
                    next_action="Fix readiness/health endpoint regressions.",
                ),
            ]
        )
    else:
        results.extend(
            [
                skipped_for_backend_python_env("code.backend_py_compile"),
                skipped_for_backend_python_env("code.acceptance_helper_compile"),
                skipped_for_backend_python_env("code.backend_health_tests"),
            ]
        )
    results.extend(
        [
            run_command(
                "code.frontend_typecheck",
                [pnpm, "exec", "tsc", "--noEmit", "--pretty", "false"],
                frontend,
                timeout_seconds,
                next_action="Fix TypeScript contract drift.",
            ),
            run_command(
                "code.frontend_readiness_tests",
                [
                    pnpm,
                    "exec",
                    "vitest",
                    "run",
                    "tests/unit/workspaceRoutes.test.ts",
                    "tests/unit/WorkspaceShellRoutes.test.tsx",
                    "tests/unit/agentApiClient.test.ts",
                    "tests/unit/P0ReadinessPanel.test.tsx",
                ],
                frontend,
                timeout_seconds,
                next_action="Fix workspace route or API adapter regressions.",
            ),
        ]
    )
    return results


def root_e2e_checks(root: Path, timeout_seconds: int) -> list[CheckResult]:
    python = backend_python(root)
    env_check = backend_python_env_check(root, timeout_seconds)
    if env_check.status != PASS:
        return [skipped_for_backend_python_env("code.root_e2e_contracts")]
    return [
        run_command(
            "code.root_e2e_contracts",
            [python, "-m", "pytest", "tests/e2e"],
            root,
            timeout_seconds,
            next_action="Fix cross-service API contract regressions.",
        )
    ]


def p0_contract_checks(root: Path, timeout_seconds: int) -> list[CheckResult]:
    python = backend_python(root)
    pnpm = pnpm_command()
    backend = root / "backend"
    frontend = root / "frontend"
    backend_contracts = [
        "tests/integration/test_threads_runs_events_api.py",
        "tests/integration/test_jobs_api.py",
        "tests/integration/test_secrets_api.py",
        "tests/integration/test_model_config_api.py",
        "tests/integration/test_database_config_api.py",
        "tests/integration/test_database_health.py",
        "tests/integration/test_phase_g_documents_api_contract.py",
        "tests/integration/test_phase_h_graph_build_contract.py",
        "tests/integration/test_graph_query_api.py",
        "tests/integration/test_phase_i_mcp_api_contract.py",
        "tests/integration/test_phase_j_memory_api_contract.py",
        "tests/integration/test_phase_k_skill_api_contract.py",
        "tests/integration/test_phase_l_subagent_api_contract.py",
        "tests/integration/test_phase_m_diagnostics_api_contract.py",
    ]
    frontend_contracts = [
        "tests/unit/ChatPanel.test.tsx",
        "tests/unit/JobTaskCenter.test.tsx",
        "tests/unit/KnowledgePanel.test.tsx",
        "tests/unit/McpToolsPanel.test.tsx",
        "tests/unit/MemoryPanel.test.tsx",
        "tests/unit/ModelConfigSettings.test.tsx",
        "tests/unit/DatabaseSettingsPanel.test.tsx",
        "tests/unit/SecretsPanel.test.tsx",
        "tests/unit/SkillsPanel.test.tsx",
        "tests/unit/SubAgentsPanel.test.tsx",
        "tests/unit/SystemLogsPanel.test.tsx",
        "tests/unit/jobEventStream.test.ts",
    ]
    env_check = backend_python_env_check(root, timeout_seconds)
    backend_result = (
        run_command(
            "code.backend_p0_contracts",
            [python, "-m", "pytest", *backend_contracts],
            backend,
            timeout_seconds,
            next_action="Fix backend P0 API/runtime contract regressions.",
        )
        if env_check.status == PASS
        else skipped_for_backend_python_env("code.backend_p0_contracts")
    )
    return [
        backend_result,
        run_command(
            "code.frontend_p0_contracts",
            [pnpm, "exec", "vitest", "run", *frontend_contracts],
            frontend,
            timeout_seconds,
            next_action="Fix frontend P0 workflow and API adapter regressions.",
        ),
    ]


def runtime_http_checks(
    backend_url: str,
    frontend_url: str,
    timeout_seconds: int,
    *,
    workspace_id: str,
    mcp_server_name: str | None,
) -> list[CheckResult]:
    backend_url = backend_url.rstrip("/")
    frontend_url = frontend_url.rstrip("/")
    workspace_segment = quote(workspace_id, safe="")
    results = [
        http_check(
            "runtime.backend_health",
            f"{backend_url}/health",
            timeout_seconds,
            required_keys=("ok", "service", "environment"),
        ),
        http_check(
            "runtime.bootstrap",
            f"{backend_url}/bootstrap",
            timeout_seconds,
            required_keys=("user", "workspace", "feature_flags"),
        ),
        http_check(
            "runtime.p0_readiness",
            f"{backend_url}/workspaces/{workspace_segment}/readiness",
            timeout_seconds,
            required_keys=("workspace_id", "summary", "categories", "checks"),
        ),
        http_json_request_check(
            "runtime.database_live_health",
            f"{backend_url}/workspaces/{workspace_segment}/database/health/check",
            timeout_seconds,
            body={},
            required_keys=("ok", "workspace_id", "services", "source"),
            require_ok_field=True,
            next_action=(
                "Fix Settings -> Databases, MinIO/Milvus/Neo4j/Redis connectivity, "
                "or service credentials before final acceptance."
            ),
        ),
        http_check(
            "runtime.job_worker_status",
            f"{backend_url}/workspaces/{workspace_segment}/jobs/worker/status",
            timeout_seconds,
            required_keys=("workspace_id", "running"),
            expected_values={"running": True},
        ),
        http_check(
            "runtime.frontend_http",
            frontend_url,
            timeout_seconds,
            expect_json=False,
        ),
    ]
    if mcp_server_name:
        server_segment = quote(mcp_server_name, safe="")
        results.append(
            http_check(
                "runtime.mcp_live_smoke",
                (
                    f"{backend_url}/workspaces/{workspace_segment}/mcp/servers/"
                    f"{server_segment}/health?live_probe=true"
                ),
                timeout_seconds,
                required_keys=("server_name", "status", "transport", "runtime_configured"),
                expected_values={"runtime_configured": True, "connected": True},
                minimum_values={"tool_count": 1},
            )
        )
    else:
        results.append(
            CheckResult(
                check_id="runtime.mcp_live_smoke",
                status=SKIPPED,
                summary="No MCP server name was provided.",
                next_action="Rerun with --mcp-server-name after configuring a real MCP server.",
            )
        )
    results.append(frontend_route_smoke(frontend_url, timeout_seconds))
    results.append(frontend_browser_smoke(frontend_url, timeout_seconds))
    return results


def readiness_after_report_check(
    backend_url: str,
    timeout_seconds: int,
    *,
    workspace_id: str,
) -> CheckResult:
    backend_url = backend_url.rstrip("/")
    workspace_segment = quote(workspace_id, safe="")
    return http_check(
        "runtime.p0_readiness_after_report",
        f"{backend_url}/workspaces/{workspace_segment}/readiness",
        timeout_seconds,
        required_keys=("ok", "workspace_id", "summary", "categories", "checks"),
        expected_values={"ok": True},
    )


def runtime_model_smoke_checks(
    backend_url: str,
    timeout_seconds: int,
    *,
    workspace_id: str,
) -> list[CheckResult]:
    backend_url = backend_url.rstrip("/")
    workspace_segment = quote(workspace_id, safe="")
    smoke_body = {
        "max_output_tokens": 16,
        "prompt": "Reply with exactly: pong",
    }
    return [
        http_json_request_check(
            "runtime.model_config.main_chat_smoke",
            f"{backend_url}/workspaces/{workspace_segment}/model-configs/main_chat/test",
            timeout_seconds,
            body=smoke_body,
            required_keys=("ok", "provider", "model", "latency_ms", "redacted"),
            require_ok_field=True,
            next_action=(
                "Fix Settings -> Models -> main_chat, secret refs, or provider connectivity."
            ),
        ),
        http_json_request_check(
            "runtime.model_config.graphrag_llm_smoke",
            f"{backend_url}/workspaces/{workspace_segment}/model-configs/graphrag_llm/test",
            timeout_seconds,
            body=smoke_body,
            required_keys=("ok", "provider", "model", "latency_ms", "redacted"),
            require_ok_field=True,
            next_action=(
                "Fix Settings -> Models -> graphrag_llm, secret refs, or provider connectivity."
            ),
        ),
        http_json_request_check(
            "runtime.model_config.embedding_smoke",
            f"{backend_url}/workspaces/{workspace_segment}/model-configs/embedding/test",
            timeout_seconds,
            body=smoke_body,
            required_keys=("ok", "provider", "model", "latency_ms", "redacted"),
            require_ok_field=True,
            next_action=(
                "Fix Settings -> Models -> embedding, embedding secret refs, or provider "
                "connectivity."
            ),
        ),
    ]


def docker_checks(root: Path, timeout_seconds: int) -> list[CheckResult]:
    command = "docker"
    if not command_available(command):
        return [
            CheckResult(
                check_id="runtime.docker_compose_ps",
                status=SKIPPED,
                summary="Docker command is not available.",
                next_action="Install/start Docker Desktop before final P0 acceptance.",
            )
        ]
    return [docker_compose_health_check(root, timeout_seconds)]


def docker_compose_health_check(root: Path, timeout_seconds: int) -> CheckResult:
    started = time.perf_counter()
    command = ["docker", "compose", "ps", "--format", "json"]
    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return CheckResult(
            check_id="runtime.docker_compose_ps",
            status=FAIL,
            summary=f"Timed out after {timeout_seconds}s.",
            duration_ms=int((time.perf_counter() - started) * 1000),
            command=command,
            cwd=str(root),
            stdout_tail=tail_text(ensure_text(exc.stdout)),
            stderr_tail=tail_text(ensure_text(exc.stderr)),
            next_action="Start Docker Desktop and rerun Docker Compose health acceptance.",
        )
    stdout = ensure_text(completed.stdout)
    stderr = ensure_text(completed.stderr)
    if completed.returncode != 0:
        return CheckResult(
            check_id="runtime.docker_compose_ps",
            status=FAIL,
            summary=f"docker compose ps failed: {completed.returncode}",
            duration_ms=int((time.perf_counter() - started) * 1000),
            command=command,
            cwd=str(root),
            stdout_tail=tail_text(stdout),
            stderr_tail=tail_text(stderr),
            next_action="Start Docker Desktop and run docker compose up before final acceptance.",
        )
    records, parse_error = parse_compose_ps_json(stdout)
    if parse_error:
        return CheckResult(
            check_id="runtime.docker_compose_ps",
            status=FAIL,
            summary=parse_error,
            duration_ms=int((time.perf_counter() - started) * 1000),
            command=command,
            cwd=str(root),
            stdout_tail=tail_text(stdout),
            stderr_tail=tail_text(stderr),
            next_action="Upgrade Docker Compose or inspect docker compose ps output manually.",
        )
    required_services = {"backend", "frontend", "minio", "milvus", "neo4j", "redis"}
    by_service = {
        str(record.get("Service") or record.get("Name") or ""): record
        for record in records
        if isinstance(record, dict)
    }
    missing = sorted(required_services - set(by_service))
    unhealthy: list[str] = []
    not_running: list[str] = []
    for service_name in sorted(required_services & set(by_service)):
        record = by_service[service_name]
        state = str(record.get("State") or record.get("Status") or "").lower()
        health = str(record.get("Health") or "").lower()
        if "running" not in state:
            not_running.append(f"{service_name}:{state or 'unknown'}")
        if health and health not in {"healthy", "none", "running"}:
            unhealthy.append(f"{service_name}:{health}")
    status = PASS if not missing and not not_running and not unhealthy else FAIL
    evidence = {
        service: {
            "state": by_service.get(service, {}).get("State")
            or by_service.get(service, {}).get("Status"),
            "health": by_service.get(service, {}).get("Health"),
        }
        for service in sorted(required_services)
    }
    return CheckResult(
        check_id="runtime.docker_compose_ps",
        status=status,
        summary=(
            "Docker Compose P0 services are running."
            if status == PASS
            else (
                "Docker Compose P0 services are not healthy: "
                f"missing={missing}, not_running={not_running}, unhealthy={unhealthy}"
            )
        ),
        duration_ms=int((time.perf_counter() - started) * 1000),
        command=command,
        cwd=str(root),
        stdout_tail=tail_text(json.dumps(evidence, ensure_ascii=False, indent=2), 4000),
        stderr_tail=tail_text(stderr),
        next_action=""
        if status == PASS
        else "Run docker compose up -d --build and wait until all P0 services are healthy.",
    )


def parse_compose_ps_json(stdout: str) -> tuple[list[dict[str, Any]], str | None]:
    stripped = stdout.strip()
    if not stripped:
        return [], "docker compose ps returned no JSON output."
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        records: list[dict[str, Any]] = []
        for line in stripped.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                return [], f"docker compose ps output is not JSON: {exc.msg}"
            if isinstance(item, dict):
                records.append(item)
        return records, None
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)], None
    if isinstance(parsed, dict):
        return [parsed], None
    return [], "docker compose ps JSON output has an unsupported shape."


def summarize(results: Sequence[CheckResult]) -> dict[str, int]:
    summary = {PASS: 0, FAIL: 0, SKIPPED: 0, "total": len(results)}
    for result in results:
        summary[result.status] = summary.get(result.status, 0) + 1
    return summary


def final_acceptance_command_text() -> str:
    return (
        "conda activate py313\n"
        "python scripts/p0_acceptance.py --include-root-e2e "
        "--include-p0-contracts --include-runtime-http "
        "--include-model-smoke --include-docker "
        "--mcp-server-name <configured-server-name> --require-final-handoff"
    )


def final_acceptance_bootstrap_notes() -> list[str]:
    return [
        (
            "Activate the Miniconda py313 environment before running this host-side helper, "
            "or set AGENT_BACKEND_PYTHON to the py313 python.exe path."
        ),
        (
            "Start the compose stack and wait for backend, frontend, MinIO, Milvus, "
            "Neo4j, and Redis to be healthy."
        ),
        (
            "Set a real DEFAULT_MODEL_API_KEY in a private env file or configure "
            "main_chat and graphrag_llm in Settings before --include-model-smoke."
        ),
        (
            "Configure embedding with an OpenAI-compatible model plus an "
            "embedding_api_key secret; --include-model-smoke now calls /embeddings."
        ),
        (
            "Configure and reconnect a real MCP server, then pass its server name "
            "with --mcp-server-name; runtime HTTP acceptance treats a skipped MCP "
            "live smoke as non-passing."
        ),
        (
            "Install Microsoft Edge, Chrome, or Chromium on the host so final "
            "acceptance can run the headless browser smoke."
        ),
    ]


def provided_acceptance_flags(args: argparse.Namespace) -> list[str]:
    flags: list[str] = []
    if args.include_root_e2e:
        flags.append("--include-root-e2e")
    if args.include_p0_contracts:
        flags.append("--include-p0-contracts")
    if args.include_runtime_http:
        flags.append("--include-runtime-http")
    if args.include_model_smoke:
        flags.append("--include-model-smoke")
    if args.include_docker:
        flags.append("--include-docker")
    if args.mcp_server_name:
        flags.append("--mcp-server-name")
    if args.require_final_handoff:
        flags.append("--require-final-handoff")
    return flags


def final_handoff_summary(
    results: Sequence[CheckResult],
    provided_flags: Sequence[str] = (),
    *,
    readiness_after_report_pending: bool = False,
) -> dict[str, Any]:
    by_check_id = {result.check_id: result for result in results}
    provided = set(provided_flags)
    missing_flags = [
        flag for flag in FINAL_HANDOFF_REQUIRED_FLAGS if flag not in provided
    ]
    missing = [
        check_id for check_id in FINAL_HANDOFF_CHECK_IDS if check_id not in by_check_id
    ]
    non_passing = [
        {
            "check_id": check_id,
            "status": by_check_id[check_id].status,
            "summary": by_check_id[check_id].summary,
            "next_action": by_check_id[check_id].next_action,
        }
        for check_id in FINAL_HANDOFF_CHECK_IDS
        if check_id in by_check_id and by_check_id[check_id].status != PASS
    ]
    non_passing_executed = [
        {
            "check_id": result.check_id,
            "status": result.status,
            "summary": result.summary,
            "next_action": result.next_action,
        }
        for result in results
        if result.status != PASS
    ]
    return {
        "schema_version": 1,
        "contract_id": FINAL_HANDOFF_CONTRACT_ID,
        "contract_version": FINAL_HANDOFF_CONTRACT_VERSION,
        "ready": (
            not readiness_after_report_pending
            and not missing_flags
            and not missing
            and not non_passing_executed
        ),
        "required_flags": list(FINAL_HANDOFF_REQUIRED_FLAGS),
        "provided_flags": list(provided_flags),
        "missing_flags": missing_flags,
        "required_check_ids": list(FINAL_HANDOFF_CHECK_IDS),
        "missing_check_ids": missing,
        "non_passing_checks": non_passing,
        "non_passing_executed_checks": non_passing_executed,
        "readiness_after_report_pending": readiness_after_report_pending,
        "recommended_command": final_acceptance_command_text(),
        "bootstrap_notes": final_acceptance_bootstrap_notes(),
    }


def write_report(
    root: Path,
    report_path: Path,
    results: Sequence[CheckResult],
    provided_flags: Sequence[str],
    *,
    readiness_after_report_pending: bool = False,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": ACCEPTANCE_REPORT_SCHEMA_VERSION,
        "final_handoff_contract_id": FINAL_HANDOFF_CONTRACT_ID,
        "final_handoff_contract_version": FINAL_HANDOFF_CONTRACT_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "project_root": str(root),
        "provided_flags": list(provided_flags),
        "required_final_handoff_flags": list(FINAL_HANDOFF_REQUIRED_FLAGS),
        "required_final_handoff_check_ids": list(FINAL_HANDOFF_CHECK_IDS),
        "summary": summarize(results),
        "final_handoff": final_handoff_summary(
            results,
            provided_flags,
            readiness_after_report_pending=readiness_after_report_pending,
        ),
        "checks": [asdict(result) for result in results],
    }
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def default_handoff_path(report_path: Path) -> Path:
    return report_path.with_name("p0_handoff_report.md")


def write_handoff_markdown(
    root: Path,
    handoff_path: Path,
    report_path: Path,
    results: Sequence[CheckResult],
    provided_flags: Sequence[str],
    *,
    readiness_after_report_pending: bool = False,
) -> None:
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    summary = summarize(results)
    handoff = final_handoff_summary(
        results,
        provided_flags,
        readiness_after_report_pending=readiness_after_report_pending,
    )
    status = "READY" if handoff["ready"] else "BLOCKED"
    blocking_rows = [
        item
        for item in handoff["non_passing_executed_checks"]
        if isinstance(item, dict)
    ]
    lines = [
        "# P0 Final Handoff Report",
        "",
        f"- Generated at: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
        f"- Project root: `{root}`",
        f"- JSON report: `{report_path}`",
        f"- Status: **{status}**",
        f"- Contract: `{FINAL_HANDOFF_CONTRACT_ID}` v{FINAL_HANDOFF_CONTRACT_VERSION}",
        f"- Summary: pass={summary.get(PASS, 0)}, fail={summary.get(FAIL, 0)}, "
        f"skipped={summary.get(SKIPPED, 0)}, total={summary.get('total', 0)}",
        "",
        "## Required Flags",
        "",
        f"- Provided: `{', '.join(provided_flags) or 'none'}`",
        f"- Missing: `{', '.join(handoff['missing_flags']) or 'none'}`",
        "",
        "## Blocking Checks",
        "",
    ]
    if not blocking_rows and not handoff["missing_check_ids"] and not handoff["missing_flags"]:
        lines.append("No blocking checks remain in the latest acceptance report.")
    else:
        if handoff["missing_check_ids"]:
            lines.append(
                "- Missing check IDs: `"
                + ", ".join(str(item) for item in handoff["missing_check_ids"])
                + "`"
            )
        if blocking_rows:
            lines.extend(
                [
                    "",
                    "| Check | Status | Summary | Next action |",
                    "| --- | --- | --- | --- |",
                ]
            )
            for item in blocking_rows:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _md_cell(item.get("check_id")),
                            _md_cell(item.get("status")),
                            _md_cell(item.get("summary")),
                            _md_cell(item.get("next_action")),
                        ]
                    )
                    + " |"
                )
    lines.extend(
        [
            "",
            "## Recommended Final Command",
            "",
            "```powershell",
            final_acceptance_command_text(),
            "```",
            "",
            "## Bootstrap Notes",
            "",
        ]
    )
    lines.extend(f"- {note}" for note in final_acceptance_bootstrap_notes())
    lines.extend(
        [
            "",
            "## Check Results",
            "",
            "| Check | Status | Summary |",
            "| --- | --- | --- |",
        ]
    )
    for result in results:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(result.check_id),
                    _md_cell(result.status),
                    _md_cell(result.summary),
                ]
            )
            + " |"
        )
    handoff_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _md_cell(value: Any) -> str:
    text = str(value or "")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run P0 Agent System acceptance checks.")
    parser.add_argument(
        "--list-final-checks",
        action="store_true",
        help="Print the final handoff required flags and checks, then exit.",
    )
    parser.add_argument("--include-root-e2e", action="store_true")
    parser.add_argument("--include-p0-contracts", action="store_true")
    parser.add_argument("--include-runtime-http", action="store_true")
    parser.add_argument("--include-model-smoke", action="store_true")
    parser.add_argument("--include-docker", action="store_true")
    parser.add_argument(
        "--require-final-handoff",
        action="store_true",
        help=(
            "Exit non-zero unless every required final handoff check is present "
            "and passing."
        ),
    )
    parser.add_argument("--backend-url", default="http://localhost:8000")
    parser.add_argument("--frontend-url", default="http://localhost:3000")
    parser.add_argument("--workspace-id", default="default")
    parser.add_argument("--mcp-server-name", default=None)
    parser.add_argument("--timeout-seconds", default=120, type=int)
    parser.add_argument(
        "--report-path",
        default=str(project_root() / "logs" / "p0_acceptance_report.json"),
    )
    parser.add_argument(
        "--handoff-path",
        default=None,
        help="Markdown handoff report path. Defaults to logs/p0_handoff_report.md.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list_final_checks:
        print(
            json.dumps(
                {
                    "schema_version": ACCEPTANCE_REPORT_SCHEMA_VERSION,
                    "final_handoff_contract_id": FINAL_HANDOFF_CONTRACT_ID,
                    "final_handoff_contract_version": FINAL_HANDOFF_CONTRACT_VERSION,
                    "required_flags": list(FINAL_HANDOFF_REQUIRED_FLAGS),
                    "required_check_ids": list(FINAL_HANDOFF_CHECK_IDS),
                    "recommended_command": final_acceptance_command_text(),
                    "bootstrap_notes": final_acceptance_bootstrap_notes(),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    root = project_root()
    provided_flags = provided_acceptance_flags(args)
    results: list[CheckResult] = []
    results.extend(code_checks(root, args.timeout_seconds))
    if args.include_root_e2e:
        results.extend(root_e2e_checks(root, args.timeout_seconds))
    if args.include_p0_contracts:
        results.extend(p0_contract_checks(root, args.timeout_seconds))
    if args.include_runtime_http:
        results.extend(
            runtime_http_checks(
                args.backend_url,
                args.frontend_url,
                args.timeout_seconds,
                workspace_id=args.workspace_id,
                mcp_server_name=args.mcp_server_name,
            )
        )
    if args.include_model_smoke:
        results.extend(
            runtime_model_smoke_checks(
                args.backend_url,
                args.timeout_seconds,
                workspace_id=args.workspace_id,
            )
        )
    if args.include_docker:
        results.extend(docker_checks(root, args.timeout_seconds))

    report_path = Path(args.report_path)
    if not report_path.is_absolute():
        report_path = root / report_path
    raw_handoff_path = getattr(args, "handoff_path", None)
    handoff_path = Path(raw_handoff_path) if raw_handoff_path else default_handoff_path(report_path)
    if not handoff_path.is_absolute():
        handoff_path = root / handoff_path
    if args.include_runtime_http:
        write_report(
            root,
            report_path,
            results,
            provided_flags,
            readiness_after_report_pending=True,
        )
        results.append(
            readiness_after_report_check(
                args.backend_url,
                args.timeout_seconds,
                workspace_id=args.workspace_id,
            )
        )
    write_report(root, report_path, results, provided_flags)
    write_handoff_markdown(root, handoff_path, report_path, results, provided_flags)

    summary = summarize(results)
    handoff = final_handoff_summary(results, provided_flags)
    print(f"P0 acceptance report: {report_path}")
    print(f"P0 handoff report: {handoff_path}")
    print(f"summary={summary}")
    print(f"final_handoff_ready={handoff['ready']}")
    if handoff["missing_flags"]:
        print(f"final_handoff_missing_flags={handoff['missing_flags']}")
    if handoff["missing_check_ids"]:
        print(f"final_handoff_missing={handoff['missing_check_ids']}")
    if handoff["non_passing_checks"]:
        print(f"final_handoff_non_passing={handoff['non_passing_checks']}")
    for result in results:
        print(f"[{result.status}] {result.check_id}: {result.summary}")
    if args.require_final_handoff and not handoff["ready"]:
        return 1
    return 1 if summary.get(FAIL, 0) or summary.get(SKIPPED, 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())

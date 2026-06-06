# Agent System

P0 implementation of the Agent System described in `../final_plan`.

## Stack

- Backend: Python + FastAPI + LangChain + LangGraph
- Frontend: React + TypeScript + Next.js + `@lobehub/ui` + Antd + `antd-style` + Zustand
- Data: MinIO + Milvus + Neo4j
- Cache: Redis cache only
- Network: REST + SSE

This project intentionally does not use PostgreSQL, MySQL, Gateway, WebSocket, Redis queue, or Redis lock.

## Layout

```text
backend/      FastAPI backend
frontend/     Next.js frontend
deploy/       Local compose and initialization scripts
docs/         Snapshot of final design documents
tests/        Cross-service E2E tests
```

## P0 local start

Start the full local stack:

```powershell
docker compose --env-file deploy/env/local.env.example up -d --build
```

The root `.env` is also prepared for local development, so `docker compose up -d --build` works after Docker Desktop is running. In development, `DEFAULT_MODEL_API_KEY` is encrypted into Secret Store at startup and referenced by `DEFAULT_MODEL_API_KEY_REF`; when `DEFAULT_EMBEDDING_API_KEY_REF` and `DEFAULT_EMBEDDING_API_KEY` are set, the backend also seeds a separate `embedding_api_key` secret for embedding calls. API responses and logs still expose only the secret reference.

Do not put real provider credentials in committed example env files. For final model smoke, copy `.env.example` or `deploy/env/local.env.example` to a private local env file, set `DEFAULT_MODEL_API_KEY`, and keep `DEFAULT_MODEL_API_KEY_REF` stable so startup can seed the Secret Store reference. Configure the DashScope embedding key separately through `DEFAULT_EMBEDDING_API_KEY_REF` and `DEFAULT_EMBEDDING_API_KEY`; it is stored as an `embedding_api_key` secret type and is not inferred from the main chat model key.

The compose command starts services, but the host-side acceptance helper uses your local Python environment for code and contract checks. This project uses the Miniconda `py313` environment for local backend dependencies. Activate it before running `scripts/p0_acceptance.py`, or point `AGENT_BACKEND_PYTHON` at the `py313` Python executable:

```powershell
conda activate py313
python -m pip install -e ".\backend[dev]"

# optional explicit override when py313 is not the active shell environment
$env:AGENT_BACKEND_PYTHON="$env:USERPROFILE\miniconda3\envs\py313\python.exe"

python scripts\p0_acceptance.py --list-final-checks
```

If a stale `backend\.venv` exists, ignore it for P0 acceptance. The acceptance helper requires the active/configured Miniconda `py313` environment and will fail fast if it cannot confirm that backend checks are using `py313`.

```powershell
python scripts\p0_acceptance.py --list-final-checks
```

The compose stack starts:

- `backend` on `http://localhost:8000`
- `frontend` on `http://localhost:3000`
- MinIO on `http://localhost:9000`
- Milvus on `http://localhost:19530`
- Neo4j on `http://localhost:7474`
- Redis on `localhost:6379`

The compose environment uses `OBJECT_STORE_BACKEND=minio`, so P0 runtime state is written to MinIO. Unit tests can still override `get_object_store` with a local object store.
The backend also starts the local Job worker automatically with `JOB_WORKER_AUTOSTART=true`; final acceptance requires `/workspaces/default/jobs/worker/status` to report `running=true`.

Run backend manually when developing backend code. Start the dependencies first, then load the same env file shape used by compose before starting Uvicorn; otherwise the backend falls back to local object storage, a fake model provider, and no autostarted Job worker.

```powershell
docker compose --env-file deploy/env/local.env.example up -d minio milvus neo4j redis

conda activate py313
python -m pip install -e ".\backend[dev]"
cd backend

$envFile = if (Test-Path ..\.env) { "..\.env" } else { "..\deploy\env\local.env.example" }
Get-Content $envFile | ForEach-Object {
  if ($_ -match "^\s*#" -or $_ -notmatch "=") { return }
  $name, $value = $_ -split "=", 2
  Set-Item -Path "Env:$name" -Value $value
}

python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Run frontend manually when developing frontend code:

```powershell
cd frontend
pnpm install
pnpm dev
```

Verify:

```powershell
cd backend
python -m pytest

cd ..\frontend
pnpm test
pnpm tsc --noEmit
```

Run the focused P0 acceptance helper from the project root. This quick pass does not prove final handoff readiness because it does not run live Docker, model, MCP, or runtime HTTP checks.

```powershell
python scripts\p0_acceptance.py
```

Any acceptance run that includes `--include-runtime-http` and is expected to pass must also provide `--mcp-server-name`; otherwise the required MCP live smoke is skipped and the helper exits non-zero. Final-acceptance probes are explicit because they require running services, real main chat / GraphRAG / embedding model APIs, and a live MCP server:

```powershell
python scripts\p0_acceptance.py --include-root-e2e --include-p0-contracts --include-runtime-http --include-model-smoke --include-docker --mcp-server-name <configured_server_name> --require-final-handoff
```

`--include-p0-contracts` runs the curated backend and frontend contract suites for chat, jobs,
model config, database config/health, documents, GraphRAG, MCP, memory, Skill, SubAgent, logs, and
the main frontend panels.

The runtime HTTP probe uses `--workspace-id default` unless another workspace is provided. It
checks backend health, bootstrap, workspace readiness, database live health, Job worker running
status, frontend root, and the main workspace routes: Chat, Jobs, Knowledge, Memory, Skills,
SubAgents, MCP Tools, Logs, P0 Readiness, and Settings.
It also probes the Settings deep links for `tab=models`, `tab=databases`, and `tab=secrets`, because
these are the P0 configuration entry points for model APIs, database connections, and Secret Store.
This probe is recorded as `runtime.frontend_route_smoke`; it is an HTTP route smoke, not a full
browser E2E run.
The same runtime pass also tries a real headless browser smoke with Microsoft Edge, Chrome, or
Chromium and records it as `runtime.frontend_browser_smoke`. If no supported browser executable is
installed on the host, final handoff remains blocked instead of treating HTTP route checks as a
browser pass.
Final acceptance must provide `--mcp-server-name`; the MCP check calls
`/mcp/servers/{server_name}/health?live_probe=true` and requires `runtime_configured=true`,
`connected=true`, and `tool_count>=1`. Skipped required checks are treated as a failed acceptance
run. Use `--require-final-handoff` for the final handoff pass; it makes the helper exit non-zero if
any required final check is missing even when the checks that did run passed.

For a fresh local stack, configure at least one real MCP server in the MCP page, refresh/reconnect it, then pass that server name to `--mcp-server-name`. The repo includes a packaged stdio MCP smoke server that works inside the backend container:

```powershell
python scripts\bootstrap_mcp_smoke.py --server-name agent_smoke --process-refresh
python scripts\p0_acceptance.py --include-runtime-http --include-docker --mcp-server-name agent_smoke
```

The helper configures the server as `python -m app.mcp_smoke_server`, probes `/health?live_probe=true`, and prints the exact acceptance flag. For non-smoke production integrations, prefer an HTTP/SSE MCP server reachable from the backend container, for example:

```text
server_name: local_tools
transport: http
url: http://host.docker.internal:8765/mcp
```

On Windows Docker Desktop, `host.docker.internal` lets the backend container reach a host-side MCP process. If you use `stdio` instead, the configured command must exist inside the backend runtime environment. After saving the server config, run Refresh/Reconnect from the MCP page and confirm the server health reports `connected=true` with at least one tool before running final acceptance.
Use `--list-final-checks` to print the exact required final flags and check IDs without running
tests or touching services.
The model smoke probe performs real `main_chat` and `graphrag_llm` calls through the configured
OpenAI-compatible or Anthropic provider, and an `embedding` smoke through the OpenAI-compatible
`/embeddings` endpoint, so keep it explicit when API spend or network access is not intended.
For a reproducible final pass, confirm all three P0 model config slots show configured secret refs in Settings -> Models before running `--include-model-smoke`; the example env files intentionally leave `DEFAULT_MODEL_API_KEY` and `DEFAULT_EMBEDDING_API_KEY` empty.
After writing the preliminary report, the helper rechecks workspace readiness, appends the real `runtime.p0_readiness_after_report` result, and then writes the final report. The helper no longer writes any provisional self-referential PASS seed; readiness ignores only that self-check while evaluating `external.final_handoff`, then the final report records the actual post-report readiness response.

The helper writes `logs/p0_acceptance_report.json` with pass/fail/skipped checks and also writes
`logs/p0_handoff_report.md` as the human-readable final handoff summary. It does not
start, stop, delete, or modify Docker resources. The backend readiness API reads this report
through `P0_ACCEPTANCE_REPORT_PATH` and reflects the latest Docker/MCP/frontend smoke evidence on
the `P0 Readiness` page. The report also includes `final_handoff`, which the readiness API exposes
as `external.final_handoff` so the handoff page can distinguish a quick code-only run from a full
final acceptance pass. Current reports use `schema_version=2` and
`final_handoff_contract_id=p0-final-handoff-2026-06-01` with
`final_handoff_contract_version=3`; `--list-final-checks` is the authoritative way to print the
current final command, flags, and required check IDs. A stale report that lacks this contract
metadata or whose required check list does not include the current model, Docker, database, Job
worker, MCP, frontend route/browser, and post-report readiness checks is not final handoff evidence,
even if it has `schema_version=2`. `final_handoff.ready` requires all required flags, all required
check IDs, and every executed check in the report to pass, so old quick-run reports or partially
failing expanded runs cannot be mistaken for a complete handoff.

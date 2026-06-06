# Local P0 stack

This deploy folder contains the local P0 runtime stack from the final plan:

- Agent System backend
- Agent System frontend
- MinIO
- Milvus
- Neo4j
- Redis as cache only

It intentionally does not define PostgreSQL, MySQL, Gateway, WebSocket, or Redis queue services.
The local stack enables `JOB_WORKER_AUTOSTART=true` so Job-based document ingestion, MCP refresh,
database health checks, diagnostics, and log archive work can run without a manual worker start.

## Small VPS deployment note

A 2 core / 8 GB VPS can run this stack as a light development or smoke-test environment, but it should not be treated as a production high-concurrency target. Milvus, Neo4j, MinIO, backend, frontend, Redis, and etcd can fit if the machine is quiet, swap is enabled, and only small knowledge bases are used.

Recommended operating posture for a 2C8G VPS:

- Keep it as a remote dev / demo / final-acceptance target.
- Use Docker Compose with persistent volumes for MinIO, Milvus, etcd, Neo4j, and Redis.
- Keep `JOB_WORKER_MAX_JOBS_PER_TICK` low, such as `1` or `2`, when running ingestion or embedding rebuilds.
- Avoid large parallel document ingestion, large graph builds, or multiple embedding rebuild jobs.
- Prefer remote provider APIs for LLM and embedding; do not run local large models on the same VPS.
- Expose only frontend/backend through a reverse proxy; keep MinIO, Milvus, Neo4j, Redis, and etcd private.
- Back up MinIO and Neo4j volumes before changing embedding model, graph schema, or compose versions.

The project architecture does not change when moving from local Docker Desktop to a VPS. The main differences are environment values, public hostnames/TLS, firewall rules, persistent volume locations, and lower concurrency defaults.

## Start full stack

From `agent-system`:

```powershell
docker compose --env-file deploy/env/local.env.example -f deploy/compose/docker-compose.local.yml up -d --build
```

Equivalent root compose entry:

```powershell
docker compose --env-file deploy/env/local.env.example up -d --build
```

## Start dependencies only

Use this when running backend/frontend manually from source:

```powershell
docker compose --env-file deploy/env/local.env.example -f deploy/compose/docker-compose.local.yml up -d minio milvus neo4j redis
```

Equivalent root compose entry:

```powershell
docker compose --env-file deploy/env/local.env.example up -d minio milvus neo4j redis
```

When running the backend from source, load the same env values before starting Uvicorn so manual mode matches the P0 compose topology:

```powershell
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

## Initialize

Create the MinIO bucket:

```powershell
powershell -ExecutionPolicy Bypass -File deploy/init/minio/create-bucket.ps1
```

Apply Neo4j constraints:

```powershell
powershell -ExecutionPolicy Bypass -File deploy/init/neo4j/apply-constraints.ps1
```

Milvus collection/index initialization belongs to backend bootstrap because it depends on embedding dimensions and collection naming.

## Validate

`scripts/p0_acceptance.py` runs on the host. Use the Miniconda `py313` environment for backend code and contract checks:

```powershell
conda activate py313
python -m pip install -e ".\backend[dev]"
```

If the shell cannot activate Conda, set `AGENT_BACKEND_PYTHON` to the `py313` executable before running acceptance.

```powershell
$env:AGENT_BACKEND_PYTHON="$env:USERPROFILE\miniconda3\envs\py313\python.exe"

docker compose --env-file deploy/env/local.env.example -f deploy/compose/docker-compose.local.yml config
docker compose --env-file deploy/env/local.env.example -f deploy/compose/docker-compose.local.yml ps
python scripts\p0_acceptance.py --include-docker
```

`--include-runtime-http` includes the MCP live smoke. Any run that includes it and is expected to pass must provide `--mcp-server-name <configured_server_name>`.

## MCP bootstrap for final acceptance

Final handoff needs one configured live MCP server whose health endpoint returns `runtime_configured=true`, `connected=true`, and `tool_count>=1`. In a Docker compose run, the backend executes probes from inside the backend container, so the safest bootstrap is an HTTP/SSE MCP server reachable from that container:

```powershell
python scripts\bootstrap_mcp_smoke.py --server-name agent_smoke --process-refresh
python scripts\p0_acceptance.py --include-runtime-http --include-docker --mcp-server-name agent_smoke
```

The packaged smoke helper configures `agent_smoke` as a real stdio MCP server using `python -m app.mcp_smoke_server`, which is available inside the backend image. Use it for reproducible P0 smoke. For external MCP systems, use an HTTP/SSE server reachable from the backend container:

```text
server_name: local_tools
transport: http
url: http://host.docker.internal:8765/mcp
```

Start the MCP process on the host, save the server in the MCP page, run Refresh/Reconnect, and pass the same server name to acceptance. If using `stdio`, the configured command must be installed in the backend runtime environment; host-only commands are not visible inside the backend container.

The final handoff command additionally requires real model smoke, a configured live MCP server, runtime HTTP checks, headless browser smoke, Docker health, and the autostarted Job worker:

```powershell
python scripts\p0_acceptance.py --include-root-e2e --include-p0-contracts --include-runtime-http --include-model-smoke --include-docker --mcp-server-name <configured_server_name> --require-final-handoff
```

`--include-model-smoke` performs real provider calls for `main_chat` and `graphrag_llm`, plus a real OpenAI-compatible `/embeddings` call for `embedding`. The committed example env files intentionally leave `DEFAULT_MODEL_API_KEY` and `DEFAULT_EMBEDDING_API_KEY` empty; set them only in a private local env file or configure all three slots in Settings -> Models before running the final handoff command. The embedding key must be stored separately as an `embedding_api_key` secret and is not inferred from the main chat model key.

The current P0 embedding default is:

```text
provider=openai_compatible
model=text-embedding-v4
base_url=https://dashscope.aliyuncs.com/compatible-mode/v1
dimension=1024
```

`tongyi-embedding-vision-flash-2026-03-06` is not the P0 default. The working P0 path follows the OpenAI SDK compatible `/embeddings` reference for DashScope `text-embedding-v4`.

`--include-runtime-http` also attempts `runtime.frontend_browser_smoke` with Microsoft Edge, Chrome, or Chromium in headless mode. Install one of those browsers on the host before the final handoff run.

The helper writes a preliminary `logs/p0_acceptance_report.json`, checks `/workspaces/{workspace_id}/readiness` again, appends the real `runtime.p0_readiness_after_report` result, and then writes the final report. It no longer writes any provisional self-referential PASS seed; readiness ignores only that self-check while evaluating `external.final_handoff`.

In Docker Compose, backend mounts `./logs:/app/logs` and reads `P0_ACCEPTANCE_REPORT_PATH=/app/logs/p0_acceptance_report.json`. Keep that mount when moving to a VPS; otherwise container-side readiness cannot see the host-generated final acceptance report.

Use `python scripts\p0_acceptance.py --list-final-checks` immediately before final handoff to confirm the current contract. The current report must carry `final_handoff_contract_id=p0-final-handoff-2026-06-01`, `final_handoff_contract_version=3`, all required final flags, and the full required check ID list. A historical `logs/p0_acceptance_report.json` with `schema_version=2` but without this contract metadata or without the current required check IDs is stale evidence and must not be used for final handoff.

Expected local ports:

- MinIO API: `9000`
- MinIO Console: `9001`
- Milvus gRPC: `19530`
- Milvus health: `9091`
- Neo4j HTTP: `7474`
- Neo4j Bolt: `7687`
- Redis: `6379`

## Rollback

Stop containers without deleting data:

```powershell
docker compose --env-file deploy/env/local.env.example -f deploy/compose/docker-compose.local.yml stop
```

Delete local dependency data only when the data can be recreated:

```powershell
docker compose --env-file deploy/env/local.env.example -f deploy/compose/docker-compose.local.yml down -v
```

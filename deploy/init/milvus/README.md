# Milvus local init

Milvus is started in standalone mode by `deploy/compose/docker-compose.local.yml`.

For Phase A there is no destructive schema migration here. Backend bootstrap is expected to create or verify collections and indexes after it can connect to:

```text
MILVUS_URI=http://localhost:19530
MILVUS_DATABASE=default
MILVUS_COLLECTION_PREFIX=agent
```

Health expectation:

```text
http://localhost:9091/healthz
```

Acceptance checks for later backend integration:

- connect to Milvus
- list collections
- create and drop a temporary dimension test collection

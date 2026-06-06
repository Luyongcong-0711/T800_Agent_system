# Redis local init

Redis is used as cache only. It must not be treated as a job queue or durable event store.

Local compose starts Redis with:

```text
redis-server --appendonly no
```

Health expectation:

```text
redis-cli ping
```

Acceptance checks for later backend integration:

- ping Redis
- set and get a temporary key under `REDIS_NAMESPACE`
- delete the temporary key

# Worker Operations

The worker runs as a separate long-lived process:

```bash
python -m app.worker.worker
```

## Health verification

Use logs to verify health:

- startup log: `worker.started`
- claim log: `worker.job_claimed` (includes `job_id`, `job_type`)
- success log: `worker.job_succeeded`
- failure log: `worker.job_failed`

In hosted environments (Render/Railway), open worker logs and confirm:

1. `worker.started` appears after deploy.
2. New queued jobs emit `worker.job_claimed`.
3. Jobs finish with either `worker.job_succeeded` or `worker.job_failed`.

If no claim logs appear while jobs are queued, the worker is not connected to the same database as the API.

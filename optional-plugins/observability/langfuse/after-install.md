# After installing langfuse

Langfuse tracing is now installed and enabled for your Hermes profile.

## Required credentials

Set these in `~/.hermes/.env` (or via `hermes tools` → Langfuse Observability):

```bash
HERMES_LANGFUSE_PUBLIC_KEY=pk-lf-...
HERMES_LANGFUSE_SECRET_KEY=sk-lf-...
HERMES_LANGFUSE_BASE_URL=https://cloud.langfuse.com   # or your self-hosted URL
```

## Verify

```bash
hermes plugins list          # langfuse should appear as enabled
hermes chat -q "hello"       # then check Langfuse for a "Hermes turn" trace
```

## Optional settings

```bash
HERMES_LANGFUSE_ENV=production       # environment tag
HERMES_LANGFUSE_RELEASE=v1.0.0      # release tag
HERMES_LANGFUSE_SAMPLE_RATE=0.5     # sample 50% of traces
HERMES_LANGFUSE_MAX_CHARS=12000     # max chars per field (default: 12000)
HERMES_LANGFUSE_DEBUG=true          # verbose plugin logging
```

## Dependencies

The `langfuse` Python SDK is required. Install it into your Hermes venv:

```bash
pip install langfuse
```

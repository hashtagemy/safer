# vanilla-python

A 60-line demo showing how to instrument an agent framework that SAFER
does not have a bundled adapter for. The trick: call
`safer.track_event(Hook.*, payload)` at each of the 9 lifecycle points.

```bash
uv run python examples/vanilla-python/main.py
```

Prerequisites:

- Backend running (`uv run uvicorn safer_backend.main:app`)
- `ANTHROPIC_API_KEY` only if you want the Judge to run; event ingestion
  works without it.

Open `http://localhost:5173/live` to watch the 6 events land.

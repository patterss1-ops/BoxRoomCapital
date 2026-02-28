# Control Plane (FastAPI + HTMX)

This replaces the Streamlit-first workflow with an operator console that can:

- start/stop the in-process options engine
- pause/resume scheduled scans
- trigger one-shot scans
- run manual reconcile
- close a spread by `spread_id` or `ticker`
- show live status, jobs, bot events, and log tail

## Run locally

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Start the console:

```bash
python3 run_console.py
```

3. Open:

- `http://127.0.0.1:8000/` (UI)
- `http://127.0.0.1:8000/api/status` (JSON)

## Notes

- Engine control state lives in `.runtime/options_engine_state.json`.
- Log tail reads from `.runtime/control_plane.log` (isolated from legacy runner logs).
- Control jobs are persisted in `trades.db` (`jobs` table).
- If engine is not running, one-shot scans execute this path in-process:

```bash
python3 options_runner.py --mode <shadow|live> --once
```

## Legacy Entrypoints

Legacy scripts were moved to `legacy/`:

- `legacy/dashboard.py`
- `legacy/main.py`
- `legacy/runner.py`

Root-level `dashboard.py`, `main.py`, and `runner.py` now fail fast and point to `run_console.py`.

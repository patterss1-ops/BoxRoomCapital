"""Entrypoint for the new FastAPI control plane."""
from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.join(BASE_DIR, ".runtime")
os.makedirs(RUNTIME_DIR, exist_ok=True)

# Keep control-plane logs isolated from legacy bot runs.
os.environ.setdefault("BOT_LOG_FILE", os.path.join(RUNTIME_DIR, "control_plane.log"))


def _missing_dep_message(module: str):
    print(f"Missing dependency: {module}", file=sys.stderr)
    print("Install requirements with:", file=sys.stderr)
    print("  python3 -m pip install -r requirements.txt", file=sys.stderr)
    print("Or install core UI deps directly:", file=sys.stderr)
    print("  python3 -m pip install fastapi 'uvicorn[standard]' jinja2 python-multipart", file=sys.stderr)


try:
    import uvicorn
except ModuleNotFoundError:
    _missing_dep_message("uvicorn")
    raise SystemExit(1)

try:
    from app.api.server import app
except ModuleNotFoundError as exc:
    _missing_dep_message(str(exc))
    raise SystemExit(1)


if __name__ == "__main__":
    host = os.getenv("BOT_UI_HOST", "127.0.0.1")
    port = int(os.getenv("BOT_UI_PORT", "8000"))
    uvicorn.run("run_console:app", host=host, port=port, reload=False)

#!/bin/zsh
cd "$(dirname "$0")"
source .venv/bin/activate
exec python -m uvicorn app.main:app --host 127.0.0.1 --port ${PORT:-8000}

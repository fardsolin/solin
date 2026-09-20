"""Authenticated, read-only dashboard; no credentials for or access to an exchange."""

import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from engine.models import HOUR_MS
from engine.storage import read_status

STATIC = Path(__file__).parent / "static"


def create_app(data_dir=None, password=None):
    data_dir = Path(data_dir or os.getenv("DATA_DIR", "data"))
    password = password if password is not None else os.getenv("DASHBOARD_PASSWORD", "")
    app = FastAPI(title="Solin Paper Monitor", docs_url=None, redoc_url=None, openapi_url=None)
    basic = HTTPBasic(auto_error=False)

    def require_auth(credentials: HTTPBasicCredentials | None = Depends(basic)):
        if len(password) < 32 or password == "replace-with-a-random-password-at-least-32-characters":
            raise HTTPException(status_code=503, detail="Dashboard password is not securely configured")
        if credentials is None:
            raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Basic"})
        user_ok = secrets.compare_digest(credentials.username.encode(), b"admin")
        password_ok = secrets.compare_digest(credentials.password.encode(), password.encode())
        if not (user_ok and password_ok):
            raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Basic"})

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        )
        return response

    @app.get("/healthz")
    def health():
        # Process health only. No account information, timestamps or filenames.
        return {"service": "dashboard", "ok": True}

    @app.get("/", dependencies=[Depends(require_auth)])
    def page():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status", dependencies=[Depends(require_auth)])
    def api_status(limit: int = Query(30, ge=1, le=100)):
        try:
            result = read_status(data_dir / "paper.sqlite3", limit)
        except (sqlite3.Error, json.JSONDecodeError, OSError, ValueError):
            return JSONResponse(
                {
                    "status": "unavailable",
                    "healthy": False,
                    "detail": "State unavailable; consult server logs",
                },
                status_code=503,
            )
        hb = result["heartbeat"]
        now = int(time.time() * 1000)
        heartbeat_fresh = 0 <= now - hb.get("ts_ms", 0) <= 900_000
        last_bar = hb.get("last_candle_open_ms")
        market_fresh = last_bar is not None and 0 <= now - (last_bar + HOUR_MS) <= HOUR_MS + 900_000
        result.update(
            healthy=heartbeat_fresh and market_fresh and hb.get("status") == "running",
            heartbeat_fresh=heartbeat_fresh,
            market_fresh=market_fresh,
            live_enabled=False,
        )
        return result

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()

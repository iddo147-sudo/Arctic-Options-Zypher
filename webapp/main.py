"""
Local dashboard for the futures bot -- backtest results now, live paper-trading status once
Alpaca is connected (see ../paper_trade_alpaca.py). Reads results.json, written by
../backtest.py after every run, so re-running a backtest and refreshing the page is the
whole workflow -- no server restart needed.

Reads and displays only. Nothing here talks to a broker or moves money on its own -- it
only reads whatever backtest.py or paper_trade_alpaca.py already wrote to disk. Neither
this file nor the dashboard it serves ever sees ALPACA_API_KEY/ALPACA_SECRET_KEY -- those
stay in paper_trade_alpaca.py's own process only.
"""

import base64
import ipaddress
import json
import pathlib
import os
import secrets

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

BASE_DIR = pathlib.Path(__file__).parent
RESULTS_PATH = BASE_DIR / "results.json"
STATUS_PATH = BASE_DIR / "agent_status.json"  # written by paper_trade_alpaca.py once that's live

# Same reasoning as the Hardcore Arctic telemetry dashboard's own require_reader: this is
# about to go on a public Railway URL, and trading strategy/performance data isn't something
# to leave open to anyone who finds the link. Unset means remote reads are refused outright
# rather than served openly.
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")


def _peer_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def require_reader(request: Request):
    """Loopback (local dev) is exempt; everything else needs HTTP Basic credentials."""
    if _is_loopback(_peer_ip(request)):
        return
    if not DASHBOARD_PASSWORD:
        raise HTTPException(status_code=503, detail="DASHBOARD_PASSWORD is not set -- remote access refused")
    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8", "replace")
            _, _, password = decoded.partition(":")
            if secrets.compare_digest(password, DASHBOARD_PASSWORD):
                return
        except Exception:
            pass
    raise HTTPException(
        status_code=401,
        detail="authentication required",
        headers={"WWW-Authenticate": 'Basic realm="Futures Bot Dashboard"'},
    )


app = FastAPI(title="Futures Bot Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/", dependencies=[Depends(require_reader)])
def dashboard():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/results", dependencies=[Depends(require_reader)])
def results():
    if not RESULTS_PATH.exists():
        return JSONResponse({"available": False, "reason": "No backtest run yet -- run backtest.py first."})
    return json.loads(RESULTS_PATH.read_text())


@app.get("/api/status", dependencies=[Depends(require_reader)])
def status():
    # No paper-trading agent connected yet -- this just says so honestly rather than
    # faking a "connected" state. paper_trade_alpaca.py will write real state here once it runs.
    if not STATUS_PATH.exists():
        return {"connected": False}
    return json.loads(STATUS_PATH.read_text())

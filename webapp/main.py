"""
Local dashboard for the futures bot -- backtest results now, live paper-trading status once
Alpaca is connected (see ../paper_trade_alpaca.py). Reads results.json, written by
../backtest.py after every run, so re-running a backtest and refreshing the page is the
whole workflow -- no server restart needed.

Reads and displays only, as far as any real trading goes -- nothing here talks to a broker
or moves money. Neither this file nor the dashboard it serves ever sees
ALPACA_API_KEY/ALPACA_SECRET_KEY -- those stay in paper_trade_alpaca.py's own process only.

REPORTING ENDPOINTS (2026-09-05, for running the agent as a separate scheduled Railway
service): a Railway Cron Schedule service runs in its OWN container with its OWN disk, so
if paper_trade_alpaca.py only wrote local files, a worker service's output would never
reach this dashboard service at all -- two different filesystems. /api/report_status/<strategy>
and /api/report_trade let the agent push its state here over HTTP instead, authenticated by
AGENT_REPORT_TOKEN (a separate secret from DASHBOARD_PASSWORD, since one's for reading the
dashboard and the other's for the agent writing to it -- a leaked read password shouldn't
let someone fake trade reports). Locally, paper_trade_alpaca.py still writes its status/trades
files directly and skips these entirely (no DASHBOARD_URL configured) -- both paths write to
the exact same files, so the GET endpoints below don't need to know which one produced them.

MULTI-AGENT (2026-09-06, "scale it to a workflow"): status is now PER STRATEGY
(agent_status_<strategy>.json), since paper_trade_alpaca.py can run more than one validated
strategy (breakout, rsi) as separate agents with disjoint ticker universes. /api/agents
returns all of them at once, keyed by strategy name. Trades stay in ONE shared file
(agent_trades.json) tagged with a "strategy" field per row -- one combined real-fill history
across every agent.
"""

import base64
import ipaddress
import json
import pathlib
import os
import re
import secrets

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

BASE_DIR = pathlib.Path(__file__).parent
RESULTS_PATH = BASE_DIR / "results.json"
TRADES_PATH = BASE_DIR / "agent_trades.json"  # real fills, appended to by paper_trade_alpaca.py

# Allowlist, not just "any string" -- this becomes part of a filename below
# (agent_status_<strategy>.json), so validate it rather than trust an arbitrary path segment.
VALID_STRATEGIES = re.compile(r"^[a-z_]{1,32}$")


def agent_status_path(strategy: str) -> pathlib.Path:
    return BASE_DIR / f"agent_status_{strategy}.json"

# Same reasoning as the Hardcore Arctic telemetry dashboard's own require_reader: this is
# about to go on a public Railway URL, and trading strategy/performance data isn't something
# to leave open to anyone who finds the link. Unset means remote reads are refused outright
# rather than served openly.
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
# Separate secret from DASHBOARD_PASSWORD -- see this file's module docstring. Unset means
# report writes are refused outright, same "refuse rather than serve/accept openly" stance
# require_reader already takes for reads.
AGENT_REPORT_TOKEN = os.getenv("AGENT_REPORT_TOKEN", "")


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


def require_agent(request: Request):
    """Gate for the two report_* write endpoints -- a bearer token, not the dashboard's own
    HTTP Basic reader password. No loopback exemption here on purpose: even a local run
    that happens to set DASHBOARD_URL should still have to present the real token."""
    if not AGENT_REPORT_TOKEN:
        raise HTTPException(status_code=503, detail="AGENT_REPORT_TOKEN is not set -- agent reports refused")
    header = request.headers.get("authorization", "")
    if header != f"Bearer {AGENT_REPORT_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid or missing agent token")


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


@app.get("/api/agents", dependencies=[Depends(require_reader)])
def agents():
    """All known agents' status, keyed by strategy name -- {} before any agent has ever run,
    same honest-empty-state pattern the old single-agent /api/status used."""
    result = {}
    for path in BASE_DIR.glob("agent_status_*.json"):
        strategy = path.stem.removeprefix("agent_status_")
        try:
            result[strategy] = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
    return result


@app.get("/api/live_trades", dependencies=[Depends(require_reader)])
def live_trades():
    # Real fills, separate from a backtest's trade_log -- empty rather than 404 before the
    # agent has ever placed a real order, same "report the honest state" pattern as /api/status.
    if not TRADES_PATH.exists():
        return []
    return json.loads(TRADES_PATH.read_text())


@app.post("/api/report_status/{strategy}", dependencies=[Depends(require_agent)])
async def report_status(strategy: str, request: Request):
    """paper_trade_alpaca.py's HTTP path for one agent's status update -- see module
    docstring. The body is stored verbatim so /api/agents just echoes it back unchanged."""
    if not VALID_STRATEGIES.match(strategy):
        raise HTTPException(status_code=400, detail="invalid strategy name")
    payload = await request.json()
    agent_status_path(strategy).parent.mkdir(exist_ok=True)
    agent_status_path(strategy).write_text(json.dumps(payload, indent=2))
    return {"ok": True}


@app.post("/api/report_trade", dependencies=[Depends(require_agent)])
async def report_trade(request: Request):
    """paper_trade_alpaca.py's HTTP path for one real fill -- appended the same way
    log_live_trade() appends locally, so /api/live_trades sees an identical shape either way."""
    trade = await request.json()
    trades = json.loads(TRADES_PATH.read_text()) if TRADES_PATH.exists() else []
    trades.append(trade)
    TRADES_PATH.write_text(json.dumps(trades, indent=2))
    return {"ok": True}

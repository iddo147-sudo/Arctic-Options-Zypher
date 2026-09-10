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
returns all of them at once, keyed by strategy name.

TRADE HISTORY PERSISTENCE (2026-09-06, real incident -- a redeploy of THIS service wiped its
local agent_trades.json, silently losing the one real trade logged so far): a Railway
container has no persistent disk by default, so every redeploy of this service (which happens
on every push while actively developing) starts from a completely empty filesystem. Local
files are fine for status (a fresh report arrives within a day anyway) but NOT for trade
HISTORY, which should never just vanish. Trades now persist in the Postgres database already
running in this same Railway project (DATABASE_URL) when it's configured, falling back to the
local agent_trades.json file when it's not (e.g. local dev without a database) -- same
"off/local unless configured" pattern as DASHBOARD_URL elsewhere in this project.
"""

import base64
import ipaddress
import json
import pathlib
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request

import psycopg2
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

BASE_DIR = pathlib.Path(__file__).parent
RESULTS_PATH = BASE_DIR / "results.json"
TRADES_PATH = BASE_DIR / "agent_trades.json"  # local-dev fallback only when DATABASE_URL is unset
KILL_SWITCH_PATH = BASE_DIR / "kill_switch_state.json"  # local-dev fallback only when DATABASE_URL is unset

DATABASE_URL = os.getenv("DATABASE_URL", "")


def _db_connection():
    return psycopg2.connect(DATABASE_URL)


def _ensure_trades_table():
    if not DATABASE_URL:
        return
    with _db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id SERIAL PRIMARY KEY,
                trade_date TEXT,
                strategy TEXT,
                symbol TEXT,
                side TEXT,
                price NUMERIC,
                size INTEGER,
                reason TEXT,
                reported_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        conn.commit()


def _ensure_kill_switch_table():
    if not DATABASE_URL:
        return
    with _db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS kill_switch (
                id TEXT PRIMARY KEY,
                tripped BOOLEAN NOT NULL DEFAULT FALSE,
                tripped_at TIMESTAMPTZ,
                equity_at_trip NUMERIC,
                reason TEXT
            )
        """)
        conn.commit()


try:
    _ensure_trades_table()
    _ensure_kill_switch_table()
except psycopg2.Error as e:
    # Don't take down the whole dashboard over a DB hiccup at startup -- a real connectivity
    # problem will surface again (and print again) on the next actual read/write attempt.
    print(f"[warn] could not reach Postgres at startup: {e}")

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

# ---- brute-force protection (2026-09-09, explicit user request -- "can we make it more
# secure") ------------------------------------------------------------------------------
# Nothing previously stopped repeated wrong-password guesses. In-memory only (resets on a
# redeploy) rather than a real distributed store -- proportionate to what's actually at
# stake here (a paper-trading dashboard, no real money reachable through it), and this
# service already redeploys often enough that "resets sometimes" is an accepted tradeoff,
# not a real gap. Same NTFY_TOPIC as paper_trade_alpaca.py's own trade notifications --
# reused here so a lockout event pushes to the same phone, one alert per lockout (not one
# per failed attempt, which would just spam).
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
FAILED_LOGIN_THRESHOLD = 5
FAILED_LOGIN_WINDOW_SECONDS = 300
LOCKOUT_SECONDS = 900

_failed_attempts: dict[str, list[float]] = {}
_lockouts: dict[str, float] = {}
_auth_lock = threading.Lock()


def _notify_phone(title: str, message: str):
    if not NTFY_TOPIC:
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Tags": "rotating_light"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except urllib.error.URLError:
        pass  # never let a notification hiccup affect the actual auth decision


def _is_locked_out(ip: str) -> bool:
    with _auth_lock:
        until = _lockouts.get(ip)
        if until and time.time() < until:
            return True
        if until:  # lockout expired -- clear it and give this IP a clean slate
            del _lockouts[ip]
            _failed_attempts.pop(ip, None)
        return False


def _record_failed_attempt(ip: str):
    now = time.time()
    with _auth_lock:
        attempts = [t for t in _failed_attempts.get(ip, []) if now - t < FAILED_LOGIN_WINDOW_SECONDS]
        attempts.append(now)
        _failed_attempts[ip] = attempts
        newly_locked = len(attempts) >= FAILED_LOGIN_THRESHOLD and ip not in _lockouts
        if newly_locked:
            _lockouts[ip] = now + LOCKOUT_SECONDS
    if newly_locked:
        _notify_phone(
            "Dashboard: repeated failed logins",
            f"{len(attempts)} failed attempts from {ip} in {FAILED_LOGIN_WINDOW_SECONDS // 60} min -- "
            f"locked out for {LOCKOUT_SECONDS // 60} min.",
        )


def _peer_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def require_reader(request: Request):
    """Loopback (local dev) is exempt; everything else needs HTTP Basic credentials, and
    now a clean recent history of not failing them repeatedly."""
    if _is_loopback(_peer_ip(request)):
        return
    ip = _peer_ip(request)
    if _is_locked_out(ip):
        raise HTTPException(status_code=429, detail="too many failed attempts -- try again later")
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
    _record_failed_attempt(ip)
    raise HTTPException(
        status_code=401,
        detail="authentication required",
        headers={"WWW-Authenticate": 'Basic realm="Futures Bot Dashboard"'},
    )


def require_agent(request: Request):
    """Gate for the two report_* write endpoints -- a bearer token, not the dashboard's own
    HTTP Basic reader password. No loopback exemption here on purpose: even a local run
    that happens to set DASHBOARD_URL should still have to present the real token. Shares
    the same lockout tracker as require_reader -- one brute-force counter per IP, not two
    separate ones an attacker could juggle between."""
    ip = _peer_ip(request)
    if _is_locked_out(ip):
        raise HTTPException(status_code=429, detail="too many failed attempts -- try again later")
    if not AGENT_REPORT_TOKEN:
        raise HTTPException(status_code=503, detail="AGENT_REPORT_TOKEN is not set -- agent reports refused")
    header = request.headers.get("authorization", "")
    if header != f"Bearer {AGENT_REPORT_TOKEN}":
        _record_failed_attempt(ip)
        raise HTTPException(status_code=401, detail="invalid or missing agent token")


app = FastAPI(title="Futures Bot Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Cheap, standard hardening -- 2026-09-09, "can we make it more secure." None of this
    replaces real auth, it just closes off unrelated browser-level attack classes
    (clickjacking via iframe embedding, MIME-sniffing, leaking the URL/password-bearing
    referrer to another site) for near-zero cost."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


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
    if DATABASE_URL:
        try:
            with _db_connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT trade_date, strategy, symbol, side, price, size, reason FROM trades ORDER BY id ASC")
                rows = cur.fetchall()
            return [
                {"date": r[0], "strategy": r[1], "symbol": r[2], "side": r[3], "price": float(r[4]), "size": r[5], "reason": r[6]}
                for r in rows
            ]
        except psycopg2.Error as e:
            print(f"[warn] Postgres read failed, falling back to local file: {e}")

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
    """paper_trade_alpaca.py's HTTP path for one real fill. Persists to Postgres when
    configured (survives this service's own redeploys -- see module docstring for the
    2026-09-06 incident that made this necessary), falling back to the local file otherwise."""
    trade = await request.json()

    if DATABASE_URL:
        try:
            with _db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO trades (trade_date, strategy, symbol, side, price, size, reason) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (trade.get("date"), trade.get("strategy"), trade.get("symbol"), trade.get("side"),
                     trade.get("price"), trade.get("size"), trade.get("reason")),
                )
                conn.commit()
            return {"ok": True}
        except psycopg2.Error as e:
            print(f"[warn] Postgres write failed, falling back to local file: {e}")

    trades = json.loads(TRADES_PATH.read_text()) if TRADES_PATH.exists() else []
    trades.append(trade)
    TRADES_PATH.write_text(json.dumps(trades, indent=2))
    return {"ok": True}


# ---- kill switch (2026-09-10, explicit user request -- "destroy him if he loses all 100k")
# ------------------------------------------------------------------------------------------
# Account-wide (Breakout and RSI share one Alpaca paper account, so one equity floor covers
# both), and DELIBERATELY does not auto-resume when equity recovers: tripping requires a
# human to look at why before trading resumes, not a silent bounce-back. That's why trip
# uses require_agent (the bot reports it tripped) but reset uses require_reader (only a
# human with the dashboard password can clear it) -- see chat: the whole point of a kill
# switch that "makes things better" is forcing review, not letting the bot quietly restart.


def _kill_switch_state() -> dict:
    if DATABASE_URL:
        try:
            with _db_connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT tripped, tripped_at, equity_at_trip, reason FROM kill_switch WHERE id = 'global'")
                row = cur.fetchone()
            if row:
                return {"tripped": row[0], "tripped_at": row[1].isoformat() if row[1] else None,
                        "equity_at_trip": float(row[2]) if row[2] is not None else None, "reason": row[3]}
            return {"tripped": False}
        except psycopg2.Error as e:
            print(f"[warn] Postgres read failed, falling back to local file: {e}")

    if not KILL_SWITCH_PATH.exists():
        return {"tripped": False}
    return json.loads(KILL_SWITCH_PATH.read_text())


@app.get("/api/kill_switch", dependencies=[Depends(require_agent)])
def kill_switch_status():
    """The agent's own read path, checked at the start of every run before trading -- gated
    by require_agent (bearer token), not require_reader, since this is agent-to-server
    traffic, not a human viewing the dashboard."""
    return _kill_switch_state()


@app.post("/api/kill_switch/trip", dependencies=[Depends(require_agent)])
async def trip_kill_switch(request: Request):
    """Idempotent -- if it's already tripped, this just confirms that rather than overwriting
    the original tripped_at/reason with whatever run happens to call it next."""
    body = await request.json()
    state = _kill_switch_state()
    if state.get("tripped"):
        return {"ok": True, "already_tripped": True}

    equity = body.get("equity")
    reason = body.get("reason", "equity floor breached")
    _notify_phone("KILL SWITCH TRIPPED", f"{reason} -- trading halted until manually reviewed and reset.")

    if DATABASE_URL:
        try:
            with _db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO kill_switch (id, tripped, tripped_at, equity_at_trip, reason) "
                    "VALUES ('global', TRUE, now(), %s, %s) "
                    "ON CONFLICT (id) DO UPDATE SET tripped = TRUE, tripped_at = now(), "
                    "equity_at_trip = EXCLUDED.equity_at_trip, reason = EXCLUDED.reason "
                    "WHERE kill_switch.tripped = FALSE",
                    (equity, reason),
                )
                conn.commit()
            return {"ok": True, "already_tripped": False}
        except psycopg2.Error as e:
            print(f"[warn] Postgres write failed, falling back to local file: {e}")

    import datetime as _dt
    KILL_SWITCH_PATH.write_text(json.dumps({
        "tripped": True, "tripped_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "equity_at_trip": equity, "reason": reason,
    }, indent=2))
    return {"ok": True, "already_tripped": False}


@app.post("/api/kill_switch/reset", dependencies=[Depends(require_reader)])
def reset_kill_switch():
    """Human-only (dashboard password), by design -- see module note above this section."""
    if DATABASE_URL:
        try:
            with _db_connection() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM kill_switch WHERE id = 'global'")
                conn.commit()
            return {"ok": True}
        except psycopg2.Error as e:
            print(f"[warn] Postgres write failed, falling back to local file: {e}")

    if KILL_SWITCH_PATH.exists():
        KILL_SWITCH_PATH.unlink()
    return {"ok": True}

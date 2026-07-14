import os
import contextlib
import secrets
import requests
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List

import uvicorn
import config
import bot_engine
import ledger


def _api_token() -> str:
    """Prefer env POLYCOPY_API_TOKEN; fall back to config api_token."""
    env = (os.environ.get("POLYCOPY_API_TOKEN") or "").strip()
    if env:
        return env
    try:
        cfg = config.load_config()
        return (cfg.get("api_token") or "").strip()
    except Exception:
        return ""


def require_mutate_auth(authorization: Optional[str] = Header(default=None),
                        x_api_token: Optional[str] = Header(default=None)):
    """
    Protect mutating routes. If no token is configured, reject in production-like
    hosts and allow only when POLYCOPY_ALLOW_OPEN_MUTATIONS=1 (local dev).
    """
    token = _api_token()
    provided = None
    if x_api_token:
        provided = x_api_token.strip()
    elif authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()

    if not token:
        if os.environ.get("POLYCOPY_ALLOW_OPEN_MUTATIONS", "").strip() == "1":
            return True
        raise HTTPException(
            status_code=503,
            detail=(
                "Mutating API locked: set POLYCOPY_API_TOKEN (or config api_token) "
                "and send Authorization: Bearer <token> or X-API-Token header. "
                "For local open access only, set POLYCOPY_ALLOW_OPEN_MUTATIONS=1."
            ),
        )

    if not provided or not secrets.compare_digest(provided, token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure config and ledger-backed state are loaded/initialized
    cfg = config.load_config()
    config.bootstrap_ledger(cfg)

    # Start background loop
    bot_engine.start_engine()
    yield
    # Stop background loop
    bot_engine.stop_engine()


app = FastAPI(title="Polymarket Copy-Trading Simulator", lifespan=lifespan)

# Enable response compression
app.add_middleware(GZipMiddleware, minimum_size=1000)

# CORS: restrict when origins configured; default allow local dashboard
def _cors_origins() -> List[str]:
    env = (os.environ.get("POLYCOPY_CORS_ORIGINS") or "").strip()
    if env:
        return [o.strip() for o in env.split(",") if o.strip()]
    try:
        cfg = config.load_config()
        origins = cfg.get("cors_origins") or []
        if origins:
            return list(origins)
    except Exception:
        pass
    # Local defaults
    return [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SettingsUpdate(BaseModel):
    starting_capital: Optional[float] = None
    poll_interval_seconds: Optional[int] = None
    execution_mode: Optional[str] = None
    slippage_bps: Optional[float] = None
    min_copy_price: Optional[float] = None
    max_copy_price: Optional[float] = None
    copy_only_best_wins: Optional[bool] = None
    min_best_bet_score: Optional[int] = None
    max_days_to_resolution: Optional[int] = None
    max_holding_hours: Optional[int] = None
    take_profit_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    min_whale_trade_size: Optional[float] = None
    max_market_exposure: Optional[float] = None
    min_market_liquidity: Optional[float] = None
    min_market_volume: Optional[float] = None
    enable_value_plays: Optional[bool] = None
    exclude_sports_bets: Optional[bool] = None
    exclude_crypto_bets: Optional[bool] = None
    exclude_weather_bets: Optional[bool] = None
    niche_priority_active: Optional[bool] = None
    dynamic_sizing_active: Optional[bool] = None


class FollowTrader(BaseModel):
    address: str
    name: str
    sizing_type: str = "fixed"
    sizing_value: float = 100.0


class ToggleTrader(BaseModel):
    address: str
    enabled: bool
    sizing_type: Optional[str] = None
    sizing_value: Optional[float] = None


class UnfollowTrader(BaseModel):
    address: str


class ResetRequest(BaseModel):
    confirm: bool = False
    confirm_phrase: Optional[str] = None


@app.get("/api/health")
def health_check():
    """Lightweight endpoint for keep-alive pings. Does not load full state."""
    return {"status": "ok", "ledger": True}


@app.get("/api/reconcile")
def reconcile():
    cfg = config.load_config()
    config.bootstrap_ledger(cfg)
    return ledger.reconcile_report()


@app.get("/api/state")
def get_state():
    cfg = config.load_config()
    with bot_engine._state_lock:
        state = config.load_state(cfg)
        holdings_value = bot_engine.update_live_valuations(
            state, mark_at_bid=bool(cfg.get("mark_at_bid", True))
        )
        holdings_bid = float(state.get("_holdings_value_bid", holdings_value))
        holdings_mid = float(state.get("_holdings_value_mid", holdings_value))
        total_equity = state["cash_usdc"] + holdings_bid
        total_equity_mid = state["cash_usdc"] + holdings_mid
        config.save_state(state)

    trades = state.get("trades", [])
    resolved_trades = [t for t in trades if t.get("type") in ["SELL", "RESOLVE"]]
    wins_count = sum(1 for t in resolved_trades if t.get("realized_pnl", 0.0) > 0.0)
    win_rate = (wins_count / len(resolved_trades) * 100.0) if resolved_trades else 0.0

    light_config = {k: v for k, v in cfg.items() if k not in ("followed_traders", "api_token")}

    light_state = {
        "cash_usdc": state.get("cash_usdc"),
        "positions": state.get("positions"),
        "portfolio_value_history": state.get("portfolio_value_history", []),
        "has_trades": len(trades) > 0,
        "win_rate": win_rate,
        "resolved_count": len(resolved_trades),
        "wins_count": wins_count,
        "ledger_backed": True,
        "lots_open": state.get("lots_open"),
        "holdings_value_mid": holdings_mid,
        "total_equity_mid": total_equity_mid,
    }

    return {
        "config": light_config,
        "state": light_state,
        "holdings_value": holdings_bid,
        "holdings_value_mid": holdings_mid,
        "total_equity": total_equity,
        "total_equity_mid": total_equity_mid,
        "reconcile": ledger.reconcile_report(),
    }


@app.get("/api/traders")
def get_traders():
    cfg = config.load_config()
    state = config.load_state(cfg)
    return {
        "followed_traders": cfg.get("followed_traders", []),
        "whale_positions": state.get("whale_positions", {}),
    }


@app.get("/api/history")
def get_history():
    cfg = config.load_config()
    state = config.load_state(cfg)
    return {"trades": state.get("trades", [])}


@app.get("/api/logs")
def get_logs():
    cfg = config.load_config()
    state = config.load_state(cfg)
    return {"logs": state.get("logs", [])}


@app.post("/api/settings", dependencies=[Depends(require_mutate_auth)])
def update_settings(update: SettingsUpdate):
    cfg = config.load_config()
    state = config.load_state(cfg)

    if update.starting_capital is not None:
        # Only adjust cash if no activity yet
        if len(state.get("trades") or []) == 0 and len(state.get("positions") or {}) == 0:
            with bot_engine._state_lock:
                state["cash_usdc"] = update.starting_capital
                ledger.set_cash(update.starting_capital)
                if state.get("portfolio_value_history"):
                    state["portfolio_value_history"][-1]["cash"] = update.starting_capital
                    state["portfolio_value_history"][-1]["total_equity"] = update.starting_capital
                config.save_state(state)
        cfg["starting_capital"] = update.starting_capital

    if update.poll_interval_seconds is not None:
        cfg["poll_interval_seconds"] = max(5, update.poll_interval_seconds)

    if update.execution_mode is not None:
        if update.execution_mode in ["whale_price", "market_price"]:
            cfg["execution_mode"] = update.execution_mode

    if update.slippage_bps is not None:
        cfg["slippage_bps"] = max(0.0, update.slippage_bps)

    if update.min_copy_price is not None:
        cfg["min_copy_price"] = max(0.0, min(1.0, update.min_copy_price))

    if update.max_copy_price is not None:
        cfg["max_copy_price"] = max(0.0, min(1.0, update.max_copy_price))

    if update.copy_only_best_wins is not None:
        cfg["copy_only_best_wins"] = update.copy_only_best_wins

    if update.min_best_bet_score is not None:
        cfg["min_best_bet_score"] = max(10, min(100, update.min_best_bet_score))

    if update.max_days_to_resolution is not None:
        cfg["max_days_to_resolution"] = max(1, update.max_days_to_resolution)

    if update.exclude_sports_bets is not None:
        cfg["exclude_sports_bets"] = update.exclude_sports_bets

    if update.exclude_crypto_bets is not None:
        cfg["exclude_crypto_bets"] = update.exclude_crypto_bets

    if update.exclude_weather_bets is not None:
        cfg["exclude_weather_bets"] = update.exclude_weather_bets

    if update.niche_priority_active is not None:
        cfg["niche_priority_active"] = update.niche_priority_active

    if update.dynamic_sizing_active is not None:
        cfg["dynamic_sizing_active"] = update.dynamic_sizing_active

    if update.min_market_liquidity is not None:
        cfg["min_market_liquidity"] = max(0.0, update.min_market_liquidity)

    if update.min_market_volume is not None:
        cfg["min_market_volume"] = max(0.0, update.min_market_volume)

    if update.enable_value_plays is not None:
        cfg["enable_value_plays"] = update.enable_value_plays

    if update.max_holding_hours is not None:
        cfg["max_holding_hours"] = max(0, update.max_holding_hours)

    if update.take_profit_pct is not None:
        cfg["take_profit_pct"] = max(0.0, update.take_profit_pct)

    if update.stop_loss_pct is not None:
        cfg["stop_loss_pct"] = max(0.0, update.stop_loss_pct)

    if update.min_whale_trade_size is not None:
        cfg["min_whale_trade_size"] = max(0.0, update.min_whale_trade_size)

    if update.max_market_exposure is not None:
        cfg["max_market_exposure"] = max(1.0, update.max_market_exposure)

    config.save_config(cfg)
    # Never return api_token in response
    safe = {k: v for k, v in cfg.items() if k != "api_token"}
    return {"status": "success", "config": safe}


@app.post("/api/traders/follow", dependencies=[Depends(require_mutate_auth)])
def follow_trader(trader: FollowTrader):
    cfg = config.load_config()
    addr_clean = trader.address.strip().lower()
    if not addr_clean.startswith("0x") or len(addr_clean) != 42:
        raise HTTPException(status_code=400, detail="Invalid Ethereum address format.")

    for t in cfg["followed_traders"]:
        if t["address"].lower() == addr_clean:
            t["name"] = trader.name
            t["sizing_type"] = trader.sizing_type
            t["sizing_value"] = trader.sizing_value
            config.save_config(cfg)
            return {"status": "success", "message": f"Updated settings for followed trader: {trader.name}"}

    cfg["followed_traders"].append({
        "address": addr_clean,
        "name": trader.name,
        "enabled": True,
        "sizing_type": trader.sizing_type,
        "sizing_value": trader.sizing_value,
    })

    config.save_config(cfg)

    state = config.load_state(cfg)
    with bot_engine._state_lock:
        config.add_log(state, f"Started following trader: {trader.name} ({addr_clean})")
        config.save_state(state)

    return {"status": "success", "followed_traders": cfg["followed_traders"]}


@app.post("/api/traders/toggle", dependencies=[Depends(require_mutate_auth)])
def toggle_trader(toggle: ToggleTrader):
    cfg = config.load_config()
    addr_clean = toggle.address.strip().lower()

    found = False
    for t in cfg["followed_traders"]:
        if t["address"].lower() == addr_clean:
            t["enabled"] = toggle.enabled
            if toggle.sizing_type is not None:
                t["sizing_type"] = toggle.sizing_type
            if toggle.sizing_value is not None:
                t["sizing_value"] = toggle.sizing_value
            found = True
            break

    if not found:
        raise HTTPException(status_code=404, detail="Trader not found in follow list.")

    config.save_config(cfg)

    state = config.load_state(cfg)
    with bot_engine._state_lock:
        status_txt = "enabled" if toggle.enabled else "disabled"
        config.add_log(state, f"Trader {addr_clean} copy execution {status_txt}.")
        config.save_state(state)

    return {"status": "success", "followed_traders": cfg["followed_traders"]}


@app.post("/api/traders/unfollow", dependencies=[Depends(require_mutate_auth)])
def unfollow_trader(unfollow: UnfollowTrader):
    cfg = config.load_config()
    addr_clean = unfollow.address.strip().lower()

    initial_len = len(cfg["followed_traders"])
    cfg["followed_traders"] = [t for t in cfg["followed_traders"] if t["address"].lower() != addr_clean]

    if len(cfg["followed_traders"]) == initial_len:
        raise HTTPException(status_code=404, detail="Trader not found in follow list.")

    config.save_config(cfg)

    state = config.load_state(cfg)
    with bot_engine._state_lock:
        config.add_log(state, f"Stopped following trader: {addr_clean}")
        config.save_state(state)

    return {"status": "success", "followed_traders": cfg["followed_traders"]}


@app.post("/api/control/start", dependencies=[Depends(require_mutate_auth)])
def start_simulation():
    cfg = config.load_config()
    cfg["simulation_active"] = True
    config.save_config(cfg)

    state = config.load_state(cfg)
    with bot_engine._state_lock:
        config.add_log(state, "Simulation started by user.")
        config.save_state(state)

    return {"status": "success", "simulation_active": True}


@app.post("/api/control/stop", dependencies=[Depends(require_mutate_auth)])
def stop_simulation():
    cfg = config.load_config()
    cfg["simulation_active"] = False
    config.save_config(cfg)

    state = config.load_state(cfg)
    with bot_engine._state_lock:
        config.add_log(state, "Simulation stopped/paused by user.")
        config.save_state(state)

    return {"status": "success", "simulation_active": False}


@app.post("/api/control/reset", dependencies=[Depends(require_mutate_auth)])
def reset_simulation(body: ResetRequest = ResetRequest()):
    """
    Archive ledger then re-init. Requires confirm=true and confirm_phrase=RESET.
    Never silently wipes research history.
    """
    if not body.confirm or (body.confirm_phrase or "").strip().upper() != "RESET":
        raise HTTPException(
            status_code=400,
            detail=(
                "Reset refused. Send JSON "
                '{"confirm": true, "confirm_phrase": "RESET"} '
                "after exporting/reconciling. Prior ledger will be archived, not deleted silently."
            ),
        )
    cfg = config.load_config()
    with bot_engine._state_lock:
        archive = config.reset_simulation(cfg["starting_capital"], confirm=True)
    return {
        "status": "success",
        "message": "Simulation archived and re-initialized.",
        "archive_path": archive,
    }


@app.post("/api/traders/sync-leaderboard", dependencies=[Depends(require_mutate_auth)])
def sync_leaderboard():
    success = bot_engine.sync_whales_from_leaderboard(time_period=["WEEK", "MONTH", "ALL"], limit=1000)
    if success:
        return {"status": "success", "message": "Synced top whales from WEEK, MONTH, and ALL leaderboards."}
    raise HTTPException(status_code=500, detail="Failed to sync whales from leaderboards.")


@app.get("/api/leaderboard")
def get_leaderboard(timePeriod: str = "WEEK"):
    timePeriod = timePeriod.upper()
    if timePeriod not in ["DAY", "WEEK", "ALL"]:
        timePeriod = "WEEK"

    try:
        url = f"https://data-api.polymarket.com/v1/leaderboard?timePeriod={timePeriod}"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            return res.json()
        raise HTTPException(status_code=502, detail=f"Polymarket leaderboard API returned code {res.status_code}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching leaderboard: {str(e)}")


# Ensure frontend directories exist
PUBLIC_DIR = os.path.join(config.BASE_DIR, "public")
if not os.path.exists(PUBLIC_DIR):
    os.makedirs(PUBLIC_DIR)

# Mount frontend files at the root
app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="public")

if __name__ == "__main__":
    # Local dev: allow open mutations unless token set
    if not os.environ.get("POLYCOPY_API_TOKEN") and not os.environ.get("POLYCOPY_ALLOW_OPEN_MUTATIONS"):
        os.environ["POLYCOPY_ALLOW_OPEN_MUTATIONS"] = "1"
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)

import os
import json
import time
import requests

import ledger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# A deployment can point this at a persistent disk (for example /var/data on
# Render). Local development keeps the repository-relative data directory.
DATA_DIR = os.environ.get("POLYCOPY_DATA_DIR", os.path.join(BASE_DIR, "data"))

def _verify_data_dir(d_dir):
    try:
        os.makedirs(d_dir, exist_ok=True)
        # Try writing a temporary file to check write access
        test_file = os.path.join(d_dir, ".write_test")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        return d_dir
    except Exception:
        fallback = os.path.join(BASE_DIR, "data")
        print(f"WARNING: Configured DATA_DIR '{d_dir}' is not writable. Falling back to local data directory '{fallback}'.")
        os.makedirs(fallback, exist_ok=True)
        return fallback

DATA_DIR = _verify_data_dir(DATA_DIR)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
LEDGER_PATH = os.path.join(DATA_DIR, "ledger.db")

DEFAULT_CONFIG = {
    "starting_capital": 10000.0,
    "poll_interval_seconds": 10,
    # Realistic fills: use live CLOB ask/bid, not whale print price
    "execution_mode": "market_price",  # "whale_price" or "market_price"
    "slippage_bps": 25.0,  # 25 bps extra cushion on top of live book
    "min_copy_price": 0.45,
    "max_copy_price": 0.80,
    "copy_only_best_wins": True,
    "min_best_bet_score": 60,
    "max_days_to_resolution": 60,
    # 0 = hold to resolution/maturity/whale sell (no time force-exit)
    "max_holding_hours": 0,
    # Prediction-market edge: hold thesis; only lock near-certain or catastrophe
    "take_profit_pct": 0.0,
    "value_play_take_profit_pct": 0.0,
    "stop_loss_pct": 0.0,
    "stop_loss_grace_hours": 24.0,
    "min_whale_trade_size": 250.0,
    "max_market_exposure": 2500.0,
    "max_cluster_exposure": 5000.0,
    "min_market_liquidity": 2500.0,
    "min_market_volume": 10000.0,
    "min_whale_roi": 0.12,
    "max_whale_volume": 15000000.0,
    "enable_value_plays": True,
    "value_play_size_mult": 0.35,
    "exclude_sports_bets": True,
    "exclude_crypto_bets": True,
    "exclude_weather_bets": True,
    "simulation_active": True,
    "niche_priority_active": True,
    "dynamic_sizing_active": True,
    "enable_whale_auto_pruning": True,
    "min_whale_win_rate": 50.0,
    "whale_prune_min_trades": 5,
    # Disable whale after cumulative realized PnL on copies falls below this
    "min_whale_copy_pnl": -150.0,
    "maturity_threshold": 0.98,
    "leaderboard_sync_limit": 25,
    "catastrophic_stop_loss_pct": 45.0,
    # Protect positions opened before performance-v2 from strategy exits
    "grandfather_open_positions": True,
    # Require N distinct whales for ALL entries (consensus > single lagging whale)
    "multi_whale_confirm_count": 2,
    "multi_whale_window_seconds": 7200,
    "multi_whale_require_all": False,
    # Auto-disable wallets whose recent activity is mostly sports
    "enable_sports_whale_filter": True,
    "sports_whale_activity_ratio": 0.50,
    "sports_whale_sample_size": 20,
    # Freshness: ignore whale prints older than this (seconds)
    "max_trade_age_seconds": 300,
    # Skip BUY if live ask is worse than whale price by more than this (bps)
    "max_adverse_slippage_bps": 150.0,
    # Cap each new BUY at this % of total equity (risk control)
    "risk_per_trade_pct": 8.0,
    # Poll each enabled whale's trade feed (throttled vs main loop to avoid 429s)
    "enable_per_whale_poll": True,
    "per_whale_poll_limit": 15,
    "per_whale_poll_interval_seconds": 30,
    "per_whale_max_parallel": 4,
    # Prefer mid-price edge band slightly in scoring
    "performance_strategy_version": 3,
    "performance_v2_migrated": False,
    "performance_v3_migrated": False,
    "performance_v3_1_migrated": True,
    # Ledger / conservative fills
    "use_ledger": True,
    "depth_aware_fills": True,
    "fill_fee_bps": 0.0,
    "fill_tick_size": 0.01,
    "fill_min_order_size": 1.0,
    "fill_allow_partial": True,
    # Never fall back to whale print when book missing
    "reject_if_book_unavailable": True,
    # Mark holdings at executable bid; also report mid NAV
    "mark_at_bid": True,
    # Auth for mutating API routes (set POLYCOPY_API_TOKEN env or here)
    "api_token": "",
    "cors_origins": [],
    "followed_traders": [
        {
            "address": "0x56687bf447db6ffa42ffe2204a05edaa20f55839",
            "name": "Theo4 (Rank 1)",
            "enabled": True,
            "sizing_type": "fixed",
            "sizing_value": 200.0
        },
        {
            "address": "0x1f2dd6d473f3e824cd2f8a89d9c69fb96f6ad0cf",
            "name": "Fredi9999 (Rank 2)",
            "enabled": True,
            "sizing_type": "fixed",
            "sizing_value": 200.0
        },
        {
            "address": "0x6a72f61820b26b1fe4d956e17b6dc2a1ea3033ee",
            "name": "kch123 (Rank 3)",
            "enabled": True,
            "sizing_type": "fixed",
            "sizing_value": 200.0
        }
    ]
}

def ensure_data_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

# Gist persistence helpers for Render stateless hosting
GIST_CACHE_FILE = os.path.join(DATA_DIR, ".gist_id")
_gist_id_cache = None

def get_github_headers():
    token = os.environ.get("GITHUB_TOKEN")
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

def get_or_create_gist():
    global _gist_id_cache
    if _gist_id_cache:
        return _gist_id_cache
        
    if os.path.exists(GIST_CACHE_FILE):
        try:
            with open(GIST_CACHE_FILE, "r") as f:
                _gist_id_cache = f.read().strip()
            if _gist_id_cache:
                return _gist_id_cache
        except Exception:
            pass
            
    headers = get_github_headers()
    try:
        r = requests.get("https://api.github.com/gists", headers=headers, timeout=10)
        if r.status_code == 200:
            gists = r.json()
            for gist in gists:
                files = gist.get("files", {})
                if "polycopy_state.json" in files:
                    _gist_id_cache = gist["id"]
                    try:
                        with open(GIST_CACHE_FILE, "w") as f:
                            f.write(_gist_id_cache)
                    except Exception:
                        pass
                    return _gist_id_cache
    except Exception as e:
        print(f"Error listing gists: {e}")

    try:
        payload = {
            "description": "PolyCopy Simulator State",
            "public": False,
            "files": {
                "polycopy_state.json": {"content": "{}"},
                "polycopy_config.json": {"content": "{}"}
            }
        }
        r = requests.post("https://api.github.com/gists", headers=headers, json=payload, timeout=10)
        if r.status_code == 201:
            _gist_id_cache = r.json()["id"]
            try:
                with open(GIST_CACHE_FILE, "w") as f:
                    f.write(_gist_id_cache)
            except Exception:
                pass
            return _gist_id_cache
    except Exception as e:
        print(f"Error creating gist: {e}")
        
    return None

def fetch_from_gist(filename):
    gist_id = get_or_create_gist()
    if not gist_id:
        return None
    headers = get_github_headers()
    try:
        r = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=10)
        if r.status_code == 200:
            files = r.json().get("files", {})
            file_data = files.get(filename, {})
            content = file_data.get("content")
            if content:
                try:
                    return json.loads(content)
                except Exception:
                    pass
    except Exception as e:
        print(f"Error fetching {filename} from gist: {e}")
    return None

def save_to_gist_async(filename, data):
    gist_id = get_or_create_gist()
    if not gist_id:
        return
    headers = get_github_headers()
    try:
        payload = {
            "files": {
                filename: {
                    "content": json.dumps(data, indent=2)
                }
            }
        }
        requests.patch(f"https://api.github.com/gists/{gist_id}", headers=headers, json=payload, timeout=15)
    except Exception as e:
        print(f"Error updating Gist {filename}: {e}")

_has_loaded_config_from_gist = False

def load_config():
    ensure_data_dir()
    
    global _has_loaded_config_from_gist
    github_token = os.environ.get("GITHUB_TOKEN")
    
    config_data = None
    if github_token and not _has_loaded_config_from_gist:
        _has_loaded_config_from_gist = True
        gist_config = fetch_from_gist("polycopy_config.json")
        if gist_config and isinstance(gist_config, dict) and gist_config:
            config_data = gist_config
            
    if not config_data:
        if not os.path.exists(CONFIG_PATH):
            save_config(DEFAULT_CONFIG)
            return DEFAULT_CONFIG
        try:
            with open(CONFIG_PATH, "r") as f:
                config_data = json.load(f)
        except Exception as e:
            print(f"Error loading config: {e}. Resetting to default.")
            save_config(DEFAULT_CONFIG)
            return DEFAULT_CONFIG

    # Ensure all keys from DEFAULT_CONFIG exist
    updated = False
    for k, v in DEFAULT_CONFIG.items():
        if k not in config_data:
            config_data[k] = v
            updated = True
            
    # Migration: Change max_days_to_resolution to 90
    if config_data.get("max_days_to_resolution") in [1, 7, 30]:
        config_data["max_days_to_resolution"] = 90
        updated = True

    # Migration: Change poll_interval_seconds to 15 if it was 30
    if config_data.get("poll_interval_seconds") == 30:
        config_data["poll_interval_seconds"] = 15
        updated = True

    # Migration: Force exclude_weather_bets to True
    if config_data.get("exclude_weather_bets") is not True:
        config_data["exclude_weather_bets"] = True
        updated = True
        
    # Migration: Change min_copy_price to 0.40
    if config_data.get("min_copy_price") == 0.70:
        config_data["min_copy_price"] = 0.40
        updated = True
        
    # Migration: Change min_market_liquidity default to 1000.0
    if config_data.get("min_market_liquidity") == 5000.0:
        config_data["min_market_liquidity"] = 1000.0
        updated = True
        
    # Migration: Change min_market_volume default to 5000.0
    if config_data.get("min_market_volume") == 20000.0:
        config_data["min_market_volume"] = 5000.0
        updated = True

    # Migration: Force copy_only_best_wins to True if it was False
    if config_data.get("copy_only_best_wins") is False:
        config_data["copy_only_best_wins"] = True
        updated = True

    # Migration: Change max_copy_price to 0.85 if it was 0.95
    if config_data.get("max_copy_price") == 0.95:
        config_data["max_copy_price"] = 0.85
        updated = True

    # Migration: Mark existing auto-synced followed traders (excluding default hand-picked ones) with auto_synced = True
    default_addresses = {
        "0x56687bf447db6ffa42ffe2204a05edaa20f55839",
        "0x1f2dd6d473f3e824cd2f8a89d9c69fb96f6ad0cf",
        "0x6a72f61820b26b1fe4d956e17b6dc2a1ea3033ee"
    }
    for trader in config_data.get("followed_traders", []):
        addr = trader.get("address", "").strip().lower()
        if addr not in default_addresses and "auto_synced" not in trader:
            trader["auto_synced"] = True
            updated = True

    # Performance strategy v2: hold-to-resolution defaults + tighter whale filters.
    # Use dedicated migrated flag — DEFAULT may inject performance_strategy_version=2
    # before this block runs, so do not rely on version alone.
    if not config_data.get("performance_v2_migrated"):
        config_data["performance_v2_migrated"] = True
        config_data["performance_strategy_version"] = 2
        config_data["max_holding_hours"] = 0
        config_data["take_profit_pct"] = 25.0
        config_data["value_play_take_profit_pct"] = 50.0
        config_data["stop_loss_pct"] = 30.0
        config_data["stop_loss_grace_hours"] = 24.0
        config_data["catastrophic_stop_loss_pct"] = 40.0
        config_data["min_whale_trade_size"] = 1000.0
        config_data["min_whale_roi"] = 0.08
        config_data["leaderboard_sync_limit"] = 25
        config_data["min_best_bet_score"] = 60
        config_data["value_play_size_mult"] = 0.5
        config_data["grandfather_open_positions"] = True
        config_data["multi_whale_confirm_count"] = 2
        config_data["multi_whale_window_seconds"] = 3600
        config_data["multi_whale_require_all"] = False
        config_data["enable_sports_whale_filter"] = True
        config_data["sports_whale_activity_ratio"] = 0.55
        config_data["sports_whale_sample_size"] = 20
        config_data["whale_prune_min_trades"] = 3
        updated = True

    # Performance strategy v3: consensus entries, realistic fills, risk caps, freshness.
    if not config_data.get("performance_v3_migrated"):
        config_data["performance_v3_migrated"] = True
        config_data["performance_strategy_version"] = 3
        config_data["execution_mode"] = "market_price"
        config_data["slippage_bps"] = 25.0
        config_data["poll_interval_seconds"] = 10
        config_data["min_copy_price"] = 0.45
        config_data["max_copy_price"] = 0.80
        config_data["min_best_bet_score"] = 70
        config_data["max_days_to_resolution"] = 60
        config_data["max_holding_hours"] = 0
        config_data["take_profit_pct"] = 0.0
        config_data["value_play_take_profit_pct"] = 0.0
        config_data["stop_loss_pct"] = 0.0
        config_data["catastrophic_stop_loss_pct"] = 45.0
        config_data["min_whale_trade_size"] = 2000.0
        config_data["max_market_exposure"] = 750.0
        config_data["max_cluster_exposure"] = 1000.0
        config_data["min_market_liquidity"] = 2500.0
        config_data["min_market_volume"] = 10000.0
        config_data["min_whale_roi"] = 0.12
        config_data["max_whale_volume"] = 15000000.0
        config_data["value_play_size_mult"] = 0.35
        config_data["min_whale_win_rate"] = 50.0
        config_data["whale_prune_min_trades"] = 5
        config_data["min_whale_copy_pnl"] = -150.0
        config_data["leaderboard_sync_limit"] = 15
        config_data["multi_whale_confirm_count"] = 2
        config_data["multi_whale_window_seconds"] = 7200
        config_data["multi_whale_require_all"] = True
        config_data["enable_sports_whale_filter"] = True
        config_data["sports_whale_activity_ratio"] = 0.50
        config_data["max_trade_age_seconds"] = 300
        config_data["max_adverse_slippage_bps"] = 150.0
        config_data["risk_per_trade_pct"] = 2.0
        config_data["enable_per_whale_poll"] = True
        config_data["per_whale_poll_limit"] = 15
        config_data["per_whale_poll_interval_seconds"] = 30
        config_data["per_whale_max_parallel"] = 4
        config_data["enable_value_plays"] = True
        config_data["copy_only_best_wins"] = True
        config_data["exclude_sports_bets"] = True
        config_data["exclude_crypto_bets"] = True
        config_data["exclude_weather_bets"] = True
        updated = True

    # Performance strategy v3.1: restore continuous order flow while protecting grandfathered open positions
    if not config_data.get("performance_v3_1_migrated"):
        config_data["performance_v3_1_migrated"] = True
        config_data["multi_whale_require_all"] = False
        config_data["min_whale_trade_size"] = 250.0
        config_data["min_best_bet_score"] = 60
        config_data["leaderboard_sync_limit"] = 25
        config_data["max_market_exposure"] = 2500.0
        config_data["max_cluster_exposure"] = 5000.0
        config_data["grandfather_open_positions"] = True
        config_data["take_profit_pct"] = 0.0
        config_data["stop_loss_pct"] = 0.0
        config_data["max_holding_hours"] = 0
        updated = True

    if updated:
        save_config(config_data)
        
    return config_data

def save_config(config):
    ensure_data_dir()
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
        
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        import threading
        threading.Thread(target=save_to_gist_async, args=("polycopy_config.json", config), daemon=True).start()

def get_initial_state(starting_capital):
    """In-memory empty projection (ledger is source of truth when enabled)."""
    return {
        "cash_usdc": starting_capital,
        "portfolio_value_history": [
            {
                "timestamp": int(time.time()),
                "cash": starting_capital,
                "holdings_value": 0.0,
                "holdings_value_mid": 0.0,
                "total_equity": starting_capital,
                "total_equity_mid": starting_capital,
            }
        ],
        "positions": {},
        "trades": [],
        "whale_positions": {},
        "processed_tx_hashes": [],
        "processed_keys": [],
        "logs": [
            {
                "timestamp": int(time.time()),
                "message": "Simulation state initialized."
            }
        ],
        "ledger_backed": True,
    }

_has_loaded_state_from_gist = False
_ledger_bootstrapped = False


def _load_json_state_file(config):
    """Load legacy state.json without reinitializing on parse errors."""
    if not os.path.exists(STATE_PATH):
        return None
    try:
        with open(STATE_PATH, "r") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            print("Error loading state: root is not an object. Leaving file intact.")
            return None
        required_keys = [
            "cash_usdc", "portfolio_value_history", "positions", "trades",
            "whale_positions", "processed_tx_hashes", "logs",
        ]
        for key in required_keys:
            if key not in state:
                if key == "cash_usdc":
                    state[key] = config["starting_capital"]
                elif key == "portfolio_value_history":
                    state[key] = [{
                        "timestamp": int(time.time()),
                        "cash": state.get("cash_usdc", config["starting_capital"]),
                        "holdings_value": 0.0,
                        "total_equity": state.get("cash_usdc", config["starting_capital"]),
                    }]
                elif key in ["positions", "whale_positions"]:
                    state[key] = {}
                elif key in ["trades", "processed_tx_hashes", "logs"]:
                    state[key] = []
        return state
    except Exception as e:
        # CRITICAL: do not wipe research state on load errors
        print(f"Error loading state.json: {e}. File left intact; returning empty projection.")
        return None


def bootstrap_ledger(config=None):
    """
    Ensure SQLite ledger exists. Migrate state.json once if present and ledger empty.
    """
    global _ledger_bootstrapped, _has_loaded_state_from_gist
    if config is None:
        config = load_config()
    ensure_data_dir()
    ledger.configure(LEDGER_PATH)
    ledger.get_connection()

    starting = float(config.get("starting_capital", 10000.0))

    # Optional one-time pull of Gist state into state.json for migration
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token and not _has_loaded_state_from_gist:
        _has_loaded_state_from_gist = True
        if not os.path.exists(STATE_PATH):
            gist_state = fetch_from_gist("polycopy_state.json")
            if gist_state and isinstance(gist_state, dict) and gist_state:
                try:
                    with open(STATE_PATH, "w") as f:
                        json.dump(gist_state, f, indent=2)
                    print("Pulled state from Gist into state.json for ledger migration.")
                except Exception as e:
                    print(f"Could not write Gist state to disk: {e}")

    if not ledger.is_initialized():
        json_state = _load_json_state_file(config)
        if json_state and (
            json_state.get("positions")
            or json_state.get("trades")
            or abs(float(json_state.get("cash_usdc", starting)) - starting) > 0.01
        ):
            report = ledger.migrate_from_json_state(json_state, starting_capital=starting)
            print(f"Ledger migration report: {report}")
        else:
            ledger.init_account(starting, note="bootstrap")
    elif not ledger.get_all_open_lots() and not ledger.get_trades(limit=1):
        # Initialized empty — still try migrate if JSON has positions we don't have
        json_state = _load_json_state_file(config)
        if json_state and json_state.get("positions"):
            report = ledger.migrate_from_json_state(json_state, starting_capital=starting)
            print(f"Ledger late migration report: {report}")

    _ledger_bootstrapped = True
    return ledger.project_state()


def load_state(config=None):
    ensure_data_dir()
    if config is None:
        config = load_config()

    use_ledger = config.get("use_ledger", True)
    if use_ledger:
        global _ledger_bootstrapped
        if not _ledger_bootstrapped:
            return bootstrap_ledger(config)
        ledger.configure(LEDGER_PATH)
        if not ledger.is_initialized():
            ledger.init_account(float(config.get("starting_capital", 10000.0)))
        return ledger.project_state()

    # Legacy JSON path (discouraged)
    global _has_loaded_state_from_gist
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token and not _has_loaded_state_from_gist:
        _has_loaded_state_from_gist = True
        gist_state = fetch_from_gist("polycopy_state.json")
        if gist_state and isinstance(gist_state, dict) and gist_state:
            with open(STATE_PATH, "w") as f:
                json.dump(gist_state, f, indent=2)
            return gist_state

    state = _load_json_state_file(config)
    if state is None:
        # Only create initial state when no file exists
        if not os.path.exists(STATE_PATH):
            initial_state = get_initial_state(config["starting_capital"])
            save_state(initial_state)
            return initial_state
        # Corrupt file: return safe empty without overwriting
        return get_initial_state(config["starting_capital"])
    return state


_last_gist_save_data = None
_last_json_export_ts = 0.0


def save_state(state):
    """
    Persist projection. With ledger, cash/equity snapshot are written to SQLite;
    trade/lot mutations should already have been recorded via ledger APIs.
    Also exports a JSON mirror for dashboard backups (never the sole source of truth).
    """
    global _last_json_export_ts, _last_gist_save_data
    ensure_data_dir()
    config = None
    try:
        config = load_config()
    except Exception:
        pass
    use_ledger = True if config is None else config.get("use_ledger", True)

    if use_ledger:
        ledger.configure(LEDGER_PATH)
        # Sync cash from working state if engine mutated it
        try:
            if "cash_usdc" in state:
                ledger.set_cash(float(state["cash_usdc"]), reason="save_state_sync")
        except Exception as e:
            print(f"Ledger cash sync error: {e}")

        # Export lightweight JSON mirror for ops (not authoritative) and sync to Gist
        now = time.time()
        if now - _last_json_export_ts >= 30:
            _last_json_export_ts = now
            try:
                mirror = ledger.project_state()
                # Preserve any in-memory position marks
                if state.get("positions"):
                    mirror["positions"] = state["positions"]
                    mirror["cash_usdc"] = state.get("cash_usdc", mirror["cash_usdc"])
                with open(STATE_PATH, "w") as f:
                    json.dump(mirror, f, indent=2)

                # Sync to Gist for Render stateless hosting
                github_token = os.environ.get("GITHUB_TOKEN")
                if github_token:
                    core_str = json.dumps({
                        "cash_usdc": mirror.get("cash_usdc"),
                        "positions": mirror.get("positions"),
                        "trades_count": len(mirror.get("trades", []))
                    })
                    if _last_gist_save_data != core_str:
                        _last_gist_save_data = core_str
                        import threading
                        threading.Thread(target=save_to_gist_async, args=("polycopy_state.json", mirror), daemon=True).start()
            except Exception as e:
                print(f"JSON mirror export failed (ledger intact): {e}")
        return

    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)

    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        core_str = json.dumps({
            "cash_usdc": state.get("cash_usdc"),
            "positions": state.get("positions"),
            "trades_count": len(state.get("trades", []))
        })
        if _last_gist_save_data != core_str:
            _last_gist_save_data = core_str
            import threading
            threading.Thread(target=save_to_gist_async, args=("polycopy_state.json", state), daemon=True).start()


def add_log(state, message):
    log_entry = {
        "timestamp": int(time.time()),
        "message": message
    }
    state.setdefault("logs", []).append(log_entry)
    if len(state["logs"]) > 500:
        state["logs"] = state["logs"][-500:]
    try:
        ledger.add_log(message, ts=log_entry["timestamp"])
    except Exception:
        print(f"[LOG] {message}")


def reset_simulation(starting_capital: float, confirm: bool = False) -> str:
    """Archive ledger and re-init. Requires confirm=True."""
    ledger.configure(LEDGER_PATH)
    archive = ledger.archive_and_reset(starting_capital, confirm=confirm)
    global _ledger_bootstrapped
    _ledger_bootstrapped = True
    return archive

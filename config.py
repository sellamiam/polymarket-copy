import os
import json
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
STATE_PATH = os.path.join(DATA_DIR, "state.json")

DEFAULT_CONFIG = {
    "starting_capital": 10000.0,
    "poll_interval_seconds": 30,
    "execution_mode": "whale_price",  # "whale_price" or "market_price"
    "slippage_bps": 0,  # 0 bps = 0%
    "min_copy_price": 0.50,
    "max_copy_price": 0.95,
    "copy_only_best_wins": False,
    "min_best_bet_score": 65,
    "max_days_to_resolution": 7,
    "exclude_sports_bets": True,
    "simulation_active": False,
    "followed_traders": [
        {
            "address": "0x56687bf447db6ffa42ffe2204a05edaa20f55839",
            "name": "Theo4 (Rank 1)",
            "enabled": True,
            "sizing_type": "fixed",
            "sizing_value": 100.0
        },
        {
            "address": "0x1f2dd6d473f3e824cd2f8a89d9c69fb96f6ad0cf",
            "name": "Fredi9999 (Rank 2)",
            "enabled": True,
            "sizing_type": "fixed",
            "sizing_value": 100.0
        },
        {
            "address": "0x6a72f61820b26b1fe4d956e17b6dc2a1ea3033ee",
            "name": "kch123 (Rank 3)",
            "enabled": True,
            "sizing_type": "fixed",
            "sizing_value": 100.0
        }
    ]
}

def ensure_data_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

def load_config():
    ensure_data_dir()
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    try:
        with open(CONFIG_PATH, "r") as f:
            config = json.load(f)
            # Ensure all keys from DEFAULT_CONFIG exist
            for k, v in DEFAULT_CONFIG.items():
                if k not in config:
                    config[k] = v
            return config
    except Exception as e:
        print(f"Error loading config: {e}. Resetting to default.")
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG

def save_config(config):
    ensure_data_dir()
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

def get_initial_state(starting_capital):
    return {
        "cash_usdc": starting_capital,
        "portfolio_value_history": [
            {
                "timestamp": int(time.time()),
                "cash": starting_capital,
                "holdings_value": 0.0,
                "total_equity": starting_capital
            }
        ],
        "positions": {},  # token_id -> position dict
        "trades": [],  # list of trade dicts
        "whale_positions": {},  # trader_address -> {token_id -> quantity}
        "processed_tx_hashes": [],  # list of tx hashes
        "logs": [
            {
                "timestamp": int(time.time()),
                "message": "Simulation state initialized."
            }
        ]
    }

def load_state(config=None):
    ensure_data_dir()
    if config is None:
        config = load_config()
    
    if not os.path.exists(STATE_PATH):
        initial_state = get_initial_state(config["starting_capital"])
        save_state(initial_state)
        return initial_state
    
    try:
        with open(STATE_PATH, "r") as f:
            state = json.load(f)
            # Integrity check
            required_keys = ["cash_usdc", "portfolio_value_history", "positions", "trades", "whale_positions", "processed_tx_hashes", "logs"]
            for key in required_keys:
                if key not in state:
                    if key == "cash_usdc":
                        state[key] = config["starting_capital"]
                    elif key == "portfolio_value_history":
                        state[key] = [{"timestamp": int(time.time()), "cash": state.get("cash_usdc", config["starting_capital"]), "holdings_value": 0.0, "total_equity": state.get("cash_usdc", config["starting_capital"])}]
                    elif key in ["positions", "whale_positions"]:
                        state[key] = {}
                    elif key in ["trades", "processed_tx_hashes", "logs"]:
                        state[key] = []
            return state
    except Exception as e:
        print(f"Error loading state: {e}. Reinitializing.")
        initial_state = get_initial_state(config["starting_capital"])
        save_state(initial_state)
        return initial_state

def save_state(state):
    ensure_data_dir()
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)

def add_log(state, message):
    log_entry = {
        "timestamp": int(time.time()),
        "message": message
    }
    state["logs"].append(log_entry)
    if len(state["logs"]) > 500:
        state["logs"] = state["logs"][-500:]
    print(f"[LOG] {message}")

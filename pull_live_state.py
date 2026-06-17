import os
import sys
import json
import requests

# Live URL of the deployed app
LIVE_URL = "https://polymarket-copy.onrender.com"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
STATE_PATH = os.path.join(DATA_DIR, "state.json")

def main():
    print(f"Fetching live state from {LIVE_URL}...")
    try:
        response = requests.get(f"{LIVE_URL}/api/state", timeout=120)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"Error fetching state from live server: {e}")
        sys.exit(1)
    
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        
    # Extract config and state
    live_config = data.get("config")
    live_state = data.get("state")
    
    if live_config:
        with open(CONFIG_PATH, "w") as f:
            json.dump(live_config, f, indent=2)
        print("Successfully updated local data/config.json")
    else:
        print("Warning: 'config' field not found in response.")
        
    if live_state:
        with open(STATE_PATH, "w") as f:
            json.dump(live_state, f, indent=2)
        print("Successfully updated local data/state.json")
    else:
        print("Warning: 'state' field not found in response.")

if __name__ == "__main__":
    main()

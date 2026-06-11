import time
import requests
import threading
import traceback
import uuid
import json
from config import load_config, load_state, save_state, add_log
from concurrent.futures import ThreadPoolExecutor
import math

def calculate_conviction(usdc_size):
    if usdc_size <= 0:
        return 10
    # Logarithmic scaling of conviction score based on trade size
    # size of $10 -> ~35, $100 -> ~60, $1000 -> ~85, >=$10000 -> 100
    score = int(10 + 25 * math.log10(max(1.0, usdc_size)))
    return min(100, max(10, score))

import datetime

def parse_iso_datetime(dt_str):
    if not dt_str:
        return None
    try:
        if dt_str.endswith("Z"):
            dt_str = dt_str[:-1] + "+00:00"
        return datetime.datetime.fromisoformat(dt_str)
    except Exception as e:
        print(f"Error parsing datetime {dt_str}: {e}")
        return None

def fetch_market_details(condition_id):
    try:
        url = f"https://gamma-api.polymarket.com/markets?condition_ids={condition_id}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            markets = res.json()
            if isinstance(markets, list) and len(markets) > 0:
                for m in markets:
                    if m.get("conditionId") == condition_id:
                        return m
                return markets[0]
    except Exception as e:
        print(f"Error fetching market details for {condition_id}: {e}")
    return None


_engine_thread = None
_stop_event = threading.Event()
_state_lock = threading.Lock()

def fetch_clob_price(token_id, side):
    """
    Fetch live price for token_id.
    side can be 'buy' (bid) or 'sell' (ask).
    """
    try:
        url = f"https://clob.polymarket.com/price?token_id={token_id}&side={side}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if "price" in data:
                return float(data["price"])
    except Exception as e:
        print(f"Error fetching CLOB price for {token_id} ({side}): {e}")
    return None

def fetch_clob_midpoint(token_id):
    """
    Fetch live midpoint price for token_id.
    """
    try:
        url = f"https://clob.polymarket.com/midpoint?token_id={token_id}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if "mid" in data:
                return float(data["mid"])
    except Exception as e:
        print(f"Error fetching CLOB midpoint for {token_id}: {e}")
    return None

def calculate_total_equity(state, holdings_value):
    return state["cash_usdc"] + holdings_value

def update_live_valuations(state):
    """
    Polls the CLOB API to get the current midpoint price of all open positions,
    and updates their value. Returns the total value of all holdings.
    """
    total_holdings_value = 0.0
    for token_id, pos in list(state["positions"].items()):
        # Query CLOB midpoint price (fairest valuation)
        price = fetch_clob_midpoint(token_id)
        if price is not None:
            pos["current_price"] = price
        else:
            # Fallback to best bid
            bid = fetch_clob_price(token_id, "buy")
            price = bid if bid is not None else pos.get("current_price", pos["avg_price"])
            pos["current_price"] = price
        
        pos_value = pos["quantity"] * price
        total_holdings_value += pos_value
        pos["last_updated"] = int(time.time())
    
    return total_holdings_value

def resolve_positions(state):
    """
    Queries Gamma API to check if any open position's market is resolved.
    If resolved, settles the position (USDC payout = quantity * winning_outcome_price)
    """
    resolved_any = False
    for token_id, pos in list(state["positions"].items()):
        condition_id = pos["condition_id"]
        try:
            url = f"https://gamma-api.polymarket.com/markets?condition_ids={condition_id}"
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                markets = res.json()
                if isinstance(markets, list) and len(markets) > 0:
                    market = None
                    for m in markets:
                        if m.get("conditionId") == condition_id:
                            market = m
                            break
                    if not market:
                        market = markets[0]
                    is_resolved = market.get("resolved", False)
                    is_closed = market.get("closed", False)
                    outcome_prices_str = market.get("outcomePrices")
                    
                    # A market is resolved if resolved is true or closed is true with outcomePrices [1, 0] or similar
                    should_resolve = is_resolved
                    prices = None
                    if outcome_prices_str:
                        try:
                            prices = json.loads(outcome_prices_str)
                            # If closed and we have final binary outcomes
                            if is_closed and prices and any(float(p) >= 0.99 for p in prices):
                                should_resolve = True
                        except Exception:
                            pass
                    
                    if should_resolve and prices:
                        outcome_idx = pos.get("outcome_index", 0)
                        if outcome_idx < len(prices):
                            payout_per_share = float(prices[outcome_idx])
                            proceeds = pos["quantity"] * payout_per_share
                            cost_basis = pos["avg_price"] * pos["quantity"]
                            pnl = proceeds - cost_basis
                            
                            # Execute resolution
                            state["cash_usdc"] += proceeds
                            
                            # Record resolved trade
                            trade_id = str(uuid.uuid4())
                            state["trades"].append({
                                "id": trade_id,
                                "timestamp": int(time.time()),
                                "trader_address": "resolution",
                                "trader_name": "System Resolution",
                                "market_title": pos["market_title"],
                                "market_slug": pos["market_slug"],
                                "outcome": pos["outcome"],
                                "type": "RESOLVE",
                                "quantity": pos["quantity"],
                                "price": payout_per_share,
                                "usdc_size": proceeds,
                                "tx_hash": "resolution",
                                "realized_pnl": pnl
                            })
                            
                            # Delete position
                            del state["positions"][token_id]
                            resolved_any = True
                            
                            msg = f"RESOLVED: '{pos['market_title']}' ({pos['outcome']}) settled at {payout_per_share:.2f} USDC. Received {proceeds:.2f} USDC (PnL: {pnl:+.2f} USDC)."
                            add_log(state, msg)
        except Exception as e:
            print(f"Error checking resolution for condition {condition_id}: {e}")
            
    return resolved_any

def fetch_trader_activities(trader):
    address = trader["address"].lower()
    name = trader["name"]
    try:
        url = f"https://data-api.polymarket.com/activity?user={address}&limit=20"
        res = requests.get(url, timeout=7)
        if res.status_code == 200:
            activities = res.json()
            if isinstance(activities, list):
                return (trader, activities)
    except Exception as e:
        print(f"Error fetching activity for {name} ({address}): {e}")
    return (trader, [])

def run_simulation_iteration(config, state):
    """
    Executes a single check cycle using the global trades feed,
    cross-referencing with followed/enabled traders.
    """
    # Build maps/sets for followed traders
    active_traders_map = {t["address"].lower(): t for t in config["followed_traders"] if t.get("enabled", True)}
    if not active_traders_map:
        return False

    trade_executed_or_resolved = False

    # Check for resolutions first
    if resolve_positions(state):
        trade_executed_or_resolved = True

    # 1. Fetch global activities feed
    activities = []
    try:
        url = "https://data-api.polymarket.com/v1/trades?limit=200"
        res = requests.get(url, timeout=7)
        if res.status_code == 200:
            activities = res.json()
    except Exception as e:
        print(f"Error fetching global trades activity: {e}")

    if not isinstance(activities, list):
        return trade_executed_or_resolved

    # 2. Process transactions from oldest to newest
    for act in reversed(activities):
        tx_hash = act.get("transactionHash")
        if not tx_hash or tx_hash in state["processed_tx_hashes"]:
            continue

        # Skip historical trades older than 1 hour to prevent copying stale history
        timestamp = int(act.get("timestamp", 0))
        if time.time() - timestamp > 3600:  # 1 hour
            state["processed_tx_hashes"].append(tx_hash)
            continue

        # Get trader wallet address
        proxy_wallet = act.get("proxyWallet")
        if not proxy_wallet:
            continue
        address = proxy_wallet.strip().lower()

        # Check if this trader is in our active followed list
        if address not in active_traders_map:
            continue

        trader = active_traders_map[address]
        name = trader["name"]

        side = act.get("side")
        if side not in ["BUY", "SELL"]:
            continue

        # Core trade properties
        asset = act.get("asset")  # token_id
        if not asset:
            continue

        price = float(act.get("price", 0))
        size = float(act.get("size", 0))
        
        # Calculate usdc_size since v1/trades does not return usdcSize directly
        usdc_size = size * price

        outcome = act.get("outcome", "")
        outcome_index = int(act.get("outcomeIndex", 0))
        title = act.get("title", "")
        slug = act.get("slug", "")
        condition_id = act.get("conditionId", "")

        # Safeguard
        if price <= 0 or size <= 0:
            continue

        # 3. Calculate simulated copy size
        sizing_type = trader.get("sizing_type", "fixed")
        sizing_val = float(trader.get("sizing_value", 100))
        
        # Sizing calculation
        if sizing_type == "fixed":
            copy_usdc = sizing_val
        elif sizing_type == "multiplier":
            copy_usdc = usdc_size * sizing_val
        elif sizing_type == "proportional":
            # sizing_val is decimal fraction of total portfolio (e.g. 0.02 = 2%)
            # Update live value to get precise equity
            holdings_val = update_live_valuations(state)
            total_equity = calculate_total_equity(state, holdings_val)
            copy_usdc = total_equity * sizing_val
        else:
            copy_usdc = 100.0

        # 4. Handle execution
        if side == "BUY":
            win_probability = price * 100
            conviction_score = calculate_conviction(usdc_size)
            best_bet_score = int(win_probability * 0.6 + conviction_score * 0.4)

            # Check price range filters
            min_price = float(config.get("min_copy_price", 0.70))
            max_price = float(config.get("max_copy_price", 0.95))
            if price < min_price or price > max_price:
                add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name} @ {price:.3f} USDC: Outside price range [{min_price:.2f}, {max_price:.2f}] [Win Prob: {win_probability:.1f}%]")
                state["processed_tx_hashes"].append(tx_hash)
                continue

            # Check resolution time and category filters
            max_days = int(config.get("max_days_to_resolution", 7))
            if condition_id:
                market_details = fetch_market_details(condition_id)
                if market_details:
                    # Exclude sports bets filter
                    if config.get("exclude_sports_bets", True):
                        is_sports = False
                        # 1. Check official tags
                        tags = market_details.get("tags", [])
                        if tags:
                            for tag in tags:
                                if str(tag.get("id")) == "1" or tag.get("slug") == "sports":
                                    is_sports = True
                                    break
                        
                        # 2. Check feeType
                        fee_type = market_details.get("feeType", "").lower()
                        if "sports" in fee_type:
                            is_sports = True

                        # 3. Fallback text keyword matching
                        question_lower = market_details.get("question", "").lower()
                        slug_lower = market_details.get("slug", "").lower()
                        sports_keywords = [
                            " vs ", "vs.", "versus", "wnba", "nba", "nfl", "mlb", "nhl", "ufc", "mma", "pga", "mls",
                            "premier league", "champions league", "la liga", "bundesliga", "serie a", "atp", "wta",
                            "soccer", "football", "basketball", "baseball", "hockey", "tennis", "golf", "cricket", "rugby",
                            "boxing", "wrestling", "nascar", "formula 1", "f1", "grand prix", "athletics", "olympics",
                            "spread", "over/under", "o/u", "points", "rebounds", "assists", "goals", "runs", "touchdowns",
                            "homeruns", "strikeouts", "matchup", "beat the", "win the match", "win the game", "scoring",
                            "quarter", "halftime", "inning", "puck line", "moneyline", "touchdown", "interception"
                        ]
                        if any(kw in question_lower or kw in slug_lower for kw in sports_keywords):
                            is_sports = True

                        if is_sports:
                            add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name}: Sports bets are excluded.")
                            state["processed_tx_hashes"].append(tx_hash)
                            continue

                    # Expiry validation
                    end_date_str = market_details.get("endDate")
                    if end_date_str:
                        end_dt = parse_iso_datetime(end_date_str)
                        if end_dt:
                            now_utc = datetime.datetime.now(datetime.timezone.utc)
                            delta = end_dt - now_utc
                            days_left = delta.total_seconds() / (24 * 3600)
                            if days_left > max_days:
                                add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name}: Resolves in {days_left:.1f} days, exceeding limit of {max_days} days.")
                                state["processed_tx_hashes"].append(tx_hash)
                                continue
                            elif days_left < 0:
                                add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name}: Already passed resolution date.")
                                state["processed_tx_hashes"].append(tx_hash)
                                continue
                        else:
                            add_log(state, f"Skipped BUY on '{title}' from {name}: Could not parse end date '{end_date_str}'.")
                            state["processed_tx_hashes"].append(tx_hash)
                            continue
                    else:
                        add_log(state, f"Skipped BUY on '{title}' from {name}: Missing end date field.")
                        state["processed_tx_hashes"].append(tx_hash)
                        continue
                else:
                    add_log(state, f"Skipped BUY on '{title}' from {name}: Could not fetch market details.")
                    state["processed_tx_hashes"].append(tx_hash)
                    continue

            # Check best wins filter if enabled
            if config.get("copy_only_best_wins", False):
                min_best_score = int(config.get("min_best_bet_score", 65))
                if best_bet_score < min_best_score:
                    add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name}: Score {best_bet_score} is below minimum Best Bet Score {min_best_score} [Win Prob: {win_probability:.1f}%, Conviction: {conviction_score}%]")
                    state["processed_tx_hashes"].append(tx_hash)
                    continue

            if state["cash_usdc"] < 1.0:
                add_log(state, f"Skipped BUY on '{title}' from {name}: Insufficient cash (Balance: {state['cash_usdc']:.2f} USDC)")
                state["processed_tx_hashes"].append(tx_hash)
                continue

            # Settle size
            invest_usdc = min(copy_usdc, state["cash_usdc"])
            
            # Fetch live market execution price if required
            if config.get("execution_mode") == "market_price":
                live_price = fetch_clob_price(asset, "sell") # ask price
                if live_price is not None:
                    exec_price = live_price
                else:
                    exec_price = price
                    add_log(state, f"Could not fetch ask price for {title}. Defaulting to whale price {price:.2f}")
            else:
                exec_price = price

            # Apply slippage
            slippage_bps = float(config.get("slippage_bps", 10))
            exec_price = exec_price * (1.0 + (slippage_bps / 10000.0))
            
            if exec_price <= 0:
                state["processed_tx_hashes"].append(tx_hash)
                continue

            quantity = invest_usdc / exec_price
            
            # Deduct cash
            state["cash_usdc"] -= invest_usdc
            
            # Update positions
            if asset in state["positions"]:
                pos = state["positions"][asset]
                old_qty = pos["quantity"]
                old_avg = pos["avg_price"]
                new_qty = old_qty + quantity
                new_avg = (old_avg * old_qty + exec_price * quantity) / new_qty
                
                pos["quantity"] = new_qty
                pos["avg_price"] = new_avg
                pos["invested_usdc"] += invest_usdc
                
                # Update weighted averages for scores
                pos["win_probability"] = (pos.get("win_probability", win_probability) * old_qty + win_probability * quantity) / new_qty
                pos["conviction_score"] = int((pos.get("conviction_score", conviction_score) * old_qty + conviction_score * quantity) / new_qty)
                pos["best_bet_score"] = int((pos.get("best_bet_score", best_bet_score) * old_qty + best_bet_score * quantity) / new_qty)
                
                pos["last_updated"] = int(time.time())
            else:
                state["positions"][asset] = {
                    "token_id": asset,
                    "condition_id": condition_id,
                    "market_title": title,
                    "market_slug": slug,
                    "outcome": outcome,
                    "outcome_index": outcome_index,
                    "avg_price": exec_price,
                    "quantity": quantity,
                    "invested_usdc": invest_usdc,
                    "current_price": exec_price,
                    "win_probability": win_probability,
                    "conviction_score": conviction_score,
                    "best_bet_score": best_bet_score,
                    "last_updated": int(time.time())
                }

            # Update tracked whale holding
            if address not in state["whale_positions"]:
                state["whale_positions"][address] = {}
            state["whale_positions"][address][asset] = state["whale_positions"][address].get(asset, 0.0) + size

            # Record trade history
            trade_id = str(uuid.uuid4())
            state["trades"].append({
                "id": trade_id,
                "timestamp": int(time.time()),
                "trader_address": address,
                "trader_name": name,
                "market_title": title,
                "market_slug": slug,
                "outcome": outcome,
                "type": "BUY",
                "quantity": quantity,
                "price": exec_price,
                "usdc_size": invest_usdc,
                "win_probability": win_probability,
                "conviction_score": conviction_score,
                "best_bet_score": best_bet_score,
                "tx_hash": tx_hash,
                "realized_pnl": 0.0
            })
            
            add_log(state, f"COPIED BUY: {name} bought {outcome} on '{title}' [Win Prob: {win_probability:.1f}%, Conviction: {conviction_score}%, Score: {best_bet_score}]. Simulated buy of {quantity:.2f} shares @ {exec_price:.3f} USDC (Total: {invest_usdc:.2f} USDC)")
            trade_executed_or_resolved = True

        elif side == "SELL":
            # Check if we own this asset
            if asset not in state["positions"]:
                # Even if we don't own it, update what we think the whale owns
                if address not in state["whale_positions"]:
                    state["whale_positions"][address] = {}
                state["whale_positions"][address][asset] = max(0.0, state["whale_positions"][address].get(asset, 0.0) - size)
                
                state["processed_tx_hashes"].append(tx_hash)
                continue

            pos = state["positions"][asset]
            
            # Compute proportional sell fraction
            whale_holdings = state["whale_positions"].get(address, {}).get(asset, 0.0)
            if whale_holdings <= 0 or size >= whale_holdings:
                sell_fraction = 1.0
            else:
                sell_fraction = size / whale_holdings
            
            sell_qty = pos["quantity"] * sell_fraction
            if sell_qty <= 0:
                state["processed_tx_hashes"].append(tx_hash)
                continue

            # Fetch live market price if required
            if config.get("execution_mode") == "market_price":
                live_price = fetch_clob_price(asset, "buy") # bid price
                if live_price is not None:
                    exec_price = live_price
                else:
                    exec_price = price
                    add_log(state, f"Could not fetch bid price for {title}. Defaulting to whale price {price:.2f}")
            else:
                exec_price = price

            # Apply slippage
            slippage_bps = float(config.get("slippage_bps", 10))
            exec_price = exec_price * (1.0 - (slippage_bps / 10000.0))
            
            proceeds = sell_qty * exec_price
            cost_basis = pos["avg_price"] * sell_qty
            realized_pnl = proceeds - cost_basis

            # Execute simulated sell
            state["cash_usdc"] += proceeds
            
            # Update positions
            pos["quantity"] -= sell_qty
            pos["invested_usdc"] = max(0.0, pos["invested_usdc"] - cost_basis)
            
            if pos["quantity"] <= 0.0001:
                del state["positions"][asset]
            else:
                pos["last_updated"] = int(time.time())

            # Update whale position holding
            if address in state["whale_positions"] and asset in state["whale_positions"][address]:
                state["whale_positions"][address][asset] = max(0.0, whale_holdings - size)

            # Record trade history
            trade_id = str(uuid.uuid4())
            state["trades"].append({
                "id": trade_id,
                "timestamp": int(time.time()),
                "trader_address": address,
                "trader_name": name,
                "market_title": title,
                "market_slug": slug,
                "outcome": outcome,
                "type": "SELL",
                "quantity": sell_qty,
                "price": exec_price,
                "usdc_size": proceeds,
                "tx_hash": tx_hash,
                "realized_pnl": realized_pnl
            })
            
            add_log(state, f"COPIED SELL: {name} sold {outcome} on '{title}'. Simulated sell of {sell_qty:.2f} shares @ {exec_price:.3f} USDC (Proceeds: {proceeds:.2f} USDC, PnL: {realized_pnl:+.2f} USDC)")
            trade_executed_or_resolved = True

        # Mark processed
        state["processed_tx_hashes"].append(tx_hash)
        if len(state["processed_tx_hashes"]) > 1000:
            state["processed_tx_hashes"] = state["processed_tx_hashes"][-1000:]

    return trade_executed_or_resolved


def sync_whales_from_leaderboard(time_period="WEEK", limit=1000):
    """
    Fetches top traders from the Polymarket leaderboard and updates
    the followed_traders list in config.json.
    """
    print(f"Syncing top {limit} whales from Polymarket {time_period} leaderboard...")
    new_traders = []
    
    # Leaderboard pages of 50
    for offset in range(0, limit, 50):
        url = f"https://data-api.polymarket.com/v1/leaderboard?timePeriod={time_period}&limit=50&offset={offset}"
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                page_data = res.json()
                if isinstance(page_data, list):
                    new_traders.extend(page_data)
                    if len(page_data) < 50:
                        break
                else:
                    break
            else:
                print(f"Failed to fetch leaderboard page at offset {offset}: status {res.status_code}")
                break
        except Exception as e:
            print(f"Error fetching leaderboard page at offset {offset}: {e}")
            break
        time.sleep(0.1)
        
    if not new_traders:
        print("No traders fetched from leaderboard. Sync skipped.")
        return False
        
    # Load current configuration
    from config import save_config
    cfg = load_config()
    
    current_traders = cfg.get("followed_traders", [])
    current_map = {t["address"].lower(): t for t in current_traders}
    
    updated_traders = []
    added_count = 0
    
    for item in new_traders:
        addr = item.get("proxyWallet")
        if not addr:
            continue
        addr_clean = addr.strip().lower()
        rank = item.get("rank")
        name = item.get("userName") or f"Whale Rank {rank}"
        
        # If already followed, keep existing settings but update name if username changed
        if addr_clean in current_map:
            trader_cfg = current_map[addr_clean]
            if item.get("userName") and not trader_cfg["name"].startswith(item.get("userName")):
                trader_cfg["name"] = f"{item.get('userName')} (Rank {rank})"
            updated_traders.append(trader_cfg)
        else:
            # Create a new followed trader
            new_trader = {
                "address": addr_clean,
                "name": f"{name} (Rank {rank})",
                "enabled": True,
                "sizing_type": "fixed",
                "sizing_value": 100.0
            }
            updated_traders.append(new_trader)
            added_count += 1
            
    # Add any traders that were manually added before but not in this top leaderboard
    new_addresses = {t["address"].lower() for t in updated_traders}
    for addr_clean, trader_cfg in current_map.items():
        if addr_clean not in new_addresses:
            updated_traders.append(trader_cfg)
            
    cfg["followed_traders"] = updated_traders
    save_config(cfg)
    print(f"Leaderboard sync completed. Added {added_count} new whales. Total followed whales: {len(updated_traders)}")
    
    # Add a system log entry if state is loaded
    try:
        state = load_state(cfg)
        add_log(state, f"Synced {limit} top whales from {time_period} leaderboard. Added {added_count} new whales. Total: {len(updated_traders)}")
        save_state(state)
    except Exception as e:
        print(f"Failed to write log for leaderboard sync: {e}")
        
    return True


_last_sync_time = 0

def _run_loop():
    global _last_sync_time
    print("Background simulation thread started.")
    while not _stop_event.is_set():
        try:
            config = load_config()
            
            # Run leaderboard sync hourly
            current_time = time.time()
            if current_time - _last_sync_time >= 3600:
                try:
                    sync_whales_from_leaderboard(time_period="WEEK", limit=1000)
                    _last_sync_time = current_time
                    # Reload config
                    config = load_config()
                except Exception as sync_err:
                    print(f"Error in automatic leaderboard sync: {sync_err}")

            if config.get("simulation_active", False):
                with _state_lock:
                    state = load_state(config)
                    
                    # 1. Run simulation iteration to copy new trades
                    trade_occurred = run_simulation_iteration(config, state)
                    
                    # 2. Update current prices and value of open positions
                    holdings_value = update_live_valuations(state)
                    total_equity = calculate_total_equity(state, holdings_value)
                    
                    # 3. Add snapshot to portfolio history (limit frequency to keep history small)
                    last_snapshot = state["portfolio_value_history"][-1] if state["portfolio_value_history"] else None
                    current_time = int(time.time())
                    
                    should_append = False
                    if not last_snapshot:
                        should_append = True
                    elif trade_occurred:
                        should_append = True
                    elif current_time - last_snapshot["timestamp"] >= 300: # Every 5 minutes
                        should_append = True
                        
                    if should_append:
                        # Limit history length to ~2000 points
                        if len(state["portfolio_value_history"]) > 2000:
                            state["portfolio_value_history"] = state["portfolio_value_history"][-2000:]
                        state["portfolio_value_history"].append({
                            "timestamp": current_time,
                            "cash": state["cash_usdc"],
                            "holdings_value": holdings_value,
                            "total_equity": total_equity
                        })
                    
                    save_state(state)
            
            # Sleep in 1-second chunks to respond quickly to shutdown signals
            poll_interval = config.get("poll_interval_seconds", 30)
            for _ in range(poll_interval):
                if _stop_event.is_set():
                    break
                time.sleep(1)
                
        except Exception as e:
            print(f"Exception in simulation background thread: {e}")
            traceback.print_exc()
            time.sleep(10)

def start_engine():
    global _engine_thread, _stop_event
    with _state_lock:
        if _engine_thread and _engine_thread.is_alive():
            return
        _stop_event.clear()
        _engine_thread = threading.Thread(target=_run_loop, daemon=True)
        _engine_thread.start()

def stop_engine():
    global _stop_event
    _stop_event.set()

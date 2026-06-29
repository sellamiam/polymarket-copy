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


def is_niche_market(title, slug, question=""):
    title_lower = (title or "").lower()
    slug_lower = (slug or "").lower()
    q_lower = (question or "").lower()
    
    niche_keywords = [
        "science", "spacex", "nasa", "space", "gpt-", "openai", "anthropic", "google", "apple", "ai", 
        "artificial intelligence", "valuation", "ipo", "merger", "acquisition", "fed", "interest rate", 
        "sec", "election", "president", "regulatory", "court", "ruling", "lawsuit", "vaccine", "audit"
    ]
    return any(kw in title_lower or kw in slug_lower or kw in q_lower for kw in niche_keywords)

def calculate_dynamic_sizing_multiplier(trader_cfg, whale_trade_usdc):
    roi_multiplier = 1.0
    rank_mult = 1.0
    conviction_multiplier = 1.0
    
    # ROI Sizing Factor
    pnl = trader_cfg.get("pnl", 0.0)
    vol = trader_cfg.get("vol", 0.0)
    if vol > 0.0 and pnl > 0.0:
        roi = pnl / vol
        if roi > 0.10: # > 10% weekly ROI
            roi_multiplier = 1.5
        elif roi < 0.03: # < 3% weekly ROI
            roi_multiplier = 0.5
            
    # Rank Sizing Factor
    rank = trader_cfg.get("rank")
    if rank and rank <= 100:
        rank_mult = 1.2
        
    # Conviction Sizing Factor
    if whale_trade_usdc > 25000:
        conviction_multiplier = 2.0
    elif whale_trade_usdc > 10000:
        conviction_multiplier = 1.5
    elif whale_trade_usdc < 500:
        conviction_multiplier = 0.5
        
    total_mult = roi_multiplier * rank_mult * conviction_multiplier
    return min(3.0, max(0.2, total_mult))


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
    # Hardcoded overrides for resolved markets that no longer return from the active Gamma API endpoint
    overrides = {
        # Will SpaceX IPO by June 15, 2026? Yes
        "0x13ca9f55fed9ce4094d90fe1ab5f63c0d371d1589dfffe247a3c1c13d6fb0477": {
            "resolved": True,
            "closed": True,
            "outcomePrices": "[1, 0]"
        },
        # Will SpaceX's market cap be between $1.5T and $2.0T at market close on IPO day? Yes
        "0x235693693a07f40782ff4a91d992a98a627d77a4cde305af3dd675eb982ec283": {
            "resolved": True,
            "closed": True,
            "outcomePrices": "[1, 0]"
        }
    }

    resolved_any = False
    for token_id, pos in list(state["positions"].items()):
        condition_id = pos["condition_id"]
        try:
            market = None
            if condition_id in overrides:
                market = overrides[condition_id]
            else:
                url = f"https://gamma-api.polymarket.com/markets?condition_ids={condition_id}"
                res = requests.get(url, timeout=5)
                if res.status_code == 200:
                    markets = res.json()
                    if isinstance(markets, list) and len(markets) > 0:
                        for m in markets:
                            if m.get("conditionId") == condition_id:
                                market = m
                                break
                        if not market:
                            market = markets[0]
                
                # Fallback to closed markets endpoint if not found in active markets
                if not market:
                    url_closed = f"https://gamma-api.polymarket.com/markets?closed=true&condition_ids={condition_id}"
                    res_closed = requests.get(url_closed, timeout=5)
                    if res_closed.status_code == 200:
                        markets = res_closed.json()
                        if isinstance(markets, list) and len(markets) > 0:
                            for m in markets:
                                if m.get("conditionId") == condition_id:
                                    market = m
                                    break
                            if not market:
                                market = markets[0]
            
            if market:
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
                            "original_trader_address": pos.get("trader_address", ""),
                            "original_trader_name": pos.get("trader_name", ""),
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

def recycle_positions_and_exit_strategies(config, state):
    """
    Checks open positions against active exit rules:
    - Take Profit (TP)
    - Stop Loss (SL)
    - Time-based Early Exit (Capital Recycling)
    """
    take_profit_pct = float(config.get("take_profit_pct", 15.0))
    value_play_take_profit_pct = float(config.get("value_play_take_profit_pct", 40.0))
    stop_loss_pct = float(config.get("stop_loss_pct", 15.0))
    stop_loss_grace_hours = float(config.get("stop_loss_grace_hours", 4.0))
    max_holding_hours = float(config.get("max_holding_hours", 24))
    
    current_time = int(time.time())
    recycled_any = False
    
    for token_id, pos in list(state["positions"].items()):
        avg_price = pos["avg_price"]
        current_price = pos.get("current_price", avg_price)
        
        # Check current price vs average price
        pnl_pct = 0.0
        if avg_price > 0:
            pnl_pct = ((current_price - avg_price) / avg_price) * 100.0
            
        # Determine age
        opened_at = pos.get("opened_at")
        if opened_at is None:
            # Fallback for legacy positions
            opened_at = pos.get("last_updated", current_time)
            pos["opened_at"] = opened_at
            
        age_hours = (current_time - opened_at) / 3600.0
        
        trigger_reason = None
        exit_type = None
        
        # Dynamic Take Profit target based on entry price
        effective_tp_pct = take_profit_pct
        if avg_price <= 0.50:
            effective_tp_pct = value_play_take_profit_pct

        maturity_threshold = float(config.get("maturity_threshold", 0.98))
        if current_price >= maturity_threshold:
            trigger_reason = f"MATURITY ({current_price:.2f})"
            exit_type = "RECYCLE_MATURITY"
        elif effective_tp_pct > 0 and pnl_pct >= effective_tp_pct:
            trigger_reason = f"TP (+{pnl_pct:.1f}%)"
            exit_type = "RECYCLE_TP"
        elif stop_loss_pct > 0 and pnl_pct <= -stop_loss_pct and age_hours >= stop_loss_grace_hours:
            trigger_reason = f"SL ({pnl_pct:+.1f}%)"
            exit_type = "RECYCLE_SL"
        elif max_holding_hours > 0 and age_hours >= max_holding_hours:
            trigger_reason = f"TIME LIMIT ({age_hours:.1f}h)"
            exit_type = "RECYCLE_TIME"
            
        if trigger_reason and exit_type:
            # Fetch live bid price if possible
            exit_price = fetch_clob_price(token_id, "buy")
            if exit_price is None:
                exit_price = current_price
                
            proceeds = pos["quantity"] * exit_price
            cost_basis = pos["avg_price"] * pos["quantity"]
            realized_pnl = proceeds - cost_basis
            
            # Execute exit
            state["cash_usdc"] += proceeds
            
            # Record trade history
            trade_id = str(uuid.uuid4())
            state["trades"].append({
                "id": trade_id,
                "timestamp": current_time,
                "trader_address": "exit_strategy",
                "trader_name": f"Strategy Exit: {trigger_reason}",
                "original_trader_address": pos.get("trader_address", ""),
                "original_trader_name": pos.get("trader_name", ""),
                "market_title": pos["market_title"],
                "market_slug": pos["market_slug"],
                "outcome": pos["outcome"],
                "type": "SELL",
                "quantity": pos["quantity"],
                "price": exit_price,
                "usdc_size": proceeds,
                "tx_hash": exit_type.lower(),
                "realized_pnl": realized_pnl
            })
            
            # Delete position
            del state["positions"][token_id]
            recycled_any = True
            
            msg = f"EXITED: '{pos['market_title']}' ({pos['outcome']}) via {trigger_reason}. Sold {pos['quantity']:.2f} shares @ {exit_price:.3f} USDC (Proceeds: {proceeds:.2f} USDC, PnL: {realized_pnl:+.2f} USDC)."
            add_log(state, msg)
            
    return recycled_any


def get_trader_stats(state, address):
    if not address:
        return 0, 0.0
    addr_clean = address.lower()
    exits = [t for t in state.get("trades", []) if t.get("original_trader_address", "").lower() == addr_clean or t.get("trader_address", "").lower() == addr_clean]
    resolved = [t for t in exits if t.get("realized_pnl") is not None and t.get("type") in ["SELL", "RESOLVE"]]
    if not resolved:
        return 0, 0.0
    wins = sum(1 for t in resolved if t.get("realized_pnl", 0) > 0)
    win_rate = (wins / len(resolved)) * 100.0
    return len(resolved), win_rate

def get_cluster_name(title, slug):
    text = f"{title} {slug}".lower()
    geo_kw = ["iran", "strait of hormuz", "israel", "yemen", "middle east", "war", "syria", "lebanon", "gaza"]
    tech_kw = ["openai", "anthropic", "google", "gemini", "claude", "spacex", "ai", "gpt-", "nasa"]
    macro_kw = ["fed", "interest rate", "inflation", "wti", "crude oil", "natural gas", "cpi", "powell"]
    
    if any(kw in text for kw in geo_kw):
        return "geopolitics"
    elif any(kw in text for kw in tech_kw):
        return "tech_ai"
    elif any(kw in text for kw in macro_kw):
        return "fed_macro"
    return None

def get_cluster_exposure(state, target_cluster):
    if not target_cluster:
        return 0.0
    total = 0.0
    for pos in state.get("positions", {}).values():
        c_name = get_cluster_name(pos.get("market_title", ""), pos.get("market_slug", ""))
        if c_name == target_cluster:
            total += pos.get("invested_usdc", 0.0)
    return total

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
        url = "https://data-api.polymarket.com/v1/trades?limit=1000"
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
        
        is_vitalik = (address == "0x8a98109fb0f1d87d9bfcb4486ba3587b95c51b92")

        # Check Whale Alpha Auto-Pruning
        if config.get("enable_whale_auto_pruning", True) and not is_vitalik:
            min_wr = float(config.get("min_whale_win_rate", 40.0))
            resolved_cnt, wr = get_trader_stats(state, address)
            if resolved_cnt >= 3 and wr < min_wr:
                add_log(state, f"Skipped trade from {name}: Whale win rate ({wr:.1f}%) is below minimum threshold of {min_wr:.1f}%.")
                state["processed_tx_hashes"].append(tx_hash)
                continue

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

        # Fast path check for crypto/sports/weather in title/slug to avoid unnecessary API requests and noise
        title_lower = title.lower()
        slug_lower = slug.lower()
        
        is_crypto_fast = False
        is_sports_fast = False
        is_weather_fast = False
        
        if config.get("exclude_crypto_bets", True):
            crypto_keywords = ["up or down", "price of", "bitcoin", "ethereum", "solana", "cardano", "dogecoin", "ripple", "crypto", "btc", "eth", "sol", "airdrop"]
            if not ("spacex" in title_lower or "spacex" in slug_lower):
                if any(kw in title_lower or kw in slug_lower for kw in crypto_keywords):
                    is_crypto_fast = True
                    
        if config.get("exclude_sports_bets", True):
            sports_keywords = [
                " vs ", "vs.", "versus", "wnba", "nba", "nfl", "mlb", "nhl", "ufc", "mma", "pga", "mls",
                "premier league", "champions league", "la liga", "bundesliga", "serie a", "atp", "wta",
                "soccer", "football", "basketball", "baseball", "hockey", "tennis", "golf", "cricket", "rugby",
                "boxing", "wrestling", "nascar", "formula 1", "f1", "grand prix", "athletics", "olympics"
            ]
            if any(kw in title_lower or kw in slug_lower for kw in sports_keywords):
                is_sports_fast = True

        if config.get("exclude_weather_bets", True):
            weather_keywords = ["weather", "temperature", "celsius", "degree", "fahrenheit", "rain", "snow", "hottest", "coldest", "meteorological", "wind speed", "precipitation", "°c", "°f"]
            if any(kw in title_lower or kw in slug_lower for kw in weather_keywords):
                is_weather_fast = True

        if is_crypto_fast and not is_vitalik:
            add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name}: Crypto bets are excluded.")
            state["processed_tx_hashes"].append(tx_hash)
            continue
            
        if is_sports_fast and not is_vitalik:
            add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name}: Sports bets are excluded.")
            state["processed_tx_hashes"].append(tx_hash)
            continue

        if is_weather_fast and not is_vitalik:
            add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name}: Weather/temperature bets are excluded.")
            state["processed_tx_hashes"].append(tx_hash)
            continue

        # Check minimum whale trade size to filter out high-frequency bot spam
        min_whale_size = float(config.get("min_whale_trade_size", 500.0))
        if usdc_size < min_whale_size and not is_vitalik:
            print(f"[ENGINE] Skipped micro-trade on '{title}' ({outcome}) from {name}: {usdc_size:.2f} USDC is below minimum {min_whale_size:.2f} USDC.")
            if usdc_size >= 1.0:
                add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name}: Trade size ({usdc_size:.1f} USDC) is below minimum of {min_whale_size:.1f} USDC.")
            state["processed_tx_hashes"].append(tx_hash)
            continue

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

        # Check for top whale alpha multiplier (win rate >= 70%)
        resolved_cnt, wr = get_trader_stats(state, address)
        if resolved_cnt >= 3 and wr >= 70.0:
            copy_usdc *= 1.5
            add_log(state, f"ALPHA BONUS: Trade size on '{title}' scaled up 1.5x to {copy_usdc:.2f} USDC (Whale win rate: {wr:.1f}%).")

        # 4. Handle execution
        if side == "BUY":
            win_probability = price * 100
            conviction_score = calculate_conviction(usdc_size)
            best_bet_score = int(win_probability * 0.6 + conviction_score * 0.4)

            # Determine if this is a niche market for priority treatment
            niche_flag = is_niche_market(title, slug)
            niche_active = config.get("niche_priority_active", False)
            niche_bypass = niche_flag and niche_active

            # Check price range filters (bypassed for niche markets when priority is active or for value plays)
            min_price = float(config.get("min_copy_price", 0.70))
            max_price = float(config.get("max_copy_price", 0.95))
            is_value_play = False
            
            if (price < min_price or price > max_price) and not is_vitalik:
                if config.get("enable_value_plays", True) and 0.10 <= price <= 0.60:
                    is_value_play = True
                    add_log(state, f"VALUE PLAY: '{title}' ({outcome}) @ {price:.3f} USDC qualifies as a value play (potential 1.6x-10x payout).")
                elif niche_bypass:
                    if price > 0.90:
                        add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name} @ {price:.3f} USDC: Niche market but price exceeds hard ceiling 0.90 USDC (low yield yield-farm bet).")
                        state["processed_tx_hashes"].append(tx_hash)
                        continue
                    add_log(state, f"NICHE BYPASS: '{title}' ({outcome}) from {name} @ {price:.3f} USDC bypassed price filter [{min_price:.2f}, {max_price:.2f}] — niche market priority active.")
                else:
                    add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name} @ {price:.3f} USDC: Outside price range [{min_price:.2f}, {max_price:.2f}] [Win Prob: {win_probability:.1f}%]")
                    state["processed_tx_hashes"].append(tx_hash)
                    continue

            # Check resolution time and category filters
            max_days = int(config.get("max_days_to_resolution", 7))
            if condition_id:
                market_details = fetch_market_details(condition_id)
                if market_details:
                    # Check liquidity and volume filters
                    try:
                        liquidity = float(market_details.get("liquidityNum") or market_details.get("liquidity") or 0)
                        volume = float(market_details.get("volumeNum") or market_details.get("volume") or 0)
                        min_liq = float(config.get("min_market_liquidity", 5000.0))
                        min_vol = float(config.get("min_market_volume", 20000.0))
                        
                        if liquidity < min_liq and not is_vitalik:
                            add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name}: Market liquidity ({liquidity:,.1f} USDC) is below minimum of {min_liq:,.1f} USDC.")
                            state["processed_tx_hashes"].append(tx_hash)
                            continue
                            
                        if volume < min_vol and not is_vitalik:
                            add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name}: Market volume ({volume:,.1f} USDC) is below minimum of {min_vol:,.1f} USDC.")
                            state["processed_tx_hashes"].append(tx_hash)
                            continue
                    except Exception as le:
                        print(f"Error validating market liquidity/volume: {le}")
                    # Exclude crypto bets filter
                    if config.get("exclude_crypto_bets", True):
                        is_crypto = False
                        # 1. Check feeType
                        fee_type = (market_details.get("feeType") or "").lower()
                        if "crypto" in fee_type:
                            is_crypto = True

                        # 2. Check keywords
                        question_lower = (market_details.get("question") or "").lower()
                        slug_lower = (market_details.get("slug") or "").lower()
                        crypto_keywords = ["up or down", "price of", "bitcoin", "ethereum", "solana", "cardano", "dogecoin", "ripple", "crypto", "btc", "eth", "sol", "airdrop"]
                        if any(kw in question_lower or kw in slug_lower for kw in crypto_keywords):
                            is_crypto = True

                        # Safeguard: Never exclude SpaceX IPO bets
                        if "spacex" in question_lower or "spacex" in slug_lower:
                            is_crypto = False

                        if is_crypto and not is_vitalik:
                            add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name}: Crypto bets are excluded.")
                            state["processed_tx_hashes"].append(tx_hash)
                            continue

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
                        fee_type = (market_details.get("feeType") or "").lower()
                        if "sports" in fee_type:
                            is_sports = True

                        # 3. Fallback text keyword matching
                        question_lower = (market_details.get("question") or "").lower()
                        slug_lower = (market_details.get("slug") or "").lower()
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

                        if is_sports and not is_vitalik:
                            add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name}: Sports bets are excluded.")
                            state["processed_tx_hashes"].append(tx_hash)
                            continue

                    # Exclude weather bets filter
                    if config.get("exclude_weather_bets", True):
                        is_weather = False
                        question_lower = (market_details.get("question") or "").lower()
                        slug_lower = (market_details.get("slug") or "").lower()
                        title_lower = (title or "").lower()
                        weather_keywords = ["weather", "temperature", "celsius", "degree", "fahrenheit", "rain", "snow", "hottest", "coldest", "meteorological", "wind speed", "precipitation"]
                        if any(kw in question_lower or kw in slug_lower or kw in title_lower for kw in weather_keywords):
                            is_weather = True
                            
                        if is_weather and not is_vitalik:
                            add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name}: Weather/temperature bets are excluded.")
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
                            if days_left > max_days and not is_vitalik:
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
                if best_bet_score < min_best_score and not is_vitalik:
                    add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name}: Score {best_bet_score} is below minimum Best Bet Score {min_best_score} [Win Prob: {win_probability:.1f}%, Conviction: {conviction_score}%]")
                    state["processed_tx_hashes"].append(tx_hash)
                    continue

            if state["cash_usdc"] < 1.0:
                add_log(state, f"Skipped BUY on '{title}' from {name}: Insufficient cash (Balance: {state['cash_usdc']:.2f} USDC)")
                state["processed_tx_hashes"].append(tx_hash)
                continue

            # Apply dynamic sizing multiplier if enabled
            if config.get("dynamic_sizing_active", False):
                dyn_mult = calculate_dynamic_sizing_multiplier(trader, usdc_size)
                copy_usdc *= dyn_mult
                if abs(dyn_mult - 1.0) > 0.01:
                    add_log(state, f"DYNAMIC SIZING: '{title}' size adjusted by {dyn_mult:.2f}x → {copy_usdc:.2f} USDC (whale ROI/rank/conviction scaling).")

            # Value Play Sizing Discount: scale down by 0.25x to manage risk
            if is_value_play:
                copy_usdc *= 0.25
                add_log(state, f"VALUE PLAY SIZING: '{title}' size scaled down by 0.25x → {copy_usdc:.2f} USDC.")

            # Niche market bonus: +25% allocation for niche markets when priority active
            if niche_bypass:
                niche_bonus = copy_usdc * 0.25
                copy_usdc += niche_bonus

            # Check opposing outcome index protection and exposure cap
            max_exposure = float(config.get("max_market_exposure", 500.0))
            existing_pos = None
            
            # Find if we already hold a position for this market (same condition_id)
            for tid, p in state["positions"].items():
                if p.get("condition_id") == condition_id:
                    existing_pos = p
                    break
                    
            if existing_pos:
                # 1. Opposing outcome index check
                if existing_pos.get("outcome_index") != outcome_index:
                    print(f"[ENGINE] Skipped opposing outcome on '{title}': already hold {existing_pos['outcome']} (index {existing_pos['outcome_index']}), skipped {outcome} (index {outcome_index})")
                    state["processed_tx_hashes"].append(tx_hash)
                    continue
                    
                # 2. Exposure cap check
                invested = existing_pos.get("invested_usdc", 0.0)
                if invested >= max_exposure:
                    print(f"[ENGINE] Skipped copy on '{title}': already reached exposure cap of {max_exposure} USDC (current: {invested:.2f} USDC)")
                    state["processed_tx_hashes"].append(tx_hash)
                    continue
                else:
                    # Scale down trade size if it exceeds the remaining exposure space
                    remaining = max_exposure - invested
                    if copy_usdc > remaining:
                        copy_usdc = remaining
                        print(f"[ENGINE] Scaled down trade size on '{title}' to {copy_usdc:.2f} USDC to respect exposure cap of {max_exposure} USDC")
            else:
                # New position: ensure initial size doesn't exceed the cap
                if copy_usdc > max_exposure:
                    copy_usdc = max_exposure
                    print(f"[ENGINE] Scaled down initial trade size on '{title}' to {copy_usdc:.2f} USDC to respect exposure cap of {max_exposure} USDC")

            # Correlated Topic Cluster exposure check
            cluster_name = get_cluster_name(title, slug)
            if cluster_name:
                max_cluster_exp = float(config.get("max_cluster_exposure", 600.0))
                current_cluster_exp = get_cluster_exposure(state, cluster_name)
                if current_cluster_exp >= max_cluster_exp and not is_vitalik:
                    add_log(state, f"Skipped BUY on '{title}' ({outcome}) from {name}: Reached correlated '{cluster_name}' topic cluster cap of {max_cluster_exp:.0f} USDC (current: {current_cluster_exp:.1f} USDC).")
                    state["processed_tx_hashes"].append(tx_hash)
                    continue
                elif current_cluster_exp + copy_usdc > max_cluster_exp:
                    copy_usdc = max_cluster_exp - current_cluster_exp
                    add_log(state, f"Scaled down trade on '{title}' to {copy_usdc:.2f} USDC to respect '{cluster_name}' topic cluster cap of {max_cluster_exp:.0f} USDC.")

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
                    "trader_address": address,
                    "trader_name": name,
                    "last_updated": int(time.time()),
                    "opened_at": int(time.time())
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
            
            tag = " [NICHE]" if niche_bypass else (" [VALUE PLAY]" if is_value_play else "")
            add_log(state, f"COPIED BUY: {name} bought {outcome} on '{title}'{tag} [Win Prob: {win_probability:.1f}%, Conviction: {conviction_score}%, Score: {best_bet_score}]. Simulated buy of {quantity:.2f} shares @ {exec_price:.3f} USDC (Total: {invest_usdc:.2f} USDC)")
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
    Fetches top traders from specified timeframe(s) on the Polymarket leaderboard,
    deduplicates them, and updates followed_traders list in config.json.
    """
    if isinstance(time_period, str):
        time_periods = [time_period]
    else:
        time_periods = time_period

    print(f"Syncing top {limit} whales from Polymarket leaderboards: {time_periods}...")
    new_traders = {}
    
    for tp in time_periods:
        print(f"Fetching {tp} leaderboard...")
        # Leaderboard pages of 50
        for offset in range(0, limit, 50):
            url = f"https://data-api.polymarket.com/v1/leaderboard?timePeriod={tp}&limit=50&offset={offset}"
            try:
                res = requests.get(url, timeout=10)
                if res.status_code == 200:
                    page_data = res.json()
                    if isinstance(page_data, list):
                        for item in page_data:
                            addr = item.get("proxyWallet")
                            if not addr:
                                continue
                            addr_clean = addr.strip().lower()
                            
                            pnl = float(item.get("pnl", 0.0))
                            vol = float(item.get("vol", 0.0))
                            rank = item.get("rank")
                            rank_val = int(rank) if rank else 1000
                            name = item.get("userName") or f"Whale Rank {rank}"
                            
                            # Deduplicate and keep the entry with highest rank or pnl
                            if addr_clean not in new_traders or pnl > new_traders[addr_clean]["pnl"]:
                                new_traders[addr_clean] = {
                                    "address": addr_clean,
                                    "name": f"{name} ({tp} Rank {rank})",
                                    "pnl": pnl,
                                    "vol": vol,
                                    "rank": rank_val
                                }
                        if len(page_data) < 50:
                            break
                    else:
                        break
                else:
                    print(f"Failed to fetch {tp} leaderboard page at offset {offset}: status {res.status_code}")
                    break
            except Exception as e:
                print(f"Error fetching {tp} leaderboard page at offset {offset}: {e}")
                break
            time.sleep(0.1)
            
    if not new_traders:
        print("No traders fetched from any leaderboard. Sync skipped.")
        return False
        
    # Load current configuration
    from config import save_config
    cfg = load_config()
    
    min_roi = float(cfg.get("min_whale_roi", 0.01))
    max_vol = float(cfg.get("max_whale_volume", 20000000.0))
    
    current_traders = cfg.get("followed_traders", [])
    current_map = {t["address"].lower(): t for t in current_traders}
    
    updated_traders = []
    added_count = 0
    
    for addr_clean, item in new_traders.items():
        pnl = item["pnl"]
        vol = item["vol"]
        roi = (pnl / vol) if vol > 0 else 0
        if roi < min_roi or vol > max_vol:
            continue
            
        if addr_clean in current_map:
            trader_cfg = current_map[addr_clean]
            trader_cfg["pnl"] = item["pnl"]
            trader_cfg["vol"] = item["vol"]
            trader_cfg["rank"] = item["rank"]
            updated_traders.append(trader_cfg)
        else:
            new_trader = {
                "address": addr_clean,
                "name": item["name"],
                "enabled": True,
                "sizing_type": "fixed",
                "sizing_value": 100.0,
                "pnl": item["pnl"],
                "vol": item["vol"],
                "rank": item["rank"]
            }
            updated_traders.append(new_trader)
            added_count += 1
            
    # Add any traders that were manually added before but not in these leaderboards
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
        add_log(state, f"Synced {limit} top whales from {time_periods} leaderboards. Added {added_count} new whales. Total: {len(updated_traders)}")
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
                    sync_whales_from_leaderboard(time_period=["WEEK", "MONTH", "ALL"], limit=1000)
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
                    
                    # 3. Check for early exits/capital recycling based on latest prices
                    recycle_occurred = recycle_positions_and_exit_strategies(config, state)
                    if recycle_occurred:
                        # Recalculate valuations if positions were sold
                        holdings_value = update_live_valuations(state)
                        
                    total_equity = calculate_total_equity(state, holdings_value)
                    
                    # 4. Add snapshot to portfolio history (limit frequency to keep history small)
                    last_snapshot = state["portfolio_value_history"][-1] if state["portfolio_value_history"] else None
                    current_time = int(time.time())
                    
                    should_append = False
                    if not last_snapshot:
                        should_append = True
                    elif trade_occurred or recycle_occurred:
                        should_append = True
                    elif current_time - last_snapshot["timestamp"] >= 300: # Every 5 minutes
                        should_append = True
                        
                    if should_append:
                        # Limit history length to ~500 points
                        if len(state["portfolio_value_history"]) > 500:
                            state["portfolio_value_history"] = state["portfolio_value_history"][-500:]
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

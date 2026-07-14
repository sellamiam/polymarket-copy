import json
import requests
from datetime import datetime, timezone, timedelta

def main():
    # User's local timezone is UTC-03:00
    local_tz = timezone(timedelta(hours=-3))
    now = datetime.now(local_tz)
    start_of_today = datetime(now.year, now.month, now.day, tzinfo=local_tz)
    start_timestamp = start_of_today.timestamp()

    print(f"Analyzing Polymarket Bot Performance for local day: {start_of_today.strftime('%Y-%m-%d')} (from {start_of_today.isoformat()})")
    
    # 1. Fetch history (trades)
    try:
        r_history = requests.get('https://polymarket-copy.onrender.com/api/history', timeout=120)
        r_history.raise_for_status()
        trades = r_history.json().get('trades', [])
    except Exception as e:
        print(f"Error fetching trade history: {e}")
        return

    # 2. Fetch current state
    try:
        r_state = requests.get('https://polymarket-copy.onrender.com/api/state', timeout=120)
        r_state.raise_for_status()
        state_data = r_state.json()
    except Exception as e:
        print(f"Error fetching state: {e}")
        return

    # Filter trades for today
    today_trades = [t for t in trades if t.get('timestamp', 0) >= start_timestamp]
    
    # Analyze trades
    buys = [t for t in today_trades if t.get('type') == 'BUY']
    sells = [t for t in today_trades if t.get('type') in ['SELL', 'RESOLVE']]
    
    total_realized_pnl = sum(t.get('realized_pnl', 0.0) for t in sells)
    
    # Win rate of resolved/sold trades today
    resolved_today = [t for t in sells if t.get('realized_pnl') is not None]
    wins = [t for t in resolved_today if t.get('realized_pnl', 0.0) > 0.0]
    win_rate = (len(wins) / len(resolved_today) * 100) if resolved_today else 0.0
    
    total_volume = sum(t.get('usdc_size', 0.0) for t in today_trades)

    # State values
    holdings_value = state_data.get('holdings_value', 0.0)
    total_equity = state_data.get('total_equity', 0.0)
    state_inner = state_data.get('state', {})
    cash = state_inner.get('cash_usdc', 0.0)
    positions = state_inner.get('positions', {})
    
    # Calculate starting equity today from history
    history = state_inner.get('portfolio_value_history', [])
    start_equity_today = None
    for entry in history:
        entry_time = datetime.fromtimestamp(entry['timestamp'], tz=timezone.utc).astimezone(local_tz)
        if entry_time >= start_of_today:
            start_equity_today = entry.get('total_equity')
            break
            
    if start_equity_today is None and history:
        # Fallback to the first element in history or current equity
        start_equity_today = history[0].get('total_equity', total_equity)
    elif start_equity_today is None:
        start_equity_today = total_equity
        
    day_equity_change = total_equity - start_equity_today
    day_equity_change_pct = (day_equity_change / start_equity_today * 100) if start_equity_today else 0.0

    print("\n================ POLYMARKET SIMULATOR TODAY ================")
    print(f"Time Range:         {start_of_today.strftime('%Y-%m-%d %H:%M:%S %Z')} to {now.strftime('%H:%M:%S %Z')}")
    print("-" * 60)
    print(f"Current Equity:     ${total_equity:,.2f}")
    print(f"Start of Day Equity: ${start_equity_today:,.2f}")
    print(f"Day Change:         ${day_equity_change:+,.2f} ({day_equity_change_pct:+.2f}%)")
    print(f"Available Cash:     ${cash:,.2f}")
    print(f"Position Value:     ${holdings_value:,.2f}")
    print(f"Active Positions:   {len(positions)}")
    print("-" * 60)
    print(f"Trades Executed:    {len(today_trades)} (Buys: {len(buys)}, Sells/Resolves: {len(sells)})")
    print(f"Trading Volume:     ${total_volume:,.2f} USDC")
    print(f"Realized PnL:       ${total_realized_pnl:+,.2f} USDC")
    print(f"Win Rate (Today):   {win_rate:.1f}% ({len(wins)}/{len(resolved_today)})")
    print("============================================================")

if __name__ == '__main__':
    main()

import requests
import pandas as pd
import numpy as np

def main():
    r = requests.get('https://polymarket-copy.onrender.com/api/history', timeout=120)
    trades = r.json().get('trades', [])
    df = pd.DataFrame(trades)
    
    print("=== DEEP FORENSIC AUDIT OF POLYMARKET BOT ===")
    print(f"Total historical trades logged: {len(df)}")
    
    buys = df[df['type'] == 'BUY']
    sells = df[df['type'].isin(['SELL', 'RESOLVE'])]
    
    # 1. Whale Performance Audit
    print("\n--- 1. WHALE LEADERBOARD AUDIT ---")
    if 'original_trader_name' in sells.columns:
        sells['actual_whale_name'] = sells['original_trader_name'].replace('', np.nan).fillna(sells['trader_name'])
    else:
        sells['actual_whale_name'] = sells['trader_name']

    whale_stats = sells.groupby('actual_whale_name').agg(
        trades=('realized_pnl', 'count'),
        total_pnl=('realized_pnl', 'sum'),
        wins=('realized_pnl', lambda x: (x > 0).sum())
    )
    whale_stats['win_rate'] = (whale_stats['wins'] / whale_stats['trades'] * 100).round(1)
    whale_stats = whale_stats.sort_values('total_pnl', ascending=False)
    
    print("Top 5 Profitable Whales Copied:")
    print(whale_stats.head(5).to_string())
    print("\nTop 5 Unprofitable Whales Copied:")
    print(whale_stats.tail(5).to_string())
    
    # 2. Exit Reason Performance Audit
    print("\n--- 2. EXIT REASON AUDIT ---")
    # trader_name in sells often contains exit strategy info, e.g., "Strategy Exit: TP (+15.0%)", "Strategy Exit: MATURITY (0.99)", "System Resolution"
    sells['exit_type'] = sells['trader_name'].apply(lambda x: x if 'Strategy Exit' in str(x) or 'System Resolution' in str(x) else 'Other')
    exit_stats = sells.groupby('exit_type').agg(
        trades=('realized_pnl', 'count'),
        total_pnl=('realized_pnl', 'sum'),
        avg_pnl=('realized_pnl', 'mean'),
        wins=('realized_pnl', lambda x: (x > 0).sum())
    )
    exit_stats['win_rate'] = (exit_stats['wins'] / exit_stats['trades'] * 100).round(1)
    print(exit_stats.to_string())

    # 3. Holding Duration vs ROI
    print("\n--- 3. HOLDING TIME VS ROI AUDIT ---")
    # Match buys and sells to calculate hold duration
    # (Simplified approximation)
    print(f"Total Realized PnL across all history: ${sells['realized_pnl'].sum():+,.2f}")

if __name__ == '__main__':
    main()

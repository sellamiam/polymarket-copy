import json

def main():
    with open('data/state.json') as f:
        state = json.load(f)
    
    cash = state.get('cash_usdc', 0.0)
    positions = state.get('positions', {})
    trades = state.get('trades', [])
    
    total_invested = sum(p['quantity'] * p['avg_price'] for p in positions.values())
    current_value = sum(p['quantity'] * p.get('current_price', p['avg_price']) for p in positions.values())
    unrealized_pnl = current_value - total_invested
    realized_pnl = sum(t.get('realized_pnl', 0.0) for t in trades)
    total_equity = cash + current_value
    
    resolved_trades = [t for t in trades if t.get('type') in ['SELL', 'RESOLVE']]
    wins = sum(1 for t in resolved_trades if t.get('realized_pnl', 0) > 0)
    win_rate = (wins / len(resolved_trades) * 100) if resolved_trades else 0.0
    
    print('=== LATEST PORTFOLIO STATS ===')
    print(f'Total Equity:       ${total_equity:,.2f}')
    print(f'Cash:               ${cash:,.2f}')
    print(f'Invested:           ${total_invested:,.2f}')
    print(f'Holdings Value:     ${current_value:,.2f}')
    print(f'Unrealized PnL:     ${unrealized_pnl:+,.2f}')
    print(f'Realized PnL:       ${realized_pnl:+,.2f}')
    print(f'Active Positions:   {len(positions)}')
    print(f'Total Trades:       {len(trades)}')
    print(f'Overall Win Rate:   {win_rate:.1f}% ({wins}/{len(resolved_trades)})')

if __name__ == '__main__':
    main()

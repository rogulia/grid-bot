#!/usr/bin/env python3
"""Check detailed positions and margin capacity for all accounts"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
from dotenv import load_dotenv
import yaml
from src.exchange.bybit_client import BybitClient


def load_config():
    """Load config from yaml"""
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def analyze_account(account_id, account_name, api_key, api_secret, demo, strategies):
    """Analyze account positions and margin capacity"""
    print(f"\n{'='*100}")
    print(f"🏦 Account {account_id:03d}: {account_name}")
    print(f"{'='*100}")
    print(f"🔧 Environment: {'DEMO' if demo else 'PRODUCTION'}")
    print(f"🔑 API Key: {api_key[:8]}...{api_key[-4:]}")

    try:
        client = BybitClient(api_key=api_key, api_secret=api_secret, demo=demo)

        # Get wallet balance
        balance_data = client.get_wallet_balance()
        if not balance_data or 'list' not in balance_data:
            print("❌ Failed to retrieve balance")
            return

        account = balance_data['list'][0]
        total_equity = float(account.get('totalEquity', 0))
        available_balance = float(account.get('totalAvailableBalance', 0))
        total_initial_margin = float(account.get('totalInitialMargin', 0))
        total_maint_margin = float(account.get('totalMaintenanceMargin', 0))
        account_mm_rate = float(account.get('accountMMRate', 0)) * 100

        print(f"\n💰 BALANCE OVERVIEW:")
        print(f"  Total Equity:        {total_equity:.2f} USDT")
        print(f"  Available Balance:   {available_balance:.2f} USDT")
        print(f"  Initial Margin (IM): {total_initial_margin:.2f} USDT")
        print(f"  Maint. Margin (MM):  {total_maint_margin:.2f} USDT")
        print(f"  Account MM Rate:     {account_mm_rate:.2f}%")

        # Check positions for each symbol in strategies
        print(f"\n📊 POSITIONS BY SYMBOL:")

        for strategy in strategies:
            symbol = strategy.get('symbol')
            leverage = strategy.get('leverage', 75)
            initial_size = strategy.get('initial_position_size_usd', 1.0)
            multiplier = strategy.get('averaging_multiplier', 2.0)
            max_levels = strategy.get('max_grid_levels_per_side', 10)

            print(f"\n  {'─'*94}")
            print(f"  📈 Symbol: {symbol}")
            print(f"  ⚙️  Config: Leverage={leverage}x, Initial Size=${initial_size}, Multiplier={multiplier}x, Max Levels={max_levels}")

            # Get positions (returns list directly, not dict)
            positions = client.get_positions(symbol=symbol, category="linear")

            if not positions:
                print(f"  ⚠️  No position data available for {symbol}")
                continue

            long_pos = None
            short_pos = None

            for pos in positions:
                side = pos.get('side')
                if side == 'Buy':
                    long_pos = pos
                elif side == 'Sell':
                    short_pos = pos

            # Analyze LONG position
            if long_pos and float(long_pos.get('size', 0)) > 0:
                size = float(long_pos.get('size', 0))
                entry_price = float(long_pos.get('avgPrice', 0))
                mark_price = float(long_pos.get('markPrice', 0))
                unrealized_pnl = float(long_pos.get('unrealisedPnl', 0))
                position_im = float(long_pos.get('positionIM', 0))
                position_mm = float(long_pos.get('positionMM', 0))
                liq_price_str = long_pos.get('liqPrice', '0')
                liq_price = float(liq_price_str) if liq_price_str and liq_price_str != '' else 0.0

                print(f"  🟢 LONG Position:")
                print(f"     Size: {size:.4f} | Entry: ${entry_price:.6f} | Mark: ${mark_price:.6f}")
                print(f"     PnL: ${unrealized_pnl:.2f} | IM: ${position_im:.2f} | MM: ${position_mm:.2f}")
                print(f"     Liquidation: ${liq_price:.6f} | Distance: {((mark_price - liq_price) / mark_price * 100):.2f}%")
            else:
                print(f"  🟢 LONG Position: None (can average LONG ✅)")

            # Analyze SHORT position
            if short_pos and float(short_pos.get('size', 0)) > 0:
                size = float(short_pos.get('size', 0))
                entry_price = float(short_pos.get('avgPrice', 0))
                mark_price = float(short_pos.get('markPrice', 0))
                unrealized_pnl = float(short_pos.get('unrealisedPnl', 0))
                position_im = float(short_pos.get('positionIM', 0))
                position_mm = float(short_pos.get('positionMM', 0))
                liq_price_str = short_pos.get('liqPrice', '0')
                liq_price = float(liq_price_str) if liq_price_str and liq_price_str != '' else 0.0

                print(f"  🔴 SHORT Position:")
                print(f"     Size: {size:.4f} | Entry: ${entry_price:.6f} | Mark: ${mark_price:.6f}")
                print(f"     PnL: ${unrealized_pnl:.2f} | IM: ${position_im:.2f} | MM: ${position_mm:.2f}")
                print(f"     Liquidation: ${liq_price:.6f} | Distance: {((liq_price - mark_price) / mark_price * 100):.2f}%")
            else:
                print(f"  🔴 SHORT Position: None (can average SHORT ✅)")

            # Calculate maximum averaging capacity
            print(f"\n  💪 AVERAGING CAPACITY ANALYSIS:")

            # Calculate margin needed for full grid (starting from initial size)
            total_margin_needed = 0
            position_size = initial_size
            for level in range(max_levels):
                total_margin_needed += position_size
                position_size *= multiplier

            print(f"     Max margin needed for {max_levels} levels (one side): ${total_margin_needed:.2f}")
            print(f"     Max margin needed for both sides: ${total_margin_needed * 2:.2f}")

            # Current position analysis
            long_im = float(long_pos.get('positionIM', 0)) if long_pos and float(long_pos.get('size', 0)) > 0 else 0
            short_im = float(short_pos.get('positionIM', 0)) if short_pos and float(short_pos.get('size', 0)) > 0 else 0
            current_positions_im = long_im + short_im

            print(f"     Current positions IM: ${current_positions_im:.2f}")
            print(f"     Remaining capacity: ${available_balance:.2f} available")

            # Estimate how many more levels can be averaged
            # Start from current IM, calculate next position size
            if current_positions_im > 0:
                # Rough estimate: current IM / initial_size gives approximate current level
                # This is simplified - actual tracking would need position manager state
                print(f"     ⚠️  Positions open - actual averaging capacity depends on grid level state")

            # Safety check
            if available_balance < initial_size * 2:
                print(f"     ⚠️  LOW MARGIN: Cannot average both sides simultaneously!")
            elif available_balance < total_margin_needed * 0.3:
                print(f"     ⚠️  LIMITED CAPACITY: Can average ~{int((available_balance / initial_size) / multiplier)} more levels")
            elif available_balance > total_margin_needed:
                print(f"     ✅ EXCELLENT: Can handle full grid on one side + partial on other")
            else:
                capacity_pct = (available_balance / total_margin_needed) * 100
                print(f"     ⚠️  MODERATE: Can handle ~{capacity_pct:.0f}% of max grid")

        print(f"\n{'─'*100}")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


def main():
    print("=" * 100)
    print("🔍 MULTI-ACCOUNT POSITION & MARGIN ANALYSIS")
    print("=" * 100)

    load_dotenv()
    config = load_config()

    accounts = config.get('accounts', [])

    if not accounts:
        print("❌ No accounts found in config.yaml")
        return 1

    for account_config in accounts:
        account_id = account_config.get('id')
        account_name = account_config.get('name', f'Account {account_id}')
        api_key_env = account_config.get('api_key_env')
        api_secret_env = account_config.get('api_secret_env')
        demo = account_config.get('demo_trading', True)
        strategies = account_config.get('strategies', [])

        # Get credentials
        api_key = os.getenv(api_key_env)
        api_secret = os.getenv(api_secret_env)

        if not api_key or not api_secret:
            print(f"\n❌ Account {account_id:03d}: API credentials not found")
            continue

        analyze_account(account_id, account_name, api_key, api_secret, demo, strategies)

    print("\n" + "=" * 100)
    print("✅ Analysis complete!")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())

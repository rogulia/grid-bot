#!/usr/bin/env python3
"""Pre-launch checklist for demo trading"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config_loader import ConfigLoader
from src.exchange.bybit_client import BybitClient


def print_section(title):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def main():
    print("\n" + "🔍 " * 20)
    print_section("PRE-LAUNCH CHECKLIST")

    try:
        # Load config
        config = ConfigLoader()

        # Get credentials
        api_key, api_secret = config.get_api_credentials()

        # Strategy config
        strategy_config = config.get_strategy_config()
        risk_config = config.get_risk_config()
        bot_config = config.get_bot_config()
        demo = config.is_demo()

        # Check 1: Environment
        print_section("1️⃣  ENVIRONMENT")
        print(f"  Demo Trading:  {'✅ YES' if demo else '❌ NO (PRODUCTION!)'}")
        print(f"  Dry Run:       {'❌ NO (REAL ORDERS)' if not bot_config.get('dry_run') else '✅ YES (SIMULATION)'}")
        print(f"  API Key:       {api_key[:8]}...{api_key[-4:]}")

        # Check 2: Balance
        print_section("2️⃣  ACCOUNT BALANCE")
        client = BybitClient(api_key=api_key, api_secret=api_secret, demo=demo)
        balance_data = client.get_wallet_balance()

        if balance_data:
            accounts = balance_data.get('list', [])
            if accounts:
                equity = float(accounts[0].get('totalEquity', 0))
                available = float(accounts[0].get('totalAvailableBalance', 0))
                print(f"  Total Equity:  ${equity:.2f} USDT")
                print(f"  Available:     ${available:.2f} USDT")

                if equity < 100:
                    print(f"  ⚠️  WARNING: Low balance (< $100)")
                elif equity < 500:
                    print(f"  ⚡ NOTICE: Medium balance")
                else:
                    print(f"  ✅ Good balance for testing")
            else:
                print("  ❌ Could not retrieve balance")

        # Check 3: Strategy Parameters
        print_section("3️⃣  STRATEGY PARAMETERS")
        symbol = strategy_config.get('symbol')
        leverage = strategy_config.get('leverage')
        initial_size = strategy_config.get('initial_position_size')
        grid_step = strategy_config.get('grid_step_percent')
        multiplier = strategy_config.get('averaging_multiplier')
        max_levels = strategy_config.get('max_grid_levels_per_side', 10)

        print(f"  Symbol:              {symbol}")
        print(f"  Leverage:            {leverage}x", end="")
        if leverage >= 100:
            print(" ⚠️  VERY HIGH RISK!")
        elif leverage >= 50:
            print(" ⚠️  HIGH RISK")
        elif leverage >= 20:
            print(" ⚡ MEDIUM RISK")
        else:
            print(" ✅ Conservative")

        print(f"  Initial Position:    ${initial_size:.2f}")
        print(f"  Grid Step:           {grid_step}%")
        print(f"  Multiplier:          {multiplier}x")
        print(f"  Max Grid Levels:     {max_levels}")

        # Calculate max risk
        max_risk_per_side = initial_size
        for i in range(max_levels):
            if i > 0:
                max_risk_per_side += max_risk_per_side * (multiplier - 1)
        max_total_risk = max_risk_per_side * 2 * leverage

        print(f"\n  📊 Risk Calculation:")
        print(f"     Max position (one side): ${max_risk_per_side:.2f}")
        print(f"     Max total risk (L+S):    ${max_total_risk:.2f}")

        if equity > 0 and max_total_risk > equity * 0.5:
            print(f"     ⚠️  WARNING: Max risk is {max_total_risk/equity*100:.1f}% of balance!")

        # Check 4: Risk Management
        print_section("4️⃣  RISK MANAGEMENT")
        mm_rate_threshold = risk_config.get('mm_rate_threshold', 90.0)

        print(f"  MM Rate Threshold:   {mm_rate_threshold}% (emergency close when Account MM >= this)")
        print(f"  ⚠️  Note: Old risk parameters (max_total_exposure, liquidation_buffer, emergency_stop_loss) are DEPRECATED")
        print(f"      Bot now uses Account MM Rate from Bybit for liquidation protection")

        # Check 5: Current Price
        print_section("5️⃣  MARKET DATA")
        ticker = client.get_ticker(symbol, strategy_config.get('category', 'linear'))
        if ticker:
            price = float(ticker['lastPrice'])
            print(f"  Current {symbol} Price: ${price:.4f}")
            print(f"  Initial LONG:  ${initial_size:.2f} @ ${price:.4f}")
            print(f"  Initial SHORT: ${initial_size:.2f} @ ${price:.4f}")
        else:
            print(f"  ❌ Could not get price for {symbol}")

        # Final check
        print_section("✅ FINAL CHECK")

        errors = []
        warnings = []

        if not demo and bot_config.get('dry_run') == False:
            errors.append("PRODUCTION MODE ENABLED! This is not a demo!")

        if leverage >= 100:
            warnings.append(f"Very high leverage ({leverage}x) - liquidation risk!")

        if equity > 0 and max_total_risk > equity:
            warnings.append("Max risk exceeds balance - may hit exposure limits")

        if stop_loss >= 0:
            warnings.append("Emergency stop loss is positive (should be negative)")

        if errors:
            print("\n  ❌ ERRORS:")
            for err in errors:
                print(f"     - {err}")

        if warnings:
            print("\n  ⚠️  WARNINGS:")
            for warn in warnings:
                print(f"     - {warn}")

        if not errors:
            print("\n  🎉 All checks passed!")
            print("\n  📋 Ready to launch with:")
            print(f"     python src/main.py")
            print("\n  📊 After running, analyze with:")
            print(f"     python scripts/analyze.py --plot")
        else:
            print("\n  ❌ Please fix errors before launching!")

        print("\n" + "=" * 60 + "\n")

    except Exception as e:
        print(f"\n❌ Error during pre-launch check: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

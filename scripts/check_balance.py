#!/usr/bin/env python3
"""Quick script to check Bybit account balance"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config_loader import ConfigLoader
from src.exchange.bybit_client import BybitClient


def main():
    print("=" * 60)
    print("💰 Checking Bybit Account Balance")
    print("=" * 60)

    try:
        # Load config
        config = ConfigLoader()
        api_key, api_secret = config.get_api_credentials()
        demo = config.is_demo()

        print(f"🔧 Environment: {'DEMO' if demo else 'PRODUCTION'}")
        print(f"🔑 API Key: {api_key[:8]}...{api_key[-4:]}")
        print()

        # Initialize client
        client = BybitClient(
            api_key=api_key,
            api_secret=api_secret,
            demo=demo
        )

        # Get wallet balance
        print("📊 Fetching wallet balance...")
        balance_data = client.get_wallet_balance()

        if balance_data:
            print("\n✅ Balance retrieved successfully!")
            print("-" * 60)

            # Parse balance - direct access to list (no 'result' wrapper in some responses)
            accounts = balance_data.get('list', balance_data.get('result', {}).get('list', []))

            for account in accounts:
                account_type = account.get('accountType', 'Unknown')
                total_equity = float(account.get('totalEquity', 0))
                total_wallet = float(account.get('totalWalletBalance', 0))
                total_available = float(account.get('totalAvailableBalance', 0))
                total_pnl = float(account.get('totalPerpUPL', 0))

                print(f"\n📁 Account Type: {account_type}")
                print(f"  💰 Total Equity: ${total_equity:.2f}")
                print(f"  💵 Wallet Balance: ${total_wallet:.2f}")
                print(f"  ✅ Available: ${total_available:.2f}")
                print(f"  📊 Unrealized PnL: ${total_pnl:.2f}")

                if 'coin' in account:
                    print(f"\n  💎 Coin Breakdown:")
                    for coin_data in account['coin']:
                        coin = coin_data.get('coin', 'N/A')
                        equity = float(coin_data.get('equity', 0))
                        wallet_balance = float(coin_data.get('walletBalance', 0))
                        realised_pnl = float(coin_data.get('cumRealisedPnl', 0))

                        if equity > 0:  # Only show coins with balance
                            print(f"     {coin}:")
                            print(f"       Equity: {equity:.4f}")
                            print(f"       Wallet: {wallet_balance:.4f}")
                            print(f"       Realized PnL: {realised_pnl:.4f}")

            print("\n" + "-" * 60)

            # Show what will be used in analytics
            print("\n📈 Analytics Configuration:")
            risk_config = config.get_risk_config()
            initial_balance = risk_config.get('max_total_exposure', 1000.0)
            print(f"   Initial Balance (from config): ${initial_balance:.2f}")
            print(f"   This value is used as starting point in MetricsTracker")

        else:
            print("❌ Failed to retrieve balance")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    print("\n" + "=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())

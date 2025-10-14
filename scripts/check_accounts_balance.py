#!/usr/bin/env python3
"""Check balance for multiple accounts"""

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


def get_account_balance(account_id, api_key, api_secret, demo):
    """Get balance info for a specific account"""
    try:
        client = BybitClient(
            api_key=api_key,
            api_secret=api_secret,
            demo=demo
        )

        balance_data = client.get_wallet_balance()

        if balance_data and 'list' in balance_data:
            account = balance_data['list'][0]

            return {
                'total_equity': float(account.get('totalEquity', 0)),
                'total_wallet_balance': float(account.get('totalWalletBalance', 0)),
                'total_margin_balance': float(account.get('totalMarginBalance', 0)),
                'total_available_balance': float(account.get('totalAvailableBalance', 0)),
                'total_initial_margin': float(account.get('totalInitialMargin', 0)),
                'total_maint_margin': float(account.get('totalMaintenanceMargin', 0)),
                'account_mm_rate': float(account.get('accountMMRate', 0)) * 100,
                'account_type': account.get('accountType', 'Unknown')
            }
        return None
    except Exception as e:
        return {'error': str(e)}


def main():
    print("=" * 80)
    print("💰 Multi-Account Balance Check")
    print("=" * 80)

    # Load environment and config
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

        # Get credentials from environment
        api_key = os.getenv(api_key_env)
        api_secret = os.getenv(api_secret_env)

        print(f"\n{'='*80}")
        print(f"🏦 Account {account_id:03d}: {account_name}")
        print(f"{'='*80}")
        print(f"🔧 Environment: {'DEMO' if demo else 'PRODUCTION'}")

        if not api_key or not api_secret:
            print(f"❌ API credentials not found in .env ({api_key_env}, {api_secret_env})")
            continue

        print(f"🔑 API Key: {api_key[:8]}...{api_key[-4:]}")
        print("\n📊 Fetching balance...")

        balance = get_account_balance(account_id, api_key, api_secret, demo)

        if balance is None:
            print("❌ Failed to retrieve balance (no data returned)")
        elif 'error' in balance:
            print(f"❌ Error: {balance['error']}")
        else:
            print("\n✅ Balance retrieved successfully!")
            print("-" * 80)
            print(f"  Account Type:           {balance['account_type']}")
            print(f"  Total Equity:           {balance['total_equity']:.4f} USDT")
            print(f"  Total Wallet Balance:   {balance['total_wallet_balance']:.4f} USDT")
            print(f"  Total Margin Balance:   {balance['total_margin_balance']:.4f} USDT")
            print(f"  Available Balance:      {balance['total_available_balance']:.4f} USDT")
            print(f"  Initial Margin (IM):    {balance['total_initial_margin']:.4f} USDT")
            print(f"  Maintenance Margin (MM): {balance['total_maint_margin']:.4f} USDT")
            print(f"  Account MM Rate:        {balance['account_mm_rate']:.2f}%")
            print("-" * 80)

    print("\n" + "=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())

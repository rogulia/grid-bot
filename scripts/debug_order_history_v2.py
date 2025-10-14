#!/usr/bin/env python3
"""Debug script to analyze order history for position restoration issues"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import json
from datetime import datetime
from dotenv import load_dotenv
import yaml
from src.exchange.bybit_client import BybitClient


def ms_to_datetime(ms):
    """Convert millisecond timestamp to readable datetime"""
    if not ms or ms == 0:
        return "N/A"
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime('%Y-%m-%d %H:%M:%S')
    except:
        return f"Invalid: {ms}"


def load_config():
    """Load config from yaml"""
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def get_order_history(account_id, symbol, limit=100):
    """Get order history for specific account and symbol"""
    load_dotenv()
    config = load_config()

    # Find account config
    account_config = None
    for acc in config.get('accounts', []):
        if acc.get('id') == account_id:
            account_config = acc
            break

    if not account_config:
        print(f"❌ Account {account_id} not found in config")
        return None

    # Get credentials
    api_key_env = account_config.get('api_key_env')
    api_secret_env = account_config.get('api_secret_env')
    demo = account_config.get('demo_trading', True)

    api_key = os.getenv(api_key_env)
    api_secret = os.getenv(api_secret_env)

    if not api_key or not api_secret:
        print(f"❌ API credentials not found for {api_key_env}")
        return None

    print(f"🔧 Account {account_id:03d}")
    print(f"🔧 Environment: {'DEMO' if demo else 'PRODUCTION'}")
    print(f"🔧 Symbol: {symbol}")
    print(f"📊 Fetching last {limit} orders...")

    # Create Bybit client
    client = BybitClient(
        api_key=api_key,
        api_secret=api_secret,
        demo=demo
    )

    try:
        orders = client.get_order_history(
            symbol=symbol,
            category="linear",
            limit=limit
        )

        if not orders:
            print(f"❌ No orders returned")
            return None

        print(f"✅ Retrieved {len(orders)} orders")
        return orders

    except Exception as e:
        print(f"❌ Exception: {e}")
        import traceback
        traceback.print_exc()
        return None


def analyze_orders(orders, symbol):
    """Analyze and format order data"""
    print(f"\n{'='*120}")
    print(f"📋 ORDER HISTORY ANALYSIS: {symbol}")
    print(f"{'='*120}\n")

    # Separate by positionIdx
    long_orders = [o for o in orders if o.get('positionIdx') == 1]
    short_orders = [o for o in orders if o.get('positionIdx') == 2]

    print(f"📊 LONG (positionIdx=1): {len(long_orders)} orders")
    print(f"📊 SHORT (positionIdx=2): {len(short_orders)} orders")

    # Analyze LONG orders
    print(f"\n{'─'*120}")
    print("🟢 LONG ORDERS (positionIdx=1):")
    print(f"{'─'*120}")
    print(f"{'#':<4} {'Time':<20} {'Side':<6} {'Qty':<12} {'Price':<12} {'Status':<10} {'Type':<8} {'ReduceOnly':<11} {'OrderID':<36}")
    print(f"{'─'*120}")

    for i, order in enumerate(long_orders):
        order_time = ms_to_datetime(order.get('updatedTime', order.get('createdTime')))
        side = order.get('side', 'N/A')
        qty = order.get('qty', '0')
        price = order.get('avgPrice', order.get('price', '0'))
        status = order.get('orderStatus', 'N/A')
        order_type = order.get('orderType', 'N/A')
        reduce_only = 'Yes' if order.get('reduceOnly') else 'No'
        order_id = order.get('orderId', 'N/A')[:36]

        # Highlight filled opening orders
        marker = "📍" if (status == "Filled" and not order.get('reduceOnly')) else "  "
        marker = "🎯" if (status == "Filled" and order.get('reduceOnly')) else marker

        print(f"{marker}{i:<3} {order_time:<20} {side:<6} {qty:<12} {price:<12} {status:<10} {order_type:<8} {reduce_only:<11} {order_id}")

    # Analyze SHORT orders
    print(f"\n{'─'*120}")
    print("🔴 SHORT ORDERS (positionIdx=2):")
    print(f"{'─'*120}")
    print(f"{'#':<4} {'Time':<20} {'Side':<6} {'Qty':<12} {'Price':<12} {'Status':<10} {'Type':<8} {'ReduceOnly':<11} {'OrderID':<36}")
    print(f"{'─'*120}")

    for i, order in enumerate(short_orders):
        order_time = ms_to_datetime(order.get('updatedTime', order.get('createdTime')))
        side = order.get('side', 'N/A')
        qty = order.get('qty', '0')
        price = order.get('avgPrice', order.get('price', '0'))
        status = order.get('orderStatus', 'N/A')
        order_type = order.get('orderType', 'N/A')
        reduce_only = 'Yes' if order.get('reduceOnly') else 'No'
        order_id = order.get('orderId', 'N/A')[:36]

        # Highlight filled opening orders
        marker = "📍" if (status == "Filled" and not order.get('reduceOnly')) else "  "
        marker = "🎯" if (status == "Filled" and order.get('reduceOnly')) else marker

        print(f"{marker}{i:<3} {order_time:<20} {side:<6} {qty:<12} {price:<12} {status:<10} {order_type:<8} {reduce_only:<11} {order_id}")

    print(f"\n{'='*120}")
    print("Legend:")
    print("  📍 = Opening order (reduceOnly=False)")
    print("  🎯 = Closing/TP order (reduceOnly=True)")
    print(f"{'='*120}\n")


def main():
    print("=" * 120)
    print("🔍 ORDER HISTORY DEBUG TOOL V2")
    print("=" * 120)

    # Account 001, DOGEUSDT
    account_id = 1
    symbol = "DOGEUSDT"

    orders = get_order_history(account_id, symbol, limit=100)

    if not orders:
        print("❌ Failed to retrieve orders")
        return 1

    # Save to JSON
    output_file = Path(__file__).parent.parent / "data" / f"{account_id:03d}_order_history_{symbol}_debug.json"
    with open(output_file, 'w') as f:
        json.dump(orders, f, indent=2)

    print(f"💾 Saved raw data to: {output_file}")

    # Analyze and display
    analyze_orders(orders, symbol)

    # Additional analysis: Find current open position
    print("\n" + "="*120)
    print("🔍 CURRENT POSITION ANALYSIS")
    print("="*120 + "\n")

    # Find last TP closes for each side
    long_orders_filled = [o for o in orders if o.get('positionIdx') == 1 and o.get('orderStatus') == 'Filled']
    short_orders_filled = [o for o in orders if o.get('positionIdx') == 2 and o.get('orderStatus') == 'Filled']

    # LONG: Find last TP close
    long_tp_closes = [o for o in long_orders_filled if o.get('reduceOnly') and o.get('side') == 'Sell']
    if long_tp_closes:
        last_long_tp = long_tp_closes[0]  # Already sorted by time (newest first)
        print(f"🎯 Last LONG TP close: {last_long_tp.get('side')} {last_long_tp.get('qty')} @ {last_long_tp.get('avgPrice')}")
        print(f"   Time: {ms_to_datetime(last_long_tp.get('updatedTime'))}")
        print(f"   OrderID: {last_long_tp.get('orderId')}")

        # Find opens after this TP
        tp_time = int(last_long_tp.get('updatedTime', 0))
        long_opens_after = [
            o for o in long_orders_filled
            if not o.get('reduceOnly')
            and o.get('side') == 'Buy'
            and int(o.get('updatedTime', 0)) > tp_time
        ]

        print(f"\n📍 LONG opening orders after last TP: {len(long_opens_after)}")
        total_qty = 0
        for i, o in enumerate(reversed(long_opens_after)):  # Show oldest first
            qty = float(o.get('qty', 0))
            total_qty += qty
            print(f"   {i}: Buy {qty} @ {o.get('avgPrice')} (Time: {ms_to_datetime(o.get('updatedTime'))})")
        print(f"   Total: {total_qty} DOGE")
    else:
        print("⚠️  No LONG TP closes found in history")

    print(f"\n{'─'*120}\n")

    # SHORT: Find last TP close
    short_tp_closes = [o for o in short_orders_filled if o.get('reduceOnly') and o.get('side') == 'Buy']
    if short_tp_closes:
        last_short_tp = short_tp_closes[0]
        print(f"🎯 Last SHORT TP close: {last_short_tp.get('side')} {last_short_tp.get('qty')} @ {last_short_tp.get('avgPrice')}")
        print(f"   Time: {ms_to_datetime(last_short_tp.get('updatedTime'))}")
        print(f"   OrderID: {last_short_tp.get('orderId')}")

        # Find opens after this TP
        tp_time = int(last_short_tp.get('updatedTime', 0))
        short_opens_after = [
            o for o in short_orders_filled
            if not o.get('reduceOnly')
            and o.get('side') == 'Sell'
            and int(o.get('updatedTime', 0)) > tp_time
        ]

        print(f"\n📍 SHORT opening orders after last TP: {len(short_opens_after)}")
        total_qty = 0
        for i, o in enumerate(reversed(short_opens_after)):  # Show oldest first
            qty = float(o.get('qty', 0))
            total_qty += qty
            print(f"   {i}: Sell {qty} @ {o.get('avgPrice')} (Time: {ms_to_datetime(o.get('updatedTime'))})")
        print(f"   Total: {total_qty} DOGE")
    else:
        print("⚠️  No SHORT TP closes found in history")

    print(f"\n{'='*120}")
    print("✅ Analysis complete!")
    print(f"{'='*120}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())

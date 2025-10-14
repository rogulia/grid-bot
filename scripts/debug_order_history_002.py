#!/usr/bin/env python3
"""
Diagnostic script to analyze Bybit Order History for Account 002 ETHUSDT
"""

import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from src.exchange.bybit_client import BybitClient


def main():
    """Fetch and display Order History for ETHUSDT"""

    # Load environment variables
    load_dotenv()

    # Account 002 credentials
    api_key = os.getenv("2_BYBIT_API_KEY")
    api_secret = os.getenv("2_BYBIT_API_SECRET")

    if not api_key or not api_secret:
        print("❌ Error: Account 002 credentials not found in .env")
        print("   Expected: 2_BYBIT_API_KEY and 2_BYBIT_API_SECRET")
        sys.exit(1)

    print("=" * 80)
    print("Bybit Order History Diagnostic Tool - Account 002")
    print("=" * 80)
    print(f"Account: 002")
    print(f"Symbol: ETHUSDT")
    print(f"Environment: DEMO")
    print("=" * 80)
    print()

    # Create Bybit client (demo mode)
    client = BybitClient(
        api_key=api_key,
        api_secret=api_secret,
        demo=True
    )

    # Fetch order history
    print("📥 Fetching order history (last 50 orders)...")
    orders = client.get_order_history(
        symbol="ETHUSDT",
        category="linear",
        limit=50
    )

    if not orders:
        print("❌ No orders found or API error")
        return

    print(f"✅ Retrieved {len(orders)} orders")
    print()

    # Display summary table
    print("=" * 80)
    print("ORDER SUMMARY (chronological order, oldest first)")
    print("=" * 80)

    # Sort by creation time
    sorted_orders = sorted(orders, key=lambda x: int(x.get('createdTime', 0)))

    print(f"{'#':<4} {'Side':<5} {'PosIdx':<7} {'Qty':<10} {'Price':<10} {'Type':<8} {'Status':<10} {'RedOnly':<8}")
    print("-" * 80)

    for i, order in enumerate(sorted_orders):
        side = order.get('side', 'N/A')
        pos_idx = str(order.get('positionIdx', 'N/A'))
        qty = order.get('cumExecQty', '0')
        price = order.get('avgPrice', '0')
        order_type = order.get('orderType', 'N/A')
        status = order.get('orderStatus', 'N/A')
        reduce_only = order.get('reduceOnly', False)

        print(f"{i:<4} {side:<5} {pos_idx:<7} {qty:<10} {price:<10} {order_type:<8} {status:<10} {reduce_only!s:<8}")

    print()

    # Analyze LONG position (positionIdx=1)
    print("=" * 80)
    print("LONG POSITION ANALYSIS (positionIdx=1)")
    print("=" * 80)

    long_orders = [
        o for o in sorted_orders
        if o.get('positionIdx') == 1 and o.get('orderStatus') == 'Filled'
    ]

    print(f"Total filled orders with positionIdx=1: {len(long_orders)}")
    print()

    # Find last TP close (Sell reduceOnly=True)
    last_tp_idx = -1
    for i in range(len(long_orders) - 1, -1, -1):
        if long_orders[i].get('side') == 'Sell' and long_orders[i].get('reduceOnly') is True:
            last_tp_idx = i
            print(f"✅ Last TP close found at index {i}:")
            print(f"   {long_orders[i].get('side')} {long_orders[i].get('cumExecQty')} @ {long_orders[i].get('avgPrice')}")
            break

    if last_tp_idx < 0:
        print("❌ No TP close found!")
        current_long_orders = [o for o in long_orders if o.get('side') == 'Buy' and o.get('reduceOnly') is False]
    else:
        orders_after_tp = long_orders[last_tp_idx + 1:]
        current_long_orders = [o for o in orders_after_tp if o.get('side') == 'Buy' and o.get('reduceOnly') is False]

    print(f"\nCurrent LONG position orders: {len(current_long_orders)}")
    total_long_qty = 0
    for i, o in enumerate(current_long_orders):
        qty = float(o.get('cumExecQty', 0))
        price = float(o.get('avgPrice', 0))
        total_long_qty += qty
        print(f"  Level {i}: {qty} @ ${price:.2f}")

    print(f"\n📊 Total LONG quantity: {total_long_qty}")

    # Analyze SHORT position (positionIdx=2)
    print()
    print("=" * 80)
    print("SHORT POSITION ANALYSIS (positionIdx=2)")
    print("=" * 80)

    short_orders = [
        o for o in sorted_orders
        if o.get('positionIdx') == 2 and o.get('orderStatus') == 'Filled'
    ]

    print(f"Total filled orders with positionIdx=2: {len(short_orders)}")
    print()

    # Find last TP close (Buy reduceOnly=True)
    last_tp_idx = -1
    for i in range(len(short_orders) - 1, -1, -1):
        if short_orders[i].get('side') == 'Buy' and short_orders[i].get('reduceOnly') is True:
            last_tp_idx = i
            print(f"✅ Last TP close found at index {i}:")
            print(f"   {short_orders[i].get('side')} {short_orders[i].get('cumExecQty')} @ {short_orders[i].get('avgPrice')}")
            break

    if last_tp_idx < 0:
        print("❌ No TP close found!")
        current_short_orders = [o for o in short_orders if o.get('side') == 'Sell' and o.get('reduceOnly') is False]
    else:
        orders_after_tp = short_orders[last_tp_idx + 1:]
        current_short_orders = [o for o in orders_after_tp if o.get('side') == 'Sell' and o.get('reduceOnly') is False]

    print(f"\nCurrent SHORT position orders: {len(current_short_orders)}")
    total_short_qty = 0
    for i, o in enumerate(current_short_orders):
        qty = float(o.get('cumExecQty', 0))
        price = float(o.get('avgPrice', 0))
        total_short_qty += qty
        print(f"  Level {i}: {qty} @ ${price:.2f}")

    print(f"\n📊 Total SHORT quantity: {total_short_qty}")

    print()
    print("=" * 80)


if __name__ == "__main__":
    main()

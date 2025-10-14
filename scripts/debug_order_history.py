#!/usr/bin/env python3
"""
Diagnostic script to analyze Bybit Order History format
Helps understand why reduceOnly detection fails during position restoration
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
    """Fetch and display Order History for DOGEUSDT"""

    # Load environment variables
    load_dotenv()

    # Account 001 credentials
    api_key = os.getenv("1_BYBIT_API_KEY")
    api_secret = os.getenv("1_BYBIT_API_SECRET")

    if not api_key or not api_secret:
        print("❌ Error: Account 001 credentials not found in .env")
        print("   Expected: 1_BYBIT_API_KEY and 1_BYBIT_API_SECRET")
        sys.exit(1)

    print("=" * 80)
    print("Bybit Order History Diagnostic Tool")
    print("=" * 80)
    print(f"Account: 001")
    print(f"Symbol: DOGEUSDT")
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
        symbol="DOGEUSDT",
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
    print("=" * 80)
    print("DETAILED ORDER INFORMATION")
    print("=" * 80)

    # Display detailed info for each order
    for i, order in enumerate(sorted_orders):
        print(f"\n{'=' * 80}")
        print(f"Order #{i}")
        print(f"{'=' * 80}")

        # Key fields
        print(f"Side:          {order.get('side')}")
        print(f"Position Idx:  {order.get('positionIdx')} (type: {type(order.get('positionIdx')).__name__})")
        print(f"Order Type:    {order.get('orderType')}")
        print(f"Order Status:  {order.get('orderStatus')}")
        print(f"Reduce Only:   {order.get('reduceOnly')} (type: {type(order.get('reduceOnly')).__name__})")
        print(f"Quantity:      {order.get('cumExecQty')}")
        print(f"Price:         {order.get('avgPrice')}")
        print(f"Created Time:  {order.get('createdTime')}")
        print(f"Updated Time:  {order.get('updatedTime')}")

        # Full JSON dump
        print("\nFull JSON:")
        print(json.dumps(order, indent=2))

    print()
    print("=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)
    print()
    print("🔍 Key things to check:")
    print("   1. Format of 'reduceOnly' field (bool, str, int?)")
    print("   2. Which orders have reduceOnly=True?")
    print("   3. Are TP close orders (reduceOnly=True) present?")
    print("   4. What is 'positionIdx' for each side (Buy=1, Sell=2)?")
    print("   5. Difference between Filled and Cancelled orders")
    print()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Save Order History for Account 002 ETHUSDT to JSON file
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from src.exchange.bybit_client import BybitClient


def main():
    """Fetch and save Order History for ETHUSDT"""

    # Load environment variables
    load_dotenv()

    # Account 002 credentials
    api_key = os.getenv("2_BYBIT_API_KEY")
    api_secret = os.getenv("2_BYBIT_API_SECRET")

    if not api_key or not api_secret:
        print("❌ Error: Account 002 credentials not found in .env")
        sys.exit(1)

    print("=" * 80)
    print("Fetching Order History - Account 002 ETHUSDT")
    print("=" * 80)

    # Create Bybit client (demo mode)
    client = BybitClient(
        api_key=api_key,
        api_secret=api_secret,
        demo=True
    )

    # Fetch order history (increase limit to 100)
    print("📥 Fetching order history (last 100 orders)...")
    orders = client.get_order_history(
        symbol="ETHUSDT",
        category="linear",
        limit=100
    )

    if not orders:
        print("❌ No orders found or API error")
        return

    print(f"✅ Retrieved {len(orders)} orders")

    # Sort by creation time (oldest first)
    sorted_orders = sorted(orders, key=lambda x: int(x.get('createdTime', 0)))

    # Prepare output data
    output = {
        "metadata": {
            "account_id": "002",
            "symbol": "ETHUSDT",
            "environment": "DEMO",
            "fetch_time": datetime.now().isoformat(),
            "total_orders": len(orders)
        },
        "orders": sorted_orders,
        "analysis": {
            "long_position": {},
            "short_position": {}
        }
    }

    # Analyze LONG position (positionIdx=1)
    long_orders = [
        o for o in sorted_orders
        if o.get('positionIdx') == 1 and o.get('orderStatus') == 'Filled'
    ]

    # Find last TP close for LONG
    last_tp_idx = -1
    for i in range(len(long_orders) - 1, -1, -1):
        if long_orders[i].get('side') == 'Sell' and long_orders[i].get('reduceOnly') is True:
            last_tp_idx = i
            break

    if last_tp_idx >= 0:
        orders_after_tp = long_orders[last_tp_idx + 1:]
        current_long_orders = [o for o in orders_after_tp if o.get('side') == 'Buy' and o.get('reduceOnly') is False]
    else:
        current_long_orders = [o for o in long_orders if o.get('side') == 'Buy' and o.get('reduceOnly') is False]

    total_long_qty = sum(float(o.get('cumExecQty', 0)) for o in current_long_orders)

    output["analysis"]["long_position"] = {
        "total_filled_orders": len(long_orders),
        "last_tp_close_index": last_tp_idx,
        "last_tp_close": long_orders[last_tp_idx] if last_tp_idx >= 0 else None,
        "current_position_orders_count": len(current_long_orders),
        "current_position_orders": [
            {
                "side": o.get('side'),
                "qty": float(o.get('cumExecQty', 0)),
                "price": float(o.get('avgPrice', 0)),
                "reduceOnly": o.get('reduceOnly'),
                "createdTime": o.get('createdTime')
            }
            for o in current_long_orders
        ],
        "total_quantity": total_long_qty
    }

    # Analyze SHORT position (positionIdx=2)
    short_orders = [
        o for o in sorted_orders
        if o.get('positionIdx') == 2 and o.get('orderStatus') == 'Filled'
    ]

    # Find last TP close for SHORT
    last_tp_idx = -1
    for i in range(len(short_orders) - 1, -1, -1):
        if short_orders[i].get('side') == 'Buy' and short_orders[i].get('reduceOnly') is True:
            last_tp_idx = i
            break

    if last_tp_idx >= 0:
        orders_after_tp = short_orders[last_tp_idx + 1:]
        current_short_orders = [o for o in orders_after_tp if o.get('side') == 'Sell' and o.get('reduceOnly') is False]
    else:
        current_short_orders = [o for o in short_orders if o.get('side') == 'Sell' and o.get('reduceOnly') is False]

    total_short_qty = sum(float(o.get('cumExecQty', 0)) for o in current_short_orders)

    output["analysis"]["short_position"] = {
        "total_filled_orders": len(short_orders),
        "last_tp_close_index": last_tp_idx,
        "last_tp_close": short_orders[last_tp_idx] if last_tp_idx >= 0 else None,
        "current_position_orders_count": len(current_short_orders),
        "current_position_orders": [
            {
                "side": o.get('side'),
                "qty": float(o.get('cumExecQty', 0)),
                "price": float(o.get('avgPrice', 0)),
                "reduceOnly": o.get('reduceOnly'),
                "createdTime": o.get('createdTime')
            }
            for o in current_short_orders
        ],
        "total_quantity": total_short_qty
    }

    # Save to file
    output_file = Path("data/002_order_history_ETHUSDT.json")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\n✅ Order History saved to: {output_file}")
    print(f"\n📊 Summary:")
    print(f"   LONG: {len(current_long_orders)} orders, total qty: {total_long_qty}")
    print(f"   SHORT: {len(current_short_orders)} orders, total qty: {total_short_qty}")
    print()


if __name__ == "__main__":
    main()

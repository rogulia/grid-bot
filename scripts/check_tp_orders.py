#!/usr/bin/env python3
"""Script to check and display current TP orders on exchange"""

import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from src.exchange.bybit_client import BybitClient

# Load environment variables
load_dotenv()

def main():
    """Check current TP orders on Bybit"""
    print("🔍 Checking TP orders on exchange...\n")

    # Initialize client
    api_key = os.getenv('BYBIT_API_KEY')
    api_secret = os.getenv('BYBIT_API_SECRET')
    demo = os.getenv('BYBIT_ENV', 'demo') == 'demo'

    client = BybitClient(api_key, api_secret, demo=demo)
    print(f"Connected to {'DEMO' if demo else 'PRODUCTION'} environment\n")

    # Check both symbols
    symbols = ['SOLUSDT', 'DOGEUSDT']

    for symbol in symbols:
        print(f"{'='*60}")
        print(f"Symbol: {symbol}")
        print(f"{'='*60}")

        # Get all open orders
        orders = client.get_open_orders(symbol, category="linear")

        if not orders:
            print(f"  No open orders found for {symbol}\n")
            continue

        # Filter TP orders (reduce-only)
        tp_orders = [o for o in orders if o.get('reduceOnly', False)]

        if not tp_orders:
            print(f"  No TP orders found for {symbol}")
            print(f"  Total open orders: {len(orders)} (none are reduce-only)\n")
            continue

        print(f"  Found {len(tp_orders)} TP order(s):\n")

        for order in tp_orders:
            order_id = order.get('orderId')
            side = order.get('side')
            qty = order.get('qty')
            price = order.get('price')
            pos_idx = order.get('positionIdx')
            status = order.get('orderStatus')

            position_type = 'LONG' if pos_idx == 1 else 'SHORT' if pos_idx == 2 else 'Unknown'

            print(f"    📌 Order ID: {order_id}")
            print(f"       Position: {position_type} (positionIdx={pos_idx})")
            print(f"       Side: {side}")
            print(f"       Qty: {qty}")
            print(f"       Price: ${price}")
            print(f"       Status: {status}")
            print()

        # Also show non-TP orders if any
        non_tp_orders = [o for o in orders if not o.get('reduceOnly', False)]
        if non_tp_orders:
            print(f"  Other open orders (not TP): {len(non_tp_orders)}")
            for order in non_tp_orders:
                print(f"    - {order.get('side')} {order.get('qty')} @ ${order.get('price')} "
                      f"(ID: {order.get('orderId')[:8]}...)")
            print()

    print(f"{'='*60}")
    print("✅ Check complete!")


if __name__ == "__main__":
    main()

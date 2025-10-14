#!/usr/bin/env python3
"""Save current order history to file for debugging"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import json
from datetime import datetime
from dotenv import load_dotenv
from src.exchange.bybit_client import BybitClient

load_dotenv()

# Account 001
api_key = os.getenv('1_BYBIT_API_KEY')
api_secret = os.getenv('1_BYBIT_API_SECRET')

client = BybitClient(api_key=api_key, api_secret=api_secret, demo=True)

print("Fetching order history...")
orders = client.get_order_history(symbol="DOGEUSDT", category="linear", limit=100)

print(f"Retrieved {len(orders)} orders")

# Save to file
output_file = Path(__file__).parent.parent / "data" / "001_order_history_DOGEUSDT_current.json"
with open(output_file, 'w') as f:
    json.dump(orders, f, indent=2)

print(f"Saved to: {output_file}")

# Show LONG orders with timestamps
long_orders = [o for o in orders if o.get('positionIdx') == 1 and o.get('orderStatus') == 'Filled']

print(f"\nLONG orders ({len(long_orders)} total):")
print(f"{'#':<3} {'Side':<6} {'Qty':<12} {'Price':<12} {'RedOnly':<8} {'UpdatedTime (ms)':<18} {'OrderID':<12}")
print("-" * 85)

def ms_to_time(ms):
    return datetime.fromtimestamp(int(ms) / 1000).strftime('%H:%M:%S.%f')[:-3]

for i, o in enumerate(long_orders):
    side = o.get('side')
    qty = o.get('qty')
    price = o.get('avgPrice', o.get('price', '0'))
    red_only = 'Yes' if o.get('reduceOnly') else 'No'
    updated_time = o.get('updatedTime', 0)
    time_str = ms_to_time(updated_time)
    order_id = o.get('orderId', '')[-8:]

    marker = "🎯" if o.get('reduceOnly') else "📍"

    print(f"{marker}{i:<2} {side:<6} {qty:<12} {price:<12} {red_only:<8} {updated_time:<18} ({time_str}) {order_id}")

print("\nDone!")

#!/usr/bin/env python3
"""Debug restoration logic step by step to find the issue"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import json
from datetime import datetime
from dotenv import load_dotenv
from src.exchange.bybit_client import BybitClient

load_dotenv()

api_key = os.getenv('1_BYBIT_API_KEY')
api_secret = os.getenv('1_BYBIT_API_SECRET')

client = BybitClient(api_key=api_key, api_secret=api_secret, demo=True)

def ms_to_time(ms):
    return datetime.fromtimestamp(int(ms) / 1000).strftime('%H:%M:%S.%f')[:-3]

print("="*120)
print("RESTORATION LOGIC DEBUG - STEP BY STEP")
print("="*120)
print()

# Step 1: Fetch order history
print("STEP 1: Fetch order history (limit=100)")
print("-"*120)
orders = client.get_order_history(symbol="DOGEUSDT", category="linear", limit=100)
print(f"Total orders fetched: {len(orders)}")
print()

# Step 2: Show first 10 orders to see what we're working with
print("STEP 2: First 10 orders (all statuses, all sides)")
print("-"*120)
print(f"{'Idx':<4} {'OrderID':<12} {'Side':<6} {'Qty':<10} {'PosIdx':<7} {'RedOnly':<8} {'Status':<12} {'UpdatedTime':<18}")
print("-"*120)
for i in range(min(10, len(orders))):
    o = orders[i]
    order_id = o.get('orderId', '')[-8:]
    side = o.get('side', '')
    qty = o.get('qty', '0')
    pos_idx = o.get('positionIdx', 0)
    red_only = 'Yes' if o.get('reduceOnly') else 'No'
    status = o.get('orderStatus', '')
    updated = o.get('updatedTime', 0)
    time_str = ms_to_time(updated)

    print(f"{i:<4} {order_id:<12} {side:<6} {qty:<10} {pos_idx:<7} {red_only:<8} {status:<12} {time_str}")
print()

# Step 3: Filter for LONG filled orders (positionIdx=1)
print("STEP 3: Filter for LONG (positionIdx=1, Filled status)")
print("-"*120)
position_idx = 1
position_orders = [o for o in orders if o.get('positionIdx') == position_idx and o.get('orderStatus') == 'Filled']
print(f"Found {len(position_orders)} filled LONG orders")
print()

# CRITICAL: Sort by createdTime (oldest first) - same as bot logic
position_orders.sort(key=lambda x: int(x.get('createdTime', 0)))
print("Sorted by createdTime (oldest first)")
print()

print(f"{'Idx':<4} {'OrderID':<12} {'Side':<6} {'Qty':<10} {'RedOnly':<8} {'UpdatedTime':<18}")
print("-"*120)
for i, o in enumerate(position_orders):
    order_id = o.get('orderId', '')[-8:]
    side = o.get('side', '')
    qty = o.get('qty', '0')
    red_only = 'Yes' if o.get('reduceOnly') else 'No'
    updated = o.get('updatedTime', 0)
    time_str = ms_to_time(updated)

    marker = "🎯" if o.get('reduceOnly') else "📍"
    print(f"{marker} {i:<3} {order_id:<12} {side:<6} {qty:<10} {red_only:<8} {time_str}")
print()

# Step 4: Get exchange quantity (target)
print("STEP 4: Get target quantity from exchange")
print("-"*120)
side = 'Buy'

try:
    position_exchange = client.get_active_position(symbol="DOGEUSDT", category="linear", side=side)
    if position_exchange:
        long_pos_exchange = position_exchange[0] if isinstance(position_exchange, list) else position_exchange
        target_qty = float(long_pos_exchange.get('size', '0'))
    else:
        target_qty = 0.0
except Exception as e:
    print(f"Could not fetch position: {e}")
    target_qty = 0.0

print(f"Exchange shows: {target_qty} DOGE")
print()

# Step 5: Get all opening orders
print("STEP 5: Get all opening Buy orders")
print("-"*120)

# Get all opening orders
open_orders = [
    o for o in position_orders
    if o.get('side') == side and not o.get('reduceOnly')
]

print(f"Found {len(open_orders)} opening {side} orders in history")
print()

# Step 6: Take newest orders that sum to exchange quantity
print("STEP 6: Take newest orders that sum to exchange quantity")
print("-"*120)

# Orders are sorted oldest-first from STEP 3, reverse to newest-first
open_orders_newest_first = list(reversed(open_orders))

current_position_orders = []
cumulative_qty = 0.0

for order in open_orders_newest_first:
    qty = float(order.get('qty', '0'))
    order_id = order.get('orderId', '')[-8:]

    if cumulative_qty + qty <= target_qty + 0.001:  # tolerance
        current_position_orders.append(order)
        cumulative_qty += qty
        print(f"  ✅ Order {order_id}: {side} {qty} (cumulative: {cumulative_qty}/{target_qty})")

        # Stop when we reach target
        if abs(cumulative_qty - target_qty) <= 0.001:
            print(f"  ✅ Reached target quantity!")
            break
    else:
        print(f"  ⏭️  Order {order_id}: {side} {qty} - SKIPPED (would exceed target)")

# Reverse back to oldest-first for grid level assignment
current_position_orders.reverse()

print()
print("STEP 7: Restored position summary")
print("-"*120)
print(f"Total open orders to restore: {len(current_position_orders)}")
print()
print(f"{'Idx':<4} {'OrderID':<12} {'Side':<6} {'Qty':<10} {'Price':<12} {'UpdatedTime':<18}")
print("-"*120)

total_qty = 0.0
for i, o in enumerate(current_position_orders):
    order_id = o.get('orderId', '')[-8:]
    side = o.get('side', '')
    qty = float(o.get('qty', '0'))
    price = o.get('avgPrice', '0')
    updated = o.get('updatedTime', 0)
    time_str = ms_to_time(updated)

    total_qty += qty

    # Highlight the orders we expect (380, 381)
    marker = "✅" if order_id in ['a756b292', '5c47ad61'] else "❓"
    print(f"{marker} {i:<3} {order_id:<12} {side:<6} {qty:<10} {price:<12} {time_str}")

print("-"*120)
print(f"Total restored quantity: {total_qty} DOGE")
print()

# Step 8: Final validation
print("STEP 8: Final validation")
print("-"*120)

print(f"Target quantity (exchange): {target_qty} DOGE")
print(f"Restored quantity (history): {total_qty} DOGE")
diff = abs(target_qty - total_qty)
print(f"Difference: {diff} DOGE")
print()

if diff <= 0.001:
    print("✅ SUCCESS: Quantities match!")
elif total_qty < target_qty:
    print(f"❌ FAIL: Restored LESS than exchange (missing {target_qty - total_qty} DOGE)")
    print("   Possible causes:")
    print("   1. Order history limit too small")
    print("   2. Orders missing from history")
else:
    print(f"❌ FAIL: Restored MORE than exchange (extra {total_qty - target_qty} DOGE)")
    print("   Possible causes:")
    print("   1. Algorithm included too many orders")
    print("   2. Logic error in cumulative sum")

print()
print("="*120)

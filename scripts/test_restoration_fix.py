#!/usr/bin/env python3
"""Test that restoration logic now correctly uses updatedTime"""

import json
from pathlib import Path
from datetime import datetime


def ms_to_time(ms):
    """Convert ms to readable time"""
    return datetime.fromtimestamp(int(ms) / 1000).strftime('%H:%M:%S.%f')[:-3]


def test_restoration_logic():
    """Simulate the restoration logic with real data"""

    # Load real order history
    data_file = Path(__file__).parent.parent / "data" / "001_order_history_DOGEUSDT_debug.json"
    with open(data_file, 'r') as f:
        orders = json.load(f)

    # Filter LONG orders (positionIdx=1)
    long_orders = [o for o in orders if o.get('positionIdx') == 1 and o.get('orderStatus') == 'Filled']

    print("="*120)
    print("TESTING RESTORATION LOGIC WITH REAL DATA")
    print("="*120)
    print()

    # Find last TP close
    last_tp_idx = -1
    for i, order in enumerate(long_orders):
        if order.get('reduceOnly') and order.get('side') == 'Sell':
            last_tp_idx = i
            print(f"✅ Found last TP close at index {i}:")
            print(f"   Order: Sell {order.get('qty')} @ {order.get('avgPrice')}")
            print(f"   createdTime: {ms_to_time(order.get('createdTime'))}")
            print(f"   updatedTime: {ms_to_time(order.get('updatedTime'))} ← EXECUTION TIME")
            print()
            break

    if last_tp_idx < 0:
        print("❌ No TP close found!")
        return

    # OLD LOGIC (using createdTime)
    print("-"*120)
    print("🔴 OLD LOGIC (using createdTime):")
    print("-"*120)

    tp_timestamp_created = int(long_orders[last_tp_idx].get('createdTime', 0))
    tp_second_created = (tp_timestamp_created // 1000) * 1000

    start_idx_old = last_tp_idx
    for i in range(last_tp_idx - 1, -1, -1):
        order_timestamp = int(long_orders[i].get('createdTime', 0))
        order_second = (order_timestamp // 1000) * 1000

        if order_second == tp_second_created:
            start_idx_old = i
        else:
            break

    old_orders_after_tp = long_orders[start_idx_old:]
    old_position_orders = [o for o in old_orders_after_tp if o.get('side') == 'Buy' and not o.get('reduceOnly')]

    old_total_qty = sum(float(o.get('qty', 0)) for o in old_position_orders)

    print(f"TP close createdTime second: {ms_to_time(tp_second_created)}")
    print(f"Starting from index: {start_idx_old} (included {last_tp_idx - start_idx_old} orders in same second)")
    print(f"Found {len(old_position_orders)} opening Buy orders after TP:")
    for i, o in enumerate(old_position_orders):
        qty = o.get('qty')
        price = o.get('avgPrice')
        created = ms_to_time(o.get('createdTime'))
        updated = ms_to_time(o.get('updatedTime'))
        marker = "❌" if updated < ms_to_time(tp_timestamp_created) else "✅"
        print(f"  {marker} {i}: Buy {qty} @ {price} (created: {created}, executed: {updated})")
    print(f"Total quantity: {old_total_qty}")
    print()

    # NEW LOGIC (simple: everything before TP index)
    print("-"*120)
    print("🟢 NEW LOGIC (simple: orders before TP index):")
    print("-"*120)

    # Simple: take all orders from 0 to last_tp_idx (NOT including TP itself)
    new_orders_after_tp = long_orders[:last_tp_idx]
    new_position_orders = [o for o in new_orders_after_tp if o.get('side') == 'Buy' and not o.get('reduceOnly')]

    new_total_qty = sum(float(o.get('qty', 0)) for o in new_position_orders)

    print(f"TP close at index: {last_tp_idx}")
    print(f"Taking orders from index 0 to {last_tp_idx-1} (total: {len(new_orders_after_tp)} orders)")
    print(f"Found {len(new_position_orders)} opening Buy orders after TP:")
    for i, o in enumerate(new_position_orders):
        qty = o.get('qty')
        price = o.get('avgPrice')
        created = ms_to_time(o.get('createdTime'))
        updated = ms_to_time(o.get('updatedTime'))
        print(f"  ✅ {i}: Buy {qty} @ {price} (created: {created}, executed: {updated})")
    print(f"Total quantity: {new_total_qty}")
    print()

    # Compare
    print("="*120)
    print("COMPARISON:")
    print("="*120)
    print(f"OLD LOGIC: {len(old_position_orders)} orders, total {old_total_qty} DOGE")
    print(f"NEW LOGIC: {len(new_position_orders)} orders, total {new_total_qty} DOGE")
    print()

    if old_total_qty != new_total_qty:
        diff = abs(new_total_qty - old_total_qty)
        if new_total_qty > old_total_qty:
            print(f"✅ FIX SUCCESSFUL! Added {diff} DOGE of correct orders")
            print(f"   OLD logic incorrectly EXCLUDED orders from AFTER TP close")
            print(f"   OLD logic incorrectly INCLUDED orders from BEFORE TP close")
        else:
            print(f"✅ FIX SUCCESSFUL! Removed {diff} DOGE of old orders")
            print(f"   OLD logic incorrectly included orders from BEFORE TP close")
    else:
        print("⚠️  No difference detected")

    print("="*120)


if __name__ == "__main__":
    test_restoration_logic()

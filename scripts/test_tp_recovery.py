#!/usr/bin/env python3
"""Test script to verify TP order ID recovery logic"""

import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from src.exchange.bybit_client import BybitClient
from src.strategy.position_manager import PositionManager
from src.core.state_manager import StateManager
from src.utils.logger import setup_logger

# Load environment variables
load_dotenv()

def test_recovery_for_symbol(client, symbol, logger):
    """Test TP order ID recovery for a symbol"""
    print(f"\n{'='*60}")
    print(f"Testing recovery for {symbol}")
    print(f"{'='*60}\n")

    # Initialize managers
    pm = PositionManager(symbol=symbol)
    state_manager = StateManager()

    # Load state from file
    state = state_manager.load_state()
    if state is None:
        state = {}
    symbol_state = state.get(symbol, {})

    # Restore positions
    for pos in symbol_state.get('long_positions', []):
        pm.add_position('Buy', pos['entry_price'], pos['quantity'], pos['grid_level'])
    for pos in symbol_state.get('short_positions', []):
        pm.add_position('Sell', pos['entry_price'], pos['quantity'], pos['grid_level'])

    # Check current TP order IDs (should be null)
    long_tp_before = pm.get_tp_order_id('Buy')
    short_tp_before = pm.get_tp_order_id('Sell')

    print(f"📋 State before recovery:")
    print(f"   LONG positions: {pm.get_position_count('Buy')}, TP ID: {long_tp_before}")
    print(f"   SHORT positions: {pm.get_position_count('Sell')}, TP ID: {short_tp_before}")

    # Get positions from exchange
    long_exchange = client.get_active_position(symbol, 'Buy', 'linear')
    short_exchange = client.get_active_position(symbol, 'Sell', 'linear')

    print(f"\n📊 Exchange positions:")
    if long_exchange:
        print(f"   LONG: {long_exchange.get('size')} @ ${long_exchange.get('avgPrice')}")
    else:
        print(f"   LONG: None")
    if short_exchange:
        print(f"   SHORT: {short_exchange.get('size')} @ ${short_exchange.get('avgPrice')}")
    else:
        print(f"   SHORT: None")

    # Now test recovery logic for each side
    print(f"\n🔄 Testing recovery logic...\n")

    for side in ['Buy', 'Sell']:
        side_name = 'LONG' if side == 'Buy' else 'SHORT'
        current_tp_id = pm.get_tp_order_id(side)

        if not current_tp_id:
            try:
                # Get all open orders
                open_orders = client.get_open_orders(symbol, 'linear')
                position_idx = 1 if side == 'Buy' else 2

                print(f"   Checking {side_name} (positionIdx={position_idx})...")

                for order in open_orders:
                    is_reduce_only = order.get('reduceOnly', False)
                    order_pos_idx = int(order.get('positionIdx', 0))

                    if is_reduce_only and order_pos_idx == position_idx:
                        order_id = order.get('orderId')
                        order_price = order.get('price')
                        print(f"   ✅ Found {side_name} TP order: {order_id} @ ${order_price}")

                        # Update in position manager
                        pm.set_tp_order_id(side, order_id)
                        break
                else:
                    print(f"   ⚠️  No TP order found for {side_name}")

            except Exception as e:
                print(f"   ❌ Error recovering {side_name} TP order: {e}")
        else:
            print(f"   ℹ️  {side_name} TP ID already set: {current_tp_id}")

    # Check final state
    long_tp_after = pm.get_tp_order_id('Buy')
    short_tp_after = pm.get_tp_order_id('Sell')

    print(f"\n📋 State after recovery:")
    print(f"   LONG TP ID: {long_tp_after}")
    print(f"   SHORT TP ID: {short_tp_after}")

    print(f"\n✅ Recovery successful for {symbol}!")


def main():
    """Test TP order ID recovery"""
    print("🧪 Testing TP Order ID Recovery Logic\n")

    # Setup logger
    logger = setup_logger("test_tp_recovery", log_level="INFO")

    # Initialize client
    api_key = os.getenv('BYBIT_API_KEY')
    api_secret = os.getenv('BYBIT_API_SECRET')
    demo = os.getenv('BYBIT_ENV', 'demo') == 'demo'

    client = BybitClient(api_key, api_secret, demo=demo)
    logger.info(f"Connected to {'DEMO' if demo else 'PRODUCTION'} environment")

    # Test for both symbols
    symbols = ['SOLUSDT', 'DOGEUSDT']

    for symbol in symbols:
        test_recovery_for_symbol(client, symbol, logger)

    print(f"\n{'='*60}")
    print("✅ Recovery test complete!")
    print("{'='*60}\n")


if __name__ == "__main__":
    main()

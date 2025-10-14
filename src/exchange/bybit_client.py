"""Bybit HTTP API client for order execution and position management"""

import logging
from typing import Dict, Any, Optional, List
from pybit.unified_trading import HTTP


class BybitClient:
    """Wrapper for Bybit HTTP API using pybit"""

    def __init__(self, api_key: str, api_secret: str, demo: bool = True):
        """
        Initialize Bybit HTTP client

        Args:
            api_key: Bybit API key
            api_secret: Bybit API secret
            demo: Use demo trading (default: True)
        """
        self.logger = logging.getLogger("sol-trader.bybit_client")
        self.demo = demo

        self.session = HTTP(
            demo=demo,
            api_key=api_key,
            api_secret=api_secret
        )

        self.logger.info(f"Bybit client initialized (demo={demo})")

    def set_position_mode(self, symbol: str, mode: int = 3, category: str = "linear") -> Dict:
        """
        Set position mode for a symbol (One-Way or Hedge Mode)

        Args:
            symbol: Trading symbol (e.g., 'SOLUSDT')
            mode: 0=Merged Single (One-Way Mode), 3=Both Sides (Hedge Mode)
            category: Market category (linear, inverse, etc.)

        Returns:
            API response
        """
        try:
            response = self.session.switch_position_mode(
                category=category,
                symbol=symbol,
                mode=mode  # 3 = Hedge Mode (Both Sides)
            )
            mode_name = "Hedge Mode" if mode == 3 else "One-Way Mode"
            self.logger.info(f"Set position mode to {mode_name} for {symbol}")
            return response
        except Exception as e:
            # Error 110025 means position mode is already set - not a real error
            if "110025" in str(e) or "position mode is not modified" in str(e).lower():
                mode_name = "Hedge Mode" if mode == 3 else "One-Way Mode"
                self.logger.info(f"Position mode already set to {mode_name} for {symbol}")
                return {"retCode": 0, "retMsg": "position mode already set"}
            self.logger.error(f"Failed to set position mode: {e}")
            raise

    def set_leverage(self, symbol: str, leverage: int, category: str = "linear") -> Dict:
        """
        Set leverage for a symbol

        Args:
            symbol: Trading symbol (e.g., 'SOLUSDT')
            leverage: Leverage value (e.g., 100)
            category: Market category (linear, inverse, etc.)

        Returns:
            API response
        """
        try:
            response = self.session.set_leverage(
                category=category,
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage)
            )
            self.logger.info(f"Set leverage to {leverage}x for {symbol}")
            return response
        except Exception as e:
            # Error 110043 means leverage is already at desired value - not a real error
            if "110043" in str(e) or "leverage not modified" in str(e).lower():
                self.logger.info(f"Leverage already set to {leverage}x for {symbol}")
                return {"retCode": 0, "retMsg": "leverage already set"}
            self.logger.error(f"Failed to set leverage: {e}")
            raise

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "Market",
        price: Optional[float] = None,
        category: str = "linear",
        position_idx: Optional[int] = None,
        reduce_only: bool = False
    ) -> Dict:
        """
        Place an order

        Args:
            symbol: Trading symbol
            side: 'Buy' or 'Sell'
            qty: Order quantity
            order_type: 'Market' or 'Limit'
            price: Limit price (required for Limit orders)
            category: Market category
            position_idx: Position index (None=auto-detect for hedge mode, 0=one-way, 1=buy hedge, 2=sell hedge)
            reduce_only: If True, order can only reduce position (for TP/SL)

        Returns:
            API response with order details
        """
        try:
            # Auto-detect positionIdx for Hedge Mode if not specified
            if position_idx is None:
                # Hedge Mode: 1=Buy/LONG, 2=Sell/SHORT
                position_idx = 1 if side == "Buy" else 2

            order_params = {
                "category": category,
                "symbol": symbol,
                "side": side,
                "orderType": order_type,
                "qty": str(qty),
                "positionIdx": position_idx
            }

            if order_type == "Limit" and price:
                order_params["price"] = str(price)
                order_params["timeInForce"] = "GTC"  # Good Till Cancel

            # Add reduceOnly flag if specified (for TP/SL orders)
            if reduce_only:
                order_params["reduceOnly"] = True

            # Debug logging for TP orders
            if reduce_only:
                self.logger.info(f"🔍 TP Order Params: {order_params}")
                print(f"[DEBUG] TP Order Params: {order_params}", flush=True)

            response = self.session.place_order(**order_params)
            reduce_tag = " [REDUCE ONLY]" if reduce_only else ""
            self.logger.info(
                f"Placed {order_type} order{reduce_tag}: {side} {qty} {symbol} "
                f"@ {price if price else 'MARKET'}"
            )
            return response
        except Exception as e:
            self.logger.error(f"Failed to place order: {e}")
            raise

    def get_positions(self, symbol: str, category: str = "linear") -> List[Dict]:
        """
        Get current positions

        Args:
            symbol: Trading symbol
            category: Market category

        Returns:
            List of position dictionaries
        """
        try:
            response = self.session.get_positions(
                category=category,
                symbol=symbol
            )

            if response.get('retCode') == 0:
                positions = response.get('result', {}).get('list', [])
                return positions
            else:
                self.logger.error(f"Failed to get positions: {response}")
                return []
        except Exception as e:
            self.logger.error(f"Error getting positions: {e}")
            return []

    def get_active_position(self, symbol: str, side: str, category: str = "linear") -> Optional[Dict]:
        """
        Get active position for specific side

        Args:
            symbol: Trading symbol
            side: 'Buy' (LONG) or 'Sell' (SHORT)
            category: Market category

        Returns:
            Position dict or None if no active position
        """
        try:
            positions = self.get_positions(symbol, category)

            for pos in positions:
                # Check if this is the right side and has non-zero size
                pos_side = pos.get('side', '')
                pos_size = float(pos.get('size', 0))

                if pos_side == side and pos_size > 0:
                    self.logger.debug(
                        f"Found active {side} position: {pos_size} @ ${pos.get('avgPrice', 'N/A')}"
                    )
                    return pos

            return None
        except Exception as e:
            self.logger.error(f"Error getting active position: {e}")
            return None

    def close_position(
        self,
        symbol: str,
        side: str,
        qty: Optional[float] = None,
        category: str = "linear",
        position_idx: Optional[int] = None
    ) -> Dict:
        """
        Close a position (market order in opposite direction)

        Args:
            symbol: Trading symbol
            side: Original position side ('Buy' or 'Sell')
            qty: Quantity to close (None = close all)
            category: Market category
            position_idx: Position index (None=auto-detect for hedge mode)

        Returns:
            API response
        """
        # Auto-detect positionIdx for Hedge Mode if not specified
        if position_idx is None:
            # Hedge Mode: 1=Buy/LONG, 2=Sell/SHORT
            position_idx = 1 if side == "Buy" else 2

        # Opposite side to close
        close_side = "Sell" if side == "Buy" else "Buy"

        if qty is None:
            # Get current position size
            positions = self.get_positions(symbol, category)
            if positions:
                for pos in positions:
                    if pos.get('side') == side:
                        qty = float(pos.get('size', 0))
                        break

        if not qty or qty == 0:
            self.logger.warning(f"No position to close for {side} {symbol}")
            return {}

        return self.place_order(
            symbol=symbol,
            side=close_side,
            qty=qty,
            order_type="Market",
            category=category,
            position_idx=position_idx,
            reduce_only=True  # Close orders must be reduce-only!
        )

    def get_wallet_balance(self, account_type: str = "UNIFIED") -> Dict:
        """
        Get wallet balance

        Args:
            account_type: Account type (UNIFIED, CONTRACT, etc.)

        Returns:
            Balance information
        """
        try:
            response = self.session.get_wallet_balance(
                accountType=account_type
            )

            if response.get('retCode') == 0:
                return response.get('result', {})
            else:
                self.logger.error(f"Failed to get wallet balance: {response}")
                return {}
        except Exception as e:
            self.logger.error(f"Error getting wallet balance: {e}")
            return {}

    def get_ticker(self, symbol: str, category: str = "linear") -> Optional[Dict]:
        """
        Get current ticker (price) information

        Args:
            symbol: Trading symbol
            category: Market category

        Returns:
            Ticker data or None
        """
        try:
            response = self.session.get_tickers(
                category=category,
                symbol=symbol
            )

            if response.get('retCode') == 0:
                tickers = response.get('result', {}).get('list', [])
                return tickers[0] if tickers else None
            else:
                self.logger.error(f"Failed to get ticker: {response}")
                return None
        except Exception as e:
            self.logger.error(f"Error getting ticker: {e}")
            return None

    def place_tp_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        tp_price: float,
        category: str = "linear",
        position_idx: Optional[int] = None
    ) -> Optional[str]:
        """
        Place a Take Profit limit order (reduce-only)

        Args:
            symbol: Trading symbol
            side: 'Buy' or 'Sell' (opposite to position side)
            qty: Order quantity
            tp_price: Take profit price
            category: Market category
            position_idx: Position index (None=auto-detect for hedge mode)

        Returns:
            Order ID or None if failed
        """
        print(f"[DEBUG] place_tp_order ENTRY: {side} {qty} @ ${tp_price:.4f}, positionIdx={position_idx}", flush=True)
        self.logger.info(f"🎯 place_tp_order called: {side} {qty} @ ${tp_price:.4f}, positionIdx={position_idx}")
        try:
            # Auto-detect positionIdx for Hedge Mode if not specified
            if position_idx is None:
                position_idx = 1 if side == "Buy" else 2

            self.logger.debug(f"Calling place_order with positionIdx={position_idx}, reduce_only=True")
            response = self.place_order(
                symbol=symbol,
                side=side,
                qty=qty,
                order_type="Limit",
                price=tp_price,
                category=category,
                position_idx=position_idx,
                reduce_only=True  # TP orders must be reduce-only!
            )

            # Detailed logging of response
            self.logger.debug(f"TP order response: {response}")

            if response.get('retCode') == 0:
                order_id = response.get('result', {}).get('orderId')
                if order_id:
                    print(f"[DEBUG] place_tp_order SUCCESS: ID={order_id}", flush=True)
                    self.logger.info(f"✅ TP order placed: {side} {qty} @ ${tp_price:.4f} (ID: {order_id})")
                    return order_id
                else:
                    print(f"[DEBUG] place_tp_order FAIL: No orderId in response", flush=True)
                    self.logger.error(f"❌ TP order response has no orderId: {response}")
                    return None
            else:
                print(f"[DEBUG] place_tp_order FAIL: retCode={response.get('retCode')}", flush=True)
                self.logger.error(f"❌ TP order failed (retCode={response.get('retCode')}): {response}")
                return None
        except Exception as e:
            print(f"[DEBUG] place_tp_order EXCEPTION: {e}", flush=True)
            self.logger.error(f"❌ Exception in place_tp_order: {e}", exc_info=True)
            return None

    def get_open_orders(
        self,
        symbol: str,
        category: str = "linear",
        limit: int = 50
    ) -> List[Dict]:
        """
        Get all open orders for a symbol

        Args:
            symbol: Trading symbol
            category: Market category
            limit: Maximum number of orders to return (default: 50)

        Returns:
            List of open order dictionaries, each containing:
                - orderId: Order ID
                - symbol: Trading symbol
                - side: 'Buy' or 'Sell'
                - orderType: 'Market' or 'Limit'
                - price: Order price
                - qty: Order quantity
                - reduceOnly: True if reduce-only order
                - positionIdx: Position index (1=LONG, 2=SHORT in hedge mode)
                - orderStatus: Order status
                - createdTime: Creation timestamp
        """
        try:
            response = self.session.get_open_orders(
                category=category,
                symbol=symbol,
                limit=limit
            )

            if response.get('retCode') == 0:
                orders = response.get('result', {}).get('list', [])
                self.logger.debug(f"Retrieved {len(orders)} open orders for {symbol}")
                return orders
            else:
                self.logger.error(f"Failed to get open orders: {response}")
                return []
        except Exception as e:
            self.logger.error(f"Error getting open orders: {e}")
            return []

    def cancel_order(
        self,
        symbol: str,
        order_id: str,
        category: str = "linear"
    ) -> bool:
        """
        Cancel an order

        Args:
            symbol: Trading symbol
            order_id: Order ID to cancel
            category: Market category

        Returns:
            True if cancelled successfully
        """
        try:
            response = self.session.cancel_order(
                category=category,
                symbol=symbol,
                orderId=order_id
            )

            if response.get('retCode') == 0:
                self.logger.info(f"Order cancelled: {order_id}")
                return True
            else:
                self.logger.warning(f"Failed to cancel order {order_id}: {response}")
                return False
        except Exception as e:
            self.logger.warning(f"Error cancelling order {order_id}: {e}")
            return False

    def get_closed_pnl(
        self,
        symbol: str,
        category: str = "linear",
        limit: int = 50
    ) -> List[Dict]:
        """
        Get closed P&L records from exchange

        This returns actual realized PnL with real fees paid (not estimated).
        Use this after closing a position to get accurate profit/loss data.

        Args:
            symbol: Trading symbol (e.g., 'SOLUSDT')
            category: Market category (default: 'linear')
            limit: Number of records to retrieve (default: 50, max: 100)

        Returns:
            List of closed PnL records, each containing:
                - closedPnl: Actual profit/loss
                - openFee: Fee paid to open position
                - closeFee: Fee paid to close position
                - avgEntryPrice: Average entry price
                - avgExitPrice: Average exit price
                - qty: Quantity
                - symbol, side, createdTime, etc.

        Example:
            >>> records = client.get_closed_pnl('SOLUSDT', limit=1)
            >>> if records:
            ...     print(f"PnL: {records[0]['closedPnl']}")
            ...     print(f"Open fee: {records[0]['openFee']}")
            ...     print(f"Close fee: {records[0]['closeFee']}")
        """
        try:
            response = self.session.get_closed_pnl(
                category=category,
                symbol=symbol,
                limit=limit
            )

            if response.get('retCode') == 0:
                records = response.get('result', {}).get('list', [])
                self.logger.debug(
                    f"Retrieved {len(records)} closed PnL records for {symbol}"
                )
                return records
            else:
                self.logger.error(f"Failed to get closed PnL: {response}")
                return []

        except Exception as e:
            self.logger.error(f"Error getting closed PnL: {e}")
            return []

    def get_order_history(
        self,
        symbol: str,
        category: str = "linear",
        limit: int = 50
    ) -> List[Dict]:
        """
        Get order history (executed/cancelled orders)

        This is used to reconstruct position history after bot restart.

        Args:
            symbol: Trading symbol (e.g., 'SOLUSDT')
            category: Market category (default: 'linear')
            limit: Number of records to retrieve (default: 50, max: 50)

        Returns:
            List of order records, each containing:
                - orderId: Order ID
                - symbol: Trading symbol
                - side: 'Buy' or 'Sell'
                - orderType: 'Market' or 'Limit'
                - price: Order price
                - qty: Order quantity
                - cumExecQty: Executed quantity
                - avgPrice: Average execution price
                - reduceOnly: True if reduce-only order
                - positionIdx: Position index (1=LONG, 2=SHORT in hedge mode)
                - orderStatus: Order status ('Filled', 'Cancelled', etc.)
                - createdTime: Creation timestamp (ms)
                - updatedTime: Update timestamp (ms)

        Example:
            >>> # Get recent order history for SOLUSDT
            >>> orders = client.get_order_history('SOLUSDT', limit=50)
            >>> for order in orders:
            ...     print(f"{order['side']} {order['qty']} @ {order['avgPrice']}")
        """
        try:
            response = self.session.get_order_history(
                category=category,
                symbol=symbol,
                limit=limit
            )

            if response.get('retCode') == 0:
                orders = response.get('result', {}).get('list', [])
                self.logger.debug(f"Retrieved {len(orders)} order history records for {symbol}")
                return orders
            else:
                self.logger.error(f"Failed to get order history: {response}")
                return []

        except Exception as e:
            self.logger.error(f"Error getting order history: {e}")
            return []

    def get_transaction_log(
        self,
        symbol: str = None,
        category: str = "linear",
        type: str = None,
        limit: int = 50
    ) -> List[Dict]:
        """
        Get transaction log including funding settlements

        This is useful for tracking funding fees paid/received over time.

        Args:
            symbol: Trading symbol (optional, None = all symbols)
            category: Market category (default: 'linear')
            type: Transaction type filter:
                   - "SETTLEMENT" for funding fees
                   - "TRANSFER_IN" for deposits
                   - None for all types
            limit: Number of records (default: 50, max: 100)

        Returns:
            List of transaction records, each containing:
                - type: Transaction type (e.g., "SETTLEMENT")
                - symbol: Trading symbol
                - category: Category
                - transactionTime: Timestamp
                - qty: Quantity
                - funding: Funding fee amount (negative = paid)
                - etc.

        Example:
            >>> # Get funding fees for SOLUSDT
            >>> records = client.get_transaction_log(
            ...     symbol='SOLUSDT',
            ...     type='SETTLEMENT',
            ...     limit=10
            ... )
            >>> total_funding = sum(float(r.get('funding', 0)) for r in records)
            >>> print(f"Total funding fees: ${total_funding:.4f}")
        """
        try:
            params = {
                "category": category,
                "limit": limit
            }

            if symbol:
                params["symbol"] = symbol

            if type:
                params["type"] = type

            response = self.session.get_transaction_log(**params)

            if response.get('retCode') == 0:
                records = response.get('result', {}).get('list', [])
                self.logger.debug(
                    f"Retrieved {len(records)} transaction log entries" +
                    (f" for {symbol}" if symbol else "") +
                    (f" (type={type})" if type else "")
                )
                return records
            else:
                self.logger.error(f"Failed to get transaction log: {response}")
                return []

        except Exception as e:
            self.logger.error(f"Error getting transaction log: {e}")
            return []


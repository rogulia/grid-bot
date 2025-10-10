"""Main entry point for SOL-Trader bot"""

import asyncio
import signal
import sys
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config_loader import ConfigLoader
from src.utils.logger import setup_logger, log_position_state
from src.exchange.bybit_client import BybitClient
from src.exchange.bybit_websocket import BybitWebSocket
from src.strategy.position_manager import PositionManager
from src.strategy.grid_strategy import GridStrategy
from src.analytics.metrics_tracker import MetricsTracker


class TradingBot:
    """Main trading bot orchestrator (Multi-symbol support)"""

    def __init__(self):
        self.config = ConfigLoader()
        self.logger = setup_logger(
            log_level=self.config.get('bot.log_level', 'INFO')
        )

        # Shared components (one per bot instance)
        self.client: BybitClient = None
        self.metrics_tracker: MetricsTracker = None

        # Per-symbol components (one per trading symbol)
        self.strategies: dict[str, GridStrategy] = {}  # {symbol: GridStrategy}
        self.position_managers: dict[str, PositionManager] = {}  # {symbol: PositionManager}
        self.websockets: dict[str, BybitWebSocket] = {}  # {symbol: WebSocket}
        self.last_log_times: dict[str, float] = {}  # {symbol: last_log_timestamp}

        self.running = False

    async def initialize(self):
        """Initialize all components"""
        try:
            self.logger.info("=" * 60)
            self.logger.info("🤖 SOL-Trader Bot Starting (Multi-Symbol)...")
            self.logger.info("=" * 60)

            # Get API credentials
            api_key, api_secret = self.config.get_api_credentials()

            # Get all strategies
            strategies_config = self.config.get_strategies_config()
            if not strategies_config:
                raise ValueError("No strategies configured! Check config/config.yaml")

            risk_config = self.config.get_risk_config()
            bot_config = self.config.get_bot_config()
            dry_run = bot_config.get('dry_run', True)
            demo = self.config.is_demo()

            self.logger.info(f"🎯 Trading {len(strategies_config)} symbol(s)")
            self.logger.info(f"🔧 Mode: {'DRY RUN' if dry_run else 'LIVE'}")
            self.logger.info(f"💰 Environment: {'DEMO' if demo else 'PRODUCTION'}")

            # Initialize Bybit client (shared across all symbols)
            self.logger.info("Initializing Bybit client...")
            self.client = BybitClient(
                api_key=api_key,
                api_secret=api_secret,
                demo=demo
            )

            # Get real account balance for analytics (REQUIRED - no fallback!)
            self.logger.info("Fetching account balance...")
            balance_data = self.client.get_wallet_balance()
            if not balance_data:
                raise RuntimeError("Failed to get wallet balance from exchange - cannot start bot")

            accounts = balance_data.get('list', balance_data.get('result', {}).get('list', []))
            if not accounts:
                raise RuntimeError("No accounts found in wallet balance response - cannot start bot")

            if 'totalEquity' not in accounts[0]:
                raise RuntimeError("Account balance missing 'totalEquity' field - cannot start bot")

            initial_balance = float(accounts[0]['totalEquity'])
            self.logger.info(f"💰 Account Balance: ${initial_balance:.2f}")

            # Initialize metrics tracker (shared across all symbols)
            self.logger.info("Initializing metrics tracker...")
            self.metrics_tracker = MetricsTracker(initial_balance=initial_balance)

            # Initialize each trading symbol
            for strategy_config in strategies_config:
                symbol = strategy_config['symbol']
                leverage = strategy_config['leverage']
                category = strategy_config.get('category', 'linear')

                self.logger.info("=" * 60)
                self.logger.info(f"📊 Initializing {symbol}")
                self.logger.info(f"⚡ Leverage: {leverage}x")
                self.logger.info("=" * 60)

                # Set position mode to Hedge Mode (required for dual-sided trading)
                if not dry_run:
                    self.client.set_position_mode(symbol, mode=3, category=category)  # 3 = Hedge Mode
                else:
                    self.logger.info(f"[DRY RUN] Would set position mode to Hedge Mode")

                # Set leverage for this symbol
                if not dry_run:
                    self.client.set_leverage(symbol, leverage, category)
                else:
                    self.logger.info(f"[DRY RUN] Would set leverage to {leverage}x")

                # Initialize position manager for this symbol
                self.position_managers[symbol] = PositionManager(
                    leverage=leverage,
                    symbol=symbol
                )

                # Initialize strategy for this symbol
                combined_config = {**strategy_config, **risk_config}
                self.strategies[symbol] = GridStrategy(
                    client=self.client,
                    position_manager=self.position_managers[symbol],
                    config=combined_config,
                    dry_run=dry_run,
                    metrics_tracker=self.metrics_tracker
                )

                # Get initial price
                ticker = self.client.get_ticker(symbol, category)
                if ticker:
                    initial_price = float(ticker['lastPrice'])
                    self.logger.info(f"💵 Initial price: ${initial_price:.4f}")
                else:
                    raise Exception(f"Failed to get initial price for {symbol}")

                # Sync positions with exchange
                self.strategies[symbol].sync_with_exchange(initial_price)

                # Initialize WebSocket for this symbol
                def make_price_callback(sym):
                    """Create price callback closure for this symbol"""
                    return lambda price: self.on_price_update(sym, price)

                self.websockets[symbol] = BybitWebSocket(
                    symbol=symbol,
                    price_callback=make_price_callback(symbol),
                    demo=demo,
                    channel_type=category
                )
                self.websockets[symbol].start()

                # Initialize last log time
                self.last_log_times[symbol] = 0

                self.logger.info(f"✅ {symbol} initialized successfully!")

            self.logger.info("=" * 60)
            self.logger.info("✅ Bot initialized successfully!")
            self.logger.info("=" * 60)

        except Exception as e:
            self.logger.error(f"Failed to initialize bot: {e}", exc_info=True)
            raise

    def on_price_update(self, symbol: str, price: float):
        """
        Callback for price updates from WebSocket

        Args:
            symbol: Trading symbol (e.g., SOLUSDT)
            price: Current market price
        """
        try:
            # Get components for this symbol
            strategy = self.strategies.get(symbol)
            position_manager = self.position_managers.get(symbol)

            if not strategy or not position_manager:
                self.logger.error(f"No strategy/position_manager for {symbol}")
                return

            # Execute strategy
            strategy.on_price_update(price)

            # Log position state and sync periodically (every 60 seconds)
            current_time = time.time()
            last_log_time = self.last_log_times.get(symbol, 0)

            if current_time - last_log_time >= 60:
                # Sync positions with exchange (reopens if TP executed)
                strategy.sync_with_exchange(price)

                long_pnl = position_manager.calculate_pnl(price, 'Buy')
                short_pnl = position_manager.calculate_pnl(price, 'Sell')

                self.logger.info(f"[{symbol}] Price: ${price:.4f} | "
                               f"LONG PnL: ${long_pnl:.2f} | SHORT PnL: ${short_pnl:.2f}")

                # Логировать unrealized PnL с учетом fees (для live trading)
                if not strategy.dry_run:
                    for side_name, side in [('LONG', 'Buy'), ('SHORT', 'Sell')]:
                        if position_manager.get_total_quantity(side) > 0:
                            try:
                                # Получить данные позиции с биржи
                                position_data = client.get_active_position(symbol, side)
                                if position_data:
                                    exchange_unrealized = float(position_data.get('unrealisedPnl', 0))

                                    # Рассчитать с учетом fees
                                    pnl_info = position_manager.calculate_unrealized_pnl_with_fees(
                                        price, side, exchange_unrealized
                                    )

                                    if pnl_info['base_pnl'] != 0:  # Только если есть позиция
                                        self.logger.info(
                                            f"[{symbol}] {side_name} Unrealized PnL: "
                                            f"Base=${pnl_info['base_pnl']:.4f}, "
                                            f"Est.Open Fee=${pnl_info['estimated_open_fee']:.4f}, "
                                            f"Est.Close Fee=${pnl_info['estimated_close_fee']:.4f}, "
                                            f"NET=${pnl_info['net_pnl']:.4f}"
                                        )
                            except Exception as e:
                                self.logger.debug(f"Could not calculate unrealized PnL with fees for {side_name}: {e}")

                # Log metrics snapshot to CSV
                if self.metrics_tracker:
                    self.metrics_tracker.log_snapshot(
                        symbol=symbol,
                        price=price,
                        long_positions=len(position_manager.long_positions),
                        short_positions=len(position_manager.short_positions),
                        long_qty=position_manager.get_total_quantity('Buy'),
                        short_qty=position_manager.get_total_quantity('Sell'),
                        long_pnl=long_pnl,
                        short_pnl=short_pnl
                    )

                self.last_log_times[symbol] = current_time

        except Exception as e:
            self.logger.error(f"Error in price update handler for {symbol}: {e}", exc_info=True)

    async def run(self):
        """Main run loop"""
        self.running = True

        try:
            await self.initialize()

            self.logger.info("🚀 Bot is now running. Press Ctrl+C to stop.")

            # Keep running
            while self.running:
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            self.logger.info("\n⏹️  Shutdown signal received...")
        except Exception as e:
            self.logger.error(f"Fatal error: {e}", exc_info=True)
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Graceful shutdown"""
        self.logger.info("=" * 60)
        self.logger.info("🛑 Shutting down bot...")
        self.logger.info("=" * 60)

        self.running = False

        # Stop all WebSockets
        for symbol, websocket in self.websockets.items():
            self.logger.info(f"Stopping WebSocket for {symbol}...")
            websocket.stop()

        # Log final state for each symbol
        for symbol in self.strategies.keys():
            position_manager = self.position_managers.get(symbol)
            websocket = self.websockets.get(symbol)

            if position_manager and websocket:
                current_price = websocket.get_current_price()
                if current_price > 0:
                    long_pnl = position_manager.calculate_pnl(current_price, 'Buy')
                    short_pnl = position_manager.calculate_pnl(current_price, 'Sell')
                    total_pnl = long_pnl + short_pnl

                    self.logger.info("=" * 60)
                    self.logger.info(f"📊 FINAL STATE - {symbol}")
                    self.logger.info("=" * 60)
                    self.logger.info(f"Final Price: ${current_price:.4f}")
                    self.logger.info(f"LONG Positions: {len(position_manager.long_positions)}")
                    self.logger.info(f"SHORT Positions: {len(position_manager.short_positions)}")
                    self.logger.info(f"LONG PnL: ${long_pnl:.2f}")
                    self.logger.info(f"SHORT PnL: ${short_pnl:.2f}")
                    self.logger.info(f"TOTAL PnL: ${total_pnl:.2f}")
                    self.logger.info("=" * 60)

                    # Log final metrics snapshot
                    if self.metrics_tracker:
                        self.metrics_tracker.log_snapshot(
                            symbol=symbol,
                            price=current_price,
                            long_positions=len(position_manager.long_positions),
                            short_positions=len(position_manager.short_positions),
                            long_qty=position_manager.get_total_quantity('Buy'),
                            short_qty=position_manager.get_total_quantity('Sell'),
                            long_pnl=long_pnl,
                            short_pnl=short_pnl
                        )

        # Generate and save summary report
        if self.metrics_tracker:
            self.logger.info("Generating performance summary report...")
            summary = self.metrics_tracker.save_summary_report()
            self.logger.info(f"📊 Total PnL: ${summary['performance']['total_pnl']:.2f} ({summary['performance']['roi_percent']:+.2f}%)")
            self.logger.info(f"📈 Win Rate: {summary['performance']['win_rate']:.1f}%")
            self.logger.info("💾 Reports saved to data/summary_report.json and .txt")

        self.logger.info("✅ Bot stopped gracefully")


def main():
    """Entry point"""
    bot = TradingBot()

    # Setup signal handlers for graceful shutdown
    def signal_handler(sig, frame):
        bot.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run bot
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()

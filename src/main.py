"""Multi-Account Trading Bot - Main Entry Point"""

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config_loader import ConfigLoader
from src.core.multi_account_bot import MultiAccountBot
from src.core.trading_account import TradingAccount
from src.utils.timezone import now_helsinki
from src.utils.logger import HelsinkiFormatter


class MultiAccountOrchestrator:
    """
    Main orchestrator for multi-account trading bot

    Manages multiple trading accounts with:
    - Per-account logging (separate log files)
    - WebSocket sharing (one WebSocket per unique (symbol, environment))
    - Complete data isolation between accounts
    - Emergency stop detection
    """

    def __init__(self):
        """Initialize orchestrator"""
        self.config = ConfigLoader()

        # Setup main system logger
        self.logger = self._setup_main_logger()

        # Multi-account bot
        self.bot = MultiAccountBot()

        self.running = False

        # Daily report tracking
        self.last_report_date = None
        self.last_check_minute = None

    def _setup_main_logger(self) -> logging.Logger:
        """
        Setup main system logger for orchestrator

        Logs go to: logs/main_{date}.log

        Returns:
            Configured logger instance
        """
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)

        today = now_helsinki().strftime("%Y-%m-%d")

        logger = logging.getLogger("main")
        logger.setLevel(logging.INFO)
        logger.propagate = False

        # File handler: main_2025-10-10.log
        file_handler = logging.FileHandler(log_dir / f"main_{today}.log")
        formatter = HelsinkiFormatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # Console handler (for systemd/screen)
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        return logger

    def _check_and_generate_daily_reports(self, force_yesterday: bool = False):
        """
        Check if daily reports need to be generated

        Args:
            force_yesterday: If True, generate report for yesterday regardless of time
                            (used on startup to catch missed reports)
        """
        now = now_helsinki()
        today = now.strftime("%Y-%m-%d")
        current_minute = now.strftime("%H:%M")

        # Prevent multiple checks in the same minute
        if not force_yesterday and current_minute == self.last_check_minute:
            return

        self.last_check_minute = current_minute

        # Determine which date to report
        if force_yesterday:
            # On startup: check yesterday
            from datetime import timedelta
            yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            report_date = yesterday
            reason = "startup check"
        elif current_minute in ["00:01", "00:02"]:
            # At 00:01-00:02: report yesterday
            from datetime import timedelta
            yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            report_date = yesterday
            reason = "scheduled"
        else:
            # Not time to generate reports
            return

        # Skip if already generated today
        if self.last_report_date == report_date:
            return

        self.logger.info(f"📊 Generating daily reports for {report_date} ({reason})...")

        # Generate reports for all accounts
        generated_count = 0
        for account in self.bot.accounts:
            try:
                result = account.generate_daily_report(report_date)
                if result:
                    generated_count += 1
            except Exception as e:
                self.logger.error(
                    f"Failed to generate daily report for account {account.id_str}: {e}"
                )

        if generated_count > 0:
            self.logger.info(f"✅ Generated {generated_count} daily report(s) for {report_date}")
            self.last_report_date = report_date
        else:
            self.logger.info(f"No data found for {report_date}, skipping reports")

    async def initialize(self):
        """Initialize all accounts"""
        self.logger.info("=" * 80)
        self.logger.info("🤖 SOL-Trader Multi-Account Bot Starting...")
        self.logger.info("=" * 80)

        try:
            # Load accounts configuration
            accounts_config = self.config.get_accounts_config()

            self.logger.info(f"📊 Total Accounts: {len(accounts_config)}")
            self.logger.info("")

            # ⚠️ Pre-check emergency stop files
            emergency_accounts = []
            for acc_config in accounts_config:
                account_id = acc_config['id']
                id_str = f"{account_id:03d}"
                emergency_file = Path(f"data/.{id_str}_emergency_stop")

                if emergency_file.exists():
                    try:
                        with open(emergency_file) as f:
                            data = json.load(f)
                        emergency_accounts.append((id_str, acc_config['name'], data))
                    except Exception as e:
                        self.logger.error(f"❌ Failed to read emergency stop file {emergency_file}: {e}")
                        emergency_accounts.append((id_str, acc_config['name'], {'reason': 'unknown', 'timestamp': 'unknown'}))

            if emergency_accounts:
                self.logger.error("=" * 80)
                self.logger.error("🚨 EMERGENCY STOP FLAGS DETECTED:")
                self.logger.error("=" * 80)

                for id_str, name, data in emergency_accounts:
                    self.logger.error(f"\nAccount {id_str}: {name}")
                    self.logger.error(f"  File: data/.{id_str}_emergency_stop")
                    self.logger.error(f"  Timestamp: {data.get('timestamp', 'unknown')}")
                    self.logger.error(f"  Reason: {data.get('reason', 'unknown')}")
                    self.logger.error(f"  Symbol: {data.get('symbol', 'N/A')}")

                self.logger.error("\n" + "=" * 80)
                self.logger.error("⚠️  Fix issues and remove emergency stop files:")
                for id_str, _, _ in emergency_accounts:
                    self.logger.error(f"   rm data/.{id_str}_emergency_stop")
                self.logger.error("=" * 80)

                raise RuntimeError(
                    f"{len(emergency_accounts)} account(s) in emergency stop state. "
                    "Fix issues and remove flags before restarting."
                )

            # Initialize each account
            for acc_config in accounts_config:
                try:
                    # Validate config
                    self.config.validate_account_config(acc_config)

                    account_id = acc_config['id']
                    name = acc_config['name']

                    # Get credentials
                    api_key, api_secret = self.config.get_account_credentials(
                        acc_config['api_key_env'],
                        acc_config['api_secret_env']
                    )

                    demo = acc_config['demo_trading']
                    dry_run = acc_config.get('dry_run', False)
                    risk_config = acc_config.get('risk_management', {})

                    id_str = f"{account_id:03d}"
                    self.logger.info(f"🔧 Initializing Account {id_str}: {name}")
                    self.logger.info(f"   Environment: {'DEMO' if demo else 'PRODUCTION ⚠️'}")
                    self.logger.info(f"   Mode: {'DRY RUN' if dry_run else 'LIVE'}")
                    self.logger.info(f"   Strategies: {len(acc_config['strategies'])} symbol(s)")

                    # Create trading account
                    account = TradingAccount(
                        account_id=account_id,
                        name=name,
                        api_key=api_key,
                        api_secret=api_secret,
                        demo=demo,
                        dry_run=dry_run,
                        strategies_config=acc_config['strategies'],
                        risk_config=risk_config
                    )

                    # Initialize account
                    await account.initialize()

                    # Register with multi-account bot
                    self.bot.register_account(account)

                    self.logger.info(f"✅ Account {id_str} ready")
                    self.logger.info("")

                except Exception as e:
                    account_name = acc_config.get('name', 'unknown')
                    self.logger.error(f"❌ Failed to initialize account '{account_name}': {e}", exc_info=True)
                    raise

            # Print statistics
            stats = self.bot.get_stats()
            self.logger.info("=" * 80)
            self.logger.info("📊 Bot Statistics:")
            self.logger.info(f"   Total Accounts: {stats['total_accounts']}")
            self.logger.info(f"   Total WebSockets: {stats['total_websockets']}")
            self.logger.info("   WebSocket Distribution:")
            for ws_key, subscriber_count in stats['websocket_breakdown'].items():
                self.logger.info(f"      {ws_key}: {subscriber_count} account(s)")
            self.logger.info("=" * 80)
            self.logger.info("✅ All accounts initialized successfully!")
            self.logger.info("=" * 80)

            # Check for missing daily reports (in case bot was restarted)
            self._check_and_generate_daily_reports(force_yesterday=True)

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize bot: {e}", exc_info=True)
            raise

    async def run(self):
        """Main run loop"""
        self.running = True

        try:
            await self.initialize()

            self.logger.info("🚀 Bot is running. Press Ctrl+C to stop.")
            self.logger.info("")

            # Keep running
            check_counter = 0
            while self.running:
                await asyncio.sleep(1)

                # Check for daily reports every 60 seconds
                check_counter += 1
                if check_counter >= 60:
                    self._check_and_generate_daily_reports()
                    check_counter = 0

        except KeyboardInterrupt:
            self.logger.info("\n⏹️  Shutdown signal received...")
        except Exception as e:
            self.logger.error(f"❌ Fatal error: {e}", exc_info=True)
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Graceful shutdown"""
        self.logger.info("=" * 80)
        self.logger.info("🛑 Shutting down bot...")
        self.logger.info("=" * 80)

        self.running = False

        # Shutdown multi-account bot (will shutdown all accounts and WebSockets)
        await self.bot.shutdown()

        self.logger.info("=" * 80)
        self.logger.info("✅ Bot stopped gracefully")
        self.logger.info("=" * 80)


def main():
    """Entry point"""
    orchestrator = MultiAccountOrchestrator()

    # Setup signal handlers for graceful shutdown
    def signal_handler(sig, frame):
        orchestrator.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run bot
    asyncio.run(orchestrator.run())


if __name__ == "__main__":
    main()

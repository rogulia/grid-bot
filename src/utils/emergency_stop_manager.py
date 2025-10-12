"""
Emergency Stop Manager - Centralized emergency stop file handling

This module centralizes emergency stop flag file management for the SOL-Trader bot.
Emergency stop files prevent automatic bot restarts after critical failures.

File format: data/.{ID}_emergency_stop (hidden JSON file)
Example: data/.001_emergency_stop

JSON structure:
{
    "timestamp": "2025-01-15T10:30:45+02:00",
    "account_id": 1,
    "symbol": "DOGEUSDT",
    "reason": "Account MM Rate reached critical level"
}
"""

import json
from pathlib import Path
from typing import Optional, Dict
import logging


class EmergencyStopManager:
    """
    Manages emergency stop flag files for multi-account trading bot

    Emergency stop files signal that a bot account has stopped due to a critical
    condition and should not be automatically restarted until the issue is resolved
    and the file is manually removed.
    """

    DATA_DIR = Path("data")

    def __init__(self, logger: Optional[logging.Logger] = None):
        """
        Initialize EmergencyStopManager

        Args:
            logger: Optional logger instance. If not provided, creates default logger.
        """
        self.logger = logger or logging.getLogger(__name__)

    @staticmethod
    def get_file_path(account_id: int) -> Path:
        """
        Get emergency stop file path for account

        Args:
            account_id: Account ID (1-999)

        Returns:
            Path to emergency stop file (e.g., data/.001_emergency_stop)
        """
        id_str = f"{account_id:03d}"
        return EmergencyStopManager.DATA_DIR / f".{id_str}_emergency_stop"

    @staticmethod
    def exists(account_id: int) -> bool:
        """
        Check if emergency stop file exists for account

        Args:
            account_id: Account ID

        Returns:
            True if emergency stop file exists
        """
        return EmergencyStopManager.get_file_path(account_id).exists()

    @staticmethod
    def get_data(account_id: int) -> Optional[Dict]:
        """
        Read emergency stop file data

        Args:
            account_id: Account ID

        Returns:
            Dictionary with emergency stop data, or None if file doesn't exist or invalid
        """
        file_path = EmergencyStopManager.get_file_path(account_id)

        if not file_path.exists():
            return None

        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def create(
        self,
        account_id: int,
        symbol: str,
        reason: str,
        additional_data: Optional[Dict] = None
    ):
        """
        Create emergency stop flag file

        Args:
            account_id: Account ID
            symbol: Trading symbol (e.g., "DOGEUSDT")
            reason: Reason for emergency stop
            additional_data: Optional additional data to include in file
        """
        from ..utils.timezone import now_helsinki

        file_path = self.get_file_path(account_id)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Build flag content
        flag_content = {
            "timestamp": now_helsinki().isoformat(),
            "account_id": account_id,
            "symbol": symbol,
            "reason": reason
        }

        # Add any additional data
        if additional_data:
            flag_content.update(additional_data)

        # Write file
        with open(file_path, 'w') as f:
            json.dump(flag_content, f, indent=2)

        id_str = f"{account_id:03d}"
        self.logger.critical(
            f"🚨 [{symbol}] EMERGENCY STOP FLAG CREATED: {file_path}\n"
            f"   Account ID: {id_str}\n"
            f"   Reason: {reason}\n"
            f"   Bot will not restart automatically.\n"
            f"   Fix issues and remove file: rm {file_path}"
        )

    @staticmethod
    def validate_and_raise(account_id: int, account_name: str):
        """
        Check for emergency stop file and raise error if exists

        This method is used during account initialization to prevent
        startup if emergency stop file exists.

        Args:
            account_id: Account ID
            account_name: Account display name

        Raises:
            RuntimeError: If emergency stop file exists or is corrupted
        """
        id_str = f"{account_id:03d}"
        file_path = EmergencyStopManager.get_file_path(account_id)

        if not file_path.exists():
            return

        # Try to read file data
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)

            raise RuntimeError(
                f"❌ Account {id_str} ({account_name}) has emergency stop flag!\n"
                f"   File: {file_path}\n"
                f"   Timestamp: {data.get('timestamp', 'unknown')}\n"
                f"   Reason: {data.get('reason', 'unknown')}\n"
                f"   Symbol: {data.get('symbol', 'N/A')}\n"
                f"\n"
                f"   Fix issues and remove file:\n"
                f"   rm {file_path}"
            )
        except json.JSONDecodeError:
            raise RuntimeError(
                f"❌ Account {id_str} has corrupted emergency stop file: {file_path}\n"
                f"   Remove it manually: rm {file_path}"
            )

    @staticmethod
    def remove(account_id: int):
        """
        Remove emergency stop file

        This method is typically not called by the bot itself - users should
        manually remove files after fixing issues. Provided for testing/utility purposes.

        Args:
            account_id: Account ID
        """
        file_path = EmergencyStopManager.get_file_path(account_id)
        if file_path.exists():
            file_path.unlink()

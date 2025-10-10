"""Configuration loader for YAML and environment variables"""

import os
from pathlib import Path
from typing import Dict, Any
import yaml
from dotenv import load_dotenv


class ConfigLoader:
    """Load and manage configuration from YAML and .env files"""

    def __init__(self, config_path: str = "config/config.yaml", env_path: str = ".env"):
        """
        Initialize configuration loader

        Args:
            config_path: Path to YAML config file
            env_path: Path to .env file
        """
        self.config_path = Path(config_path)
        self.env_path = Path(env_path)
        self.config: Dict[str, Any] = {}
        self.env_vars: Dict[str, str] = {}

        self._load_config()
        self._load_env()

    def _load_config(self):
        """Load YAML configuration"""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with open(self.config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

    def _load_env(self):
        """Load environment variables from .env file"""
        if self.env_path.exists():
            load_dotenv(self.env_path)

        # Load Bybit credentials
        self.env_vars = {
            'api_key': os.getenv('BYBIT_API_KEY', ''),
            'api_secret': os.getenv('BYBIT_API_SECRET', ''),
            'env': os.getenv('BYBIT_ENV', 'demo')
        }

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value by dot-notation key

        Args:
            key: Configuration key (e.g., 'strategy.leverage')
            default: Default value if key not found

        Returns:
            Configuration value
        """
        keys = key.split('.')
        value = self.config

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default

            if value is None:
                return default

        return value

    def get_exchange_config(self) -> Dict[str, Any]:
        """Get exchange configuration"""
        return self.config.get('exchange', {})

    def get_strategy_config(self) -> Dict[str, Any]:
        """
        Get strategy configuration (backward compatibility)

        Returns first strategy if multiple strategies exist
        """
        strategies = self.config.get('strategies')
        if strategies and isinstance(strategies, list) and len(strategies) > 0:
            return strategies[0]

        # Fallback to old single-strategy format
        return self.config.get('strategy', {})

    def get_strategies_config(self) -> list[Dict[str, Any]]:
        """
        Get all strategies configuration (multi-symbol support)

        Returns:
            List of strategy configs, one per trading symbol
        """
        strategies = self.config.get('strategies')

        # New format: list of strategies
        if strategies and isinstance(strategies, list):
            return strategies

        # Old format: single strategy dict (backward compatibility)
        strategy = self.config.get('strategy')
        if strategy and isinstance(strategy, dict):
            return [strategy]

        return []

    def get_risk_config(self) -> Dict[str, Any]:
        """Get risk management configuration"""
        return self.config.get('risk_management', {})

    def get_bot_config(self) -> Dict[str, Any]:
        """Get bot configuration"""
        return self.config.get('bot', {})

    def get_api_credentials(self) -> tuple[str, str]:
        """
        Get API credentials from environment

        Returns:
            Tuple of (api_key, api_secret)
        """
        api_key = self.env_vars.get('api_key')
        api_secret = self.env_vars.get('api_secret')

        if not api_key or not api_secret:
            raise ValueError(
                "API credentials not found. Please set BYBIT_API_KEY and "
                "BYBIT_API_SECRET in .env file"
            )

        return api_key, api_secret

    def is_demo(self) -> bool:
        """Check if running in demo mode"""
        return self.get('exchange.demo_trading', True)

    def is_dry_run(self) -> bool:
        """Check if running in dry run mode (simulation)"""
        return self.get('bot.dry_run', True)

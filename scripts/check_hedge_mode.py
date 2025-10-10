#!/usr/bin/env python3
"""Check and enable hedge mode for dual-sided trading"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config_loader import ConfigLoader
from src.exchange.bybit_client import BybitClient


def main():
    print("\n" + "=" * 60)
    print("🔍 ПРОВЕРКА HEDGE MODE")
    print("=" * 60)

    try:
        # Load config
        config = ConfigLoader()
        api_key, api_secret = config.get_api_credentials()
        demo = config.is_demo()
        strategy_config = config.get_strategy_config()
        symbol = strategy_config.get('symbol', 'SOLUSDT')
        category = strategy_config.get('category', 'linear')

        print(f"Environment: {'DEMO' if demo else 'PRODUCTION'}")
        print(f"Symbol: {symbol}")
        print(f"Category: {category}")
        print()

        # Initialize client
        client = BybitClient(api_key=api_key, api_secret=api_secret, demo=demo)

        # Check current position mode
        print("📊 Checking current position mode...")

        try:
            # Get position info to check mode
            response = client.session.get_positions(
                category=category,
                symbol=symbol
            )

            if response and 'result' in response:
                positions = response['result'].get('list', [])

                if positions:
                    position = positions[0]
                    position_idx = position.get('positionIdx', 0)

                    print(f"Position Index: {position_idx}")
                    print()

                    if position_idx == 0:
                        print("❌ РЕЖИМ: One-Way Mode (односторонний)")
                        print("   Можно держать только LONG или SHORT")
                        print()
                        print("⚠️  ДЛЯ DUAL-SIDED СТРАТЕГИИ НУЖЕН HEDGE MODE!")
                        print()

                        # Try to switch to hedge mode
                        print("🔄 Пытаюсь включить Hedge Mode...")
                        try:
                            switch_response = client.session.switch_position_mode(
                                category=category,
                                symbol=symbol,
                                mode=3  # 3 = Both Side mode (Hedge)
                            )

                            if switch_response.get('retCode') == 0:
                                print("✅ Hedge Mode успешно включен!")
                                print()
                                print("Теперь можно держать одновременно:")
                                print("  • LONG позиции")
                                print("  • SHORT позиции")
                            else:
                                print(f"❌ Не удалось включить Hedge Mode")
                                print(f"Response: {switch_response}")
                                print()
                                print("РЕШЕНИЕ: Включите вручную на Bybit:")
                                if demo:
                                    print("  1. Зайдите на https://testnet.bybit.com")
                                else:
                                    print("  1. Зайдите на https://www.bybit.com")
                                print(f"  2. Откройте торговлю {symbol}")
                                print("  3. Settings → Position Mode → Hedge Mode")

                        except Exception as e:
                            print(f"⚠️  Ошибка при переключении: {e}")
                            print()
                            print("РЕШЕНИЕ: Включите вручную на Bybit:")
                            if demo:
                                print("  1. Зайдите на https://testnet.bybit.com")
                            else:
                                print("  1. Зайдите на https://www.bybit.com")
                            print(f"  2. Откройте торговлю {symbol}")
                            print("  3. Settings → Position Mode → Hedge Mode")

                    else:
                        print("✅ РЕЖИМ: Hedge Mode (Both Side)")
                        print("   Можно держать одновременно LONG и SHORT")
                        print()
                        print("🎉 Всё настроено правильно!")
                        print("   Бот может открывать двусторонние позиции")
                else:
                    print("ℹ️  Позиций пока нет")
                    print()
                    print("🔄 Пытаюсь проверить настройки аккаунта...")

                    # Try to set hedge mode proactively
                    try:
                        switch_response = client.session.switch_position_mode(
                            category=category,
                            symbol=symbol,
                            mode=3  # 3 = Both Side mode (Hedge)
                        )

                        if switch_response.get('retCode') == 0:
                            print("✅ Hedge Mode успешно включен!")
                        elif switch_response.get('retCode') == 110025:
                            print("✅ Hedge Mode уже включен!")
                        else:
                            print(f"⚠️  Ответ API: {switch_response.get('retMsg', 'Unknown')}")

                    except Exception as e:
                        print(f"ℹ️  Не удалось проверить автоматически: {e}")
                        print()
                        print("Включите Hedge Mode вручную на Bybit:")
                        if demo:
                            print("  1. Зайдите на https://testnet.bybit.com")
                        else:
                            print("  1. Зайдите на https://www.bybit.com")
                        print(f"  2. Откройте торговлю {symbol}")
                        print("  3. Settings → Position Mode → Hedge Mode")
            else:
                print(f"❌ Не удалось получить информацию о позициях")
                print(f"Response: {response}")

        except Exception as e:
            print(f"⚠️  Ошибка при проверке режима: {e}")
            print()
            print("РЕКОМЕНДАЦИЯ:")
            print("Включите Hedge Mode вручную на Bybit:")
            if demo:
                print("  1. Зайдите на https://testnet.bybit.com")
            else:
                print("  1. Зайдите на https://www.bybit.com")
            print(f"  2. Откройте торговлю {symbol}")
            print("  3. Settings → Position Mode → Hedge Mode (Both Side)")
            print()
            print("Это позволит держать одновременно LONG и SHORT позиции")

        print()
        print("=" * 60)
        print()

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

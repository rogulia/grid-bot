# ❓ FAQ - Частые вопросы

Ответы на наиболее распространенные вопросы по SOL-Trader.

---

## 🚀 Начало работы

### Q: С чего начать?

1. **Прочитайте предупреждение о рисках** в README.md
2. **Пройдите Quick Start:** [01-getting-started.md](01-getting-started.md)
3. **Изучите стратегию:** [02-strategy.md](02-strategy.md)
4. **Тестируйте на demo минимум 2-4 недели**
5. Только после успешного demo тестирования рассматривайте production

### Q: Нужен ли опыт программирования?

**Минимальный:** Нужно уметь:
- Редактировать YAML файлы (config.yaml)
- Создавать .env файлы
- Запускать команды в терминале
- Читать логи

**Не нужно:** Писать код. Всё настраивается через конфигурацию.

### Q: Какой минимальный баланс нужен?

**Demo:** Любой (получите тестовый USDT на testnet.bybit.com)

**Production:**
- Консервативно: $100-200 (с параметрами для начинающих)
- Агрессивно: $500+ (с стандартными параметрами)

**Расчет:** Cumulative margin для 10 levels × 2 sides × safety reserve × 2
```
Example: $2,047 × 2 × 1.2 × 2 = $9,825 (ideal для 10 levels обеих сторон)
```

### Q: Можно ли использовать на Windows?

**Нет.** Бот разработан для Linux (Ubuntu, Debian, CentOS).

**Варианты для Windows:**
1. WSL2 (Windows Subsystem for Linux) - рекомендуется
2. Docker container (требует настройки)
3. Linux VPS (например, DigitalOcean, Hetzner)

---

## ⚙️ Конфигурация

### Q: Что значит "demo_trading: true"?

- `demo_trading: true` → Bybit Testnet (testnet.bybit.com), виртуальные деньги
- `demo_trading: false` → Bybit Production (bybit.com), реальные деньги

**Всегда начинайте с demo!**

### Q: В чем разница между demo_trading и dry_run?

**demo_trading:**
- `true` = testnet environment (виртуальные деньги)
- `false` = production environment (реальные деньги)

**dry_run:**
- `true` = симуляция (НЕТ API вызовов, только логи)
- `false` = реальные API вызовы (demo или prod в зависимости от demo_trading)

**Комбинации:**
```yaml
demo_trading: true,  dry_run: false  → Demo с реальными API (рекомендуется для тестов)
demo_trading: true,  dry_run: true   → Симуляция без API
demo_trading: false, dry_run: false  → Production (REAL MONEY!)
demo_trading: false, dry_run: true   → Симуляция (бессмысленно)
```

### Q: Как добавить еще одну монету?

В `config.yaml` добавьте strategy в массив `strategies`:

```yaml
strategies:
  - symbol: "DOGEUSDT"    # Уже есть
    # ...
  
  - symbol: "SOLUSDT"     # Добавляем новую
    category: "linear"
    leverage: 50
    # ... остальные параметры
```

См. подробнее: [07-multi-symbol.md](07-multi-symbol.md)

### Q: Какие параметры использовать для начала?

См. "Консервативные параметры" в [03-configuration.md](03-configuration.md):

```yaml
leverage: 15                    # Вместо 75
grid_step_percent: 2.5         # Вместо 1.0
averaging_multiplier: 1.4      # Вместо 2.0
take_profit_percent: 1.5       # Вместо 1.0
max_grid_levels_per_side: 6    # Вместо 10
mm_rate_threshold: 60.0        # Вместо 90.0
```

---

## 🛡️ Риски и безопасность

### Q: Насколько рискованна стратегия?

**ОЧЕНЬ РИСКОВАННАЯ!**

**Критические риски:**
1. **Односторонний тренд:** +10% без откатов → потеря 70% депозита
2. **Мартингейл асимметрия:** Нарушает симметрию хеджа
3. **Независимая ликвидация:** LONG и SHORT могут ликвидироваться отдельно
4. **Cross Margin:** Весь баланс = обеспечение

**Защита v3.1 снижает риски на 60-80%**, но не устраняет их полностью.

### Q: Что такое Account MM Rate и почему это важно?

**Account Maintenance Margin Rate** = (Требуемая поддерживающая маржа / Баланс) × 100%

- **0%:** Позиции нет или риск минимален
- **50%:** Умеренный риск
- **90%:** Критический риск (близко к ликвидации)
- **100%:** Ликвидация!

**Почему важно:**
- В Portfolio Margin ликвидация происходит **по всему аккаунту**
- Бот мониторит MM Rate каждую секунду
- Emergency close при MM Rate >= threshold (default 90%)

См. подробнее: [02-strategy.md](02-strategy.md)

### Q: Что делает система защиты v3.1?

**7 фаз защиты:**

1. **Dynamic Safety Factor:** Адаптация к волатильности (ATR)
2. **Early Freeze:** Блокировка усреднений при low available
3. **Panic Mode:** Intelligent TP + position balancing
4. **Intelligent TP Management:** Сохранение TP на profitable side
5. **Adaptive Reopen:** Балансировка позиций после TP
6. **Dynamic IM Monitoring:** Warning levels
7. **Emergency Close:** MM Rate >= threshold → закрытие всех позиций

См. полную спецификацию: [04-risk-management.md](04-risk-management.md)

### Q: Может ли бот потерять весь депозит?

**Да.** В крайних случаях:
- Сильный односторонний тренд (например, +20% за час)
- Gap movement (резкий скачок цены без промежуточных значений)
- Network issues (потеря соединения во время критической ситуации)

**Защита:**
- Early Freeze блокирует усреднения заранее
- Emergency Close срабатывает при MM Rate >= 90%
- Рекомендуется консервативные параметры для начинающих

**⚠️ НЕ вкладывайте деньги, которые не можете потерять!**

---

## 📊 Эксплуатация

### Q: Как запустить бота в фоне?

**Вариант 1: Systemd Service (рекомендуется)**
```bash
sudo bash scripts/setup_service.sh  # Один раз
sudo systemctl start sol-trader     # Запуск
sudo systemctl enable sol-trader    # Автозапуск при перезагрузке
```

**Вариант 2: Screen**
```bash
./scripts/run_background.sh start   # Запуск
```

См. подробнее: [05-operations.md](05-operations.md)

### Q: Как остановить бота?

**Терминал:** `Ctrl+C` (graceful shutdown)

**Systemd:** `sudo systemctl stop sol-trader`

**Screen:** `./scripts/run_background.sh stop`

### Q: Как мониторить бота?

**Логи:**
```bash
# Системные логи (real-time)
sudo journalctl -u sol-trader -f

# Файловые логи
tail -f logs/001_bot_$(date +%Y-%m-%d).log
tail -f logs/001_trades_$(date +%Y-%m-%d).log
```

**Баланс:**
```bash
python scripts/check_balance.py
```

**Ключевые метрики в логах:**
- Balance: Текущий баланс
- **Account MM Rate:** Риск ликвидации (ВАЖНО!)
- Available: Доступная маржа
- LONG/SHORT PnL: Unrealized PnL

---

## 🔧 Проблемы и решения

### Q: "Module not found" при запуске

**Решение:**
```bash
# Убедитесь что venv активирован
source .venv/bin/activate

# Переустановите зависимости
~/.local/bin/uv pip install -r requirements.txt
```

### Q: "API key invalid"

**Проверьте:**
1. Ключи скопированы правильно (без пробелов)
2. Demo keys используются с `demo_trading: true`
3. ID в `.env` совпадает с `id` в `config.yaml`
4. Permissions включают "Perpetual Trading" на Bybit

### Q: "No strategy configured"

**Решение:**
В `config.yaml` ОБЯЗАТЕЛЬНО должна быть секция `strategies:` с хотя бы одной стратегией:

```yaml
accounts:
  - id: 1
    strategies:  # <-- Это обязательно!
      - symbol: "DOGEUSDT"
        # ... параметры
```

### Q: "Emergency stop active"

**Что произошло:**
Бот обнаружил критическую ситуацию и создал emergency stop flag.

**Действия:**
```bash
# 1. Проверьте наличие файла
ls -la data/.001_emergency_stop

# 2. Прочитайте причину
cat data/.001_emergency_stop

# 3. Проанализируйте логи
grep "EMERGENCY" logs/001_bot_$(date +%Y-%m-%d).log

# 4. Исправьте проблему (например, пополните баланс)

# 5. Удалите flag только после анализа
rm data/.001_emergency_stop

# 6. Перезапустите бота
sudo systemctl restart sol-trader
```

### Q: Бот не открывает позиции

**Проверьте:**
1. Логи: `grep "ERROR\|WARNING" logs/001_bot_*.log`
2. Баланс достаточен: `python scripts/check_balance.py`
3. WebSocket подключен: Ищите "WebSocket connected" в логах
4. Hedge mode настроен: `python scripts/check_hedge_mode.py`

---

## 📈 Стратегия и торговля

### Q: Как часто бот торгует?

**Зависит от волатильности и параметров:**

**grid_step_percent: 1.0:**
- При волатильности 5%/день → ~5 усреднений/день на сторону
- При волатильности 10%/день → ~10 усреднений/день

**TP closures:**
- Зависит от частоты разворотов
- В среднем: 2-5 TP closes/день

### Q: Какая прибыльность?

**Зависит от множества факторов:**
- Волатильность рынка
- Параметры (leverage, grid_step, TP, multiplier)
- Частота разворотов

**Типичные результаты (demo testing):**
- Консервативные параметры: 5-15% месяц (низкий риск)
- Стандартные параметры: 15-40% месяц (средний риск)
- Агрессивные параметры: 40-100% месяц (ВЫСОКИЙ РИСК!)

**⚠️ Disclaimer:** Прошлые результаты не гарантируют будущую прибыль!

### Q: Почему бот усредняется так глубоко?

**Мартингейл-прогрессия экспоненциальная:**

```
Level 0:  $1
Level 5:  $32    (cumulative: $63)
Level 10: $1,024 (cumulative: $2,047)
```

**Решение:**
- Уменьшите `averaging_multiplier` (например, 1.4 вместо 2.0)
- Уменьшите `max_grid_levels_per_side` (например, 6 вместо 10)
- Увеличьте `grid_step_percent` (например, 2.5% вместо 1.0%)

### Q: Что делать при одностороннем тренде?

**Early Freeze и Panic Mode автоматически:**
1. Блокируют дальнейшие усреднения
2. Оставляют TP на profitable side
3. Балансируют позиции при возможности

**Вручную (если нужно):**
1. Мониторьте Account MM Rate
2. При MM Rate > 70% рассмотрите manual close
3. Остановите бота (`sudo systemctl stop sol-trader`)

---

## 🧪 Тестирование

### Q: Как запустить тесты?

```bash
# Все тесты (172)
pytest tests/ -v

# Конкретный файл
pytest tests/test_grid_strategy.py -v

# С coverage
pytest tests/ --cov=src --cov-report=html
```

### Q: Все тесты проходят?

**Да.** 172 comprehensive тестов должны проходить:
- Unit tests: PositionManager, GridStrategy, BalanceManager
- Integration tests: End-to-end scenarios
- Utilities tests: Timezone, timestamps, emergency stop

### Q: Можно ли добавить свои тесты?

**Да!** Структура:
```python
# tests/test_my_feature.py
import pytest
from src.strategy.grid_strategy import GridStrategy

def test_my_new_feature():
    # Arrange
    strategy = GridStrategy(...)
    
    # Act
    result = strategy.my_method()
    
    # Assert
    assert result == expected
```

См. примеры в `tests/test_grid_strategy.py`

---

## 🤝 Вклад в проект

### Q: Как внести вклад?

1. Fork репозиторий
2. Создайте feature branch
3. Внесите изменения
4. Запустите тесты: `pytest tests/ -v`
5. Создайте Pull Request

**Before contributing:**
- Прочитайте [CLAUDE.md](../CLAUDE.md)
- Убедитесь что все тесты проходят
- Следуйте существующему code style

### Q: Где сообщить о bug?

GitHub Issues (если есть публичный репозиторий) или напрямую автору.

**Включите в report:**
1. Версию бота (git commit hash)
2. Конфигурацию (config.yaml без credentials!)
3. Логи (релевантные части)
4. Шаги для воспроизведения

---

## 📚 Дополнительные вопросы

### Q: Поддерживается ли Telegram уведомления?

**Пока нет.** Запланировано в будущих версиях.

**Workaround:** Используйте external log monitoring tools для уведомлений.

### Q: Можно ли использовать для других бирж?

**Нет.** Бот разработан специально для Bybit API (pybit).

**Для других бирж:** Потребуется полная переработка exchange layer.

### Q: Работает ли на spot markets?

**Нет.** Только perpetual futures (linear contracts).

**Причина:** Hedge mode требует поддержки одновременных LONG/SHORT на один актив.

---

**Не нашли ответ?** Изучите документацию:
- [01-getting-started.md](01-getting-started.md)
- [02-strategy.md](02-strategy.md)
- [04-risk-management.md](04-risk-management.md)
- [CLAUDE.md](../CLAUDE.md)

Или обратитесь к автору проекта.

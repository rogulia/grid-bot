# Testing Guide для SOL-Trader

## Обзор

Проект имеет комплексное тестовое покрытие с **113 тестами**, покрывающими все основные компоненты:

- **Unit тесты** для PositionManager (32 теста, включая liquidation с position_data и fee calculations)
- **Unit тесты** для GridStrategy (32 теста, включая risk limits и sync с биржей)
- **Mock тесты** для BybitClient (24 теста, включая closed PnL и transaction log)
- **Integration тесты** (15 тестов)
- **Timezone тесты** (10 тестов для Helsinki timezone support)

## Быстрый старт

### Установка тестовых зависимостей

```bash
# Активировать venv (если еще не активирован)
source .venv/bin/activate

# Установить зависимости (включая тестовые)
~/.local/bin/uv pip install -r requirements.txt
```

### Запуск всех тестов

```bash
# Запустить все тесты
pytest tests/

# С подробным выводом
pytest tests/ -v

# С coverage отчетом
pytest tests/ --cov=src --cov-report=html
```

### Запуск отдельных тестовых файлов

```bash
# Только тесты PositionManager
pytest tests/test_position_manager.py -v

# Только тесты GridStrategy
pytest tests/test_grid_strategy.py -v

# Только тесты BybitClient
pytest tests/test_bybit_client.py -v

# Только интеграционные тесты
pytest tests/test_integration.py -v
```

### Запуск конкретных тестов

```bash
# Запустить один класс тестов
pytest tests/test_position_manager.py::TestPositionManager -v

# Запустить конкретный тест
pytest tests/test_position_manager.py::TestPositionManager::test_calculate_pnl_long_profit -v
```

## Структура тестов

```
tests/
├── conftest.py                  # Фикстуры и моки для всех тестов
├── test_position_manager.py     # Unit тесты для PositionManager
├── test_grid_strategy.py        # Unit тесты для GridStrategy
├── test_bybit_client.py         # Mock тесты для BybitClient
└── test_integration.py          # Интеграционные тесты
```

## Описание тестовых модулей

### 1. test_position_manager.py (26 тестов)

Тестирует логику управления позициями:

- **Создание и хранение позиций** (LONG/SHORT)
- **Расчет средней цены входа** (weighted average)
- **Расчет PnL** с учетом leverage (100x)
- **Расчет расстояния до ликвидации**
- **Проверка близости к ликвидации**
- **Управление TP order IDs**

Ключевые тесты:
- `test_calculate_pnl_long_profit` - проверка расчета PnL для LONG в профите
- `test_is_near_liquidation_dangerous_long` - проверка risk management
- `test_get_average_entry_price_long` - проверка weighted average

### 2. test_grid_strategy.py (29 тестов)

Тестирует логику grid-стратегии:

- **Инициализация стратегии** с конфигом
- **USD ⇄ Quantity конвертация** с учетом минимумов Bybit
- **Логика усреднения** (when to add positions)
- **Логика Take Profit** (when to close positions)
- **Расчет размера позиций** с multiplier
- **Обновление TP ордеров**
- **Risk limits** и emergency close
- **Синхронизация с биржей**

Ключевые тесты:
- `test_should_add_long_when_price_drops` - trigger логики усреднения
- `test_execute_take_profit_reopens_position` - автопереоткрытие после TP
- `test_update_tp_order_calculates_correct_price_long` - расчет TP price

### 3. test_bybit_client.py (16 тестов)

Mock-тесты для API клиента (без реальных вызовов):

- **Инициализация** (demo/production)
- **Установка leverage**
- **Размещение ордеров** (Market/Limit)
- **TP ордера**
- **Получение позиций**
- **Получение ticker данных**
- **Получение баланса**
- **Отмена ордеров**
- **Закрытие позиций**
- **Error handling**

Все тесты используют `unittest.mock` для имитации API ответов.

### 4. test_integration.py (15 тестов)

Интеграционные тесты взаимодействия компонентов:

- **Полный цикл LONG позиции** (open → average → TP → reopen)
- **Полный цикл SHORT позиции**
- **Одновременная работа LONG + SHORT**
- **Соблюдение max grid levels**
- **Emergency close при risk limits**
- **PnL consistency** через несколько операций
- **Metrics logging**
- **Sync with exchange**
- **Progressive averaging** с multiplier
- **Edge cases** (rapid fluctuations, extreme movements)

## Фикстуры (conftest.py)

Общие фикстуры для всех тестов:

```python
@pytest.fixture
def sample_config():
    """Стандартная конфигурация стратегии"""

@pytest.fixture
def position_manager():
    """Чистый PositionManager (без state persistence)"""

@pytest.fixture
def mock_bybit_client():
    """Mock Bybit client с заглушками для всех методов"""

@pytest.fixture
def mock_metrics_tracker():
    """Mock MetricsTracker"""

@pytest.fixture
def grid_strategy():
    """GridStrategy с моками"""
```

## Тестовое покрытие

Текущее покрытие по компонентам:

- **PositionManager**: ~95% (все основные методы)
- **GridStrategy**: ~90% (основная логика + edge cases)
- **BybitClient**: ~80% (основные методы, mock-тесты)
- **Integration**: Критические сценарии использования

## Важные особенности

### 1. State Persistence отключен в тестах

```python
PositionManager(leverage=100, enable_state_persistence=False)
```

Это предотвращает создание `data/bot_state.json` при тестировании.

### 2. Leverage в тестах

- **Unit тесты**: используют 100x leverage (как в production)
- **Integration тесты**: используют 10x leverage (чтобы избежать risk limits при малых позициях)

### 3. PnL расчеты учитывают leverage

```python
# PnL = quantity * price_change * leverage
# Пример: 0.1 qty * $10 change * 100x = $100 PnL
```

### 4. Все API вызовы замокированы

Тесты не делают реальных вызовов к Bybit API. Используются моки из `unittest.mock`.

## CI/CD Integration

Для интеграции в CI/CD добавьте в pipeline:

```yaml
test:
  script:
    - source .venv/bin/activate
    - pytest tests/ -v --cov=src --cov-report=xml
    - pytest tests/ --junitxml=report.xml
```

## Отладка тестов

### Посмотреть логи во время тестов

```bash
pytest tests/ -v -s
```

### Остановиться на первой ошибке

```bash
pytest tests/ -x
```

### Запустить только failed тесты

```bash
pytest tests/ --lf
```

### Запустить в режиме pdb (debugger)

```bash
pytest tests/ --pdb
```

## Добавление новых тестов

### Шаблон для нового теста

```python
def test_your_new_feature(grid_strategy, position_manager):
    """Test description"""
    # Arrange
    position_manager.add_position('Buy', 100.0, 0.1, 0)

    # Act
    grid_strategy.on_price_update(99.0)

    # Assert
    assert position_manager.get_position_count('Buy') == 2
```

### Использование моков

```python
from unittest.mock import Mock, patch

def test_with_api_mock(mock_bybit_client):
    """Test with mocked API call"""
    mock_bybit_client.place_order.return_value = {
        'orderId': 'test_123'
    }

    # Your test code here
    result = mock_bybit_client.place_order(...)
    assert result['orderId'] == 'test_123'
```

## Best Practices

1. **Используйте фикстуры** вместо создания объектов в каждом тесте
2. **Отключайте state persistence** в тестах
3. **Мокируйте все внешние вызовы** (API, файловая система, network)
4. **Тестируйте edge cases** (liquidation, max exposure, error handling)
5. **Проверяйте assert messages** для лучшей диагностики
6. **Используйте pytest.approx** для float сравнений

## Troubleshooting

### Тесты падают с "Position object has no attribute..."

Убедитесь, что вы используете правильную сигнатуру Position:

```python
Position(
    side='Buy',
    entry_price=100.0,
    quantity=0.1,
    timestamp=datetime.now(),  # Обязательный параметр!
    grid_level=0
)
```

### Mock не работает

Проверьте путь в `@patch`:

```python
@patch('src.exchange.bybit_client.HTTP')  # Правильно
@patch('bybit_client.HTTP')                # Неправильно
```

### PnL расчеты не сходятся

Помните, что PnL умножается на leverage:

```python
# При leverage=100:
pnl = quantity * price_change * 100
```

## Summary

✅ **86 тестов** покрывают все критические компоненты
✅ **Unit тесты** для изолированного тестирования модулей
✅ **Mock тесты** для API клиента без реальных вызовов
✅ **Integration тесты** для проверки взаимодействия компонентов
✅ **Edge cases** для risk management и error handling

Запуск тестов - обязательная часть development процесса перед любыми изменениями!

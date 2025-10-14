# 📊 Руководство по аналитике SOL-Trader

## Обзор

Система аналитики автоматически отслеживает производительность бота и сохраняет метрики в CSV файлы для последующего анализа. Поддерживает мультивалютную торговлю - каждая монета логируется отдельно с префиксом `[SYMBOL]` в логах и колонкой `symbol` в CSV.

## 📁 Файлы данных

### 1. `data/performance_metrics.csv`
Снимки состояния бота каждые 60 секунд для каждой монеты:
- Timestamp (время)
- Symbol (торговая пара: DOGEUSDT, SOLUSDT, etc.)
- Price (текущая цена монеты)
- Long/Short positions (количество позиций)
- Long/Short quantity (объем в монетах)
- Long/Short PnL (нереализованная прибыль/убыток)
- Total PnL (общий PnL для этого символа)
- Total trades (количество сделок)
- Balance (общий баланс аккаунта)

**Пример:**
```csv
timestamp,symbol,price,long_positions,short_positions,long_qty,short_qty,long_pnl,short_pnl,total_pnl,total_trades,balance
2025-10-10 16:00:00,DOGEUSDT,0.25133,1,1,298.0,298.0,0.0,0.0,0.0,2,1000.0
2025-10-10 16:00:00,SOLUSDT,220.50,1,1,0.5,0.5,0.0,0.0,0.0,2,1000.0
```

### 2. `data/trades_history.csv`
История всех сделок для всех монет:
- Timestamp (время сделки)
- Symbol (торговая пара)
- Side (Buy/Sell)
- Action (OPEN/CLOSE)
- Price (цена исполнения)
- Quantity (объем)
- Reason (причина: Grid level X, Take Profit, Emergency close)
- PnL (прибыль/убыток для закрытых позиций)

**Пример:**
```csv
timestamp,symbol,side,action,price,quantity,reason,pnl
2025-10-10 16:00:00,DOGEUSDT,Buy,OPEN,0.25133,298.0,Initial position,
2025-10-10 16:01:00,SOLUSDT,Buy,OPEN,220.50,0.5,Initial position,
2025-10-10 16:05:00,DOGEUSDT,Buy,CLOSE,0.25386,298.0,Take Profit,7.55
```

### 3. `data/summary_report.json`
Итоговый отчет в JSON формате при остановке бота:
```json
{
  "period": { "start": "...", "end": "...", "duration_hours": 24 },
  "performance": {
    "initial_balance": 1000.00,
    "final_balance": 1150.50,
    "total_pnl": 150.50,
    "roi_percent": 15.05,
    "win_rate": 65.5
  },
  "trading_stats": { ... },
  "risk_metrics": { ... }
}
```

### 4. `data/summary_report.txt`
Человеко-читаемый отчет (то же самое, но текстом)

---

## 🔍 Анализ данных

### Быстрый анализ

После остановки бота (Ctrl+C):
1. Откройте `data/summary_report.txt` - готовый отчет
2. Или используйте скрипт для детального анализа

### Использование скрипта analyze.py

#### Базовый анализ:
```bash
python scripts/analyze.py
```

Показывает:
- Summary отчет (если есть)
- Анализ метрик за весь период
- Статистика по ценам и PnL

#### Анализ с графиками:
```bash
# Установить matplotlib (если еще не установлен)
pip install matplotlib

# Запустить с графиками
python scripts/analyze.py --plot
```

Показывает 3 графика:
1. **Price vs Total PnL** - корреляция цены и прибыли
2. **LONG vs SHORT PnL** - производительность каждой стороны
3. **Active Positions** - количество позиций во времени

#### Анализ за определенный период:
```bash
# Последние 24 часа
python scripts/analyze.py --period 24h

# Последние 7 дней
python scripts/analyze.py --period 7d

# Последние 3 часа с графиками
python scripts/analyze.py --period 3h --plot
```

---

## 🔍 Анализ по символам (Multi-Symbol)

### Фильтрация логов по монете:
```bash
# Все логи (все монеты)
sudo journalctl -u sol-trader -f

# Только DOGEUSDT
sudo journalctl -u sol-trader -f | grep "\[DOGEUSDT\]"

# Только SOLUSDT
sudo journalctl -u sol-trader -f | grep "\[SOLUSDT\]"

# Только ошибки для DOGEUSDT
sudo journalctl -u sol-trader -f | grep "\[DOGEUSDT\]" | grep ERROR
```

### Фильтрация CSV данных:
```bash
# Все метрики для DOGEUSDT
cat data/performance_metrics.csv | grep DOGEUSDT

# Все трейды для SOLUSDT
cat data/trades_history.csv | grep SOLUSDT

# Трейды с прибылью для DOGEUSDT
cat data/trades_history.csv | grep DOGEUSDT | grep "Take Profit"
```

### Просмотр состояния всех монет:
```bash
# Красиво отформатированный JSON
cat data/bot_state.json | python3 -m json.tool

# Только DOGEUSDT
cat data/bot_state.json | python3 -m json.tool | grep -A 20 "DOGEUSDT"
```

---

## 📈 Ключевые метрики

### Performance метрики:
- **Total PnL** - общая прибыль/убыток (реализованный + нереализованный)
- **ROI %** - возврат на инвестиции в процентах
- **Win Rate** - процент прибыльных сделок
- **Best/Worst Trade** - лучшая и худшая сделка

### Risk метрики:
- **Max Drawdown** - максимальная просадка (абсолютная и в %)
- **Peak Balance** - пиковый баланс за период

### Trading статистика:
- **Total Trades** - общее количество сделок
- **Winning/Losing Trades** - количество прибыльных/убыточных
- **Average Trade** - средняя прибыль на сделку

---

## 📊 Анализ в Excel/Google Sheets

### Импорт CSV:
1. Откройте Excel/Google Sheets
2. File → Import → CSV
3. Выберите `data/performance_metrics.csv` или `data/trades_history.csv`

### Полезные графики:
- **Line chart**: timestamp vs total_pnl
- **Scatter plot**: price vs long_pnl/short_pnl
- **Bar chart**: winning_trades vs losing_trades

---

## 🎯 Примеры использования

### 1. Проверка после недели работы

```bash
# 1. Остановить бота (если работает)
# Ctrl+C

# 2. Посмотреть summary
cat data/summary_report.txt

# 3. Детальный анализ
python scripts/analyze.py --plot

# 4. Открыть CSV в Excel для детального анализа
# open data/performance_metrics.csv
```

### 2. Мониторинг во время работы

```bash
# Бот работает в одном терминале...

# В другом терминале:
# Посмотреть последние метрики
tail -n 50 data/performance_metrics.csv

# Или анализ текущего состояния
python scripts/analyze.py --period 1h
```

### 3. Сравнение нескольких запусков

```bash
# Сохраните summary_report.json с разными именами
cp data/summary_report.json data/report_week1.json
cp data/summary_report.json data/report_week2.json

# Сравните результаты
diff data/report_week1.json data/report_week2.json
```

---

## 🔧 Продвинутые возможности

### Python pandas анализ:

```python
import pandas as pd
import matplotlib.pyplot as plt

# Загрузить метрики
df = pd.read_csv('data/performance_metrics.csv')
df['timestamp'] = pd.to_datetime(df['timestamp'])

# Анализ
print(f"Средний PnL: ${df['total_pnl'].mean():.2f}")
print(f"Волатильность PnL: ${df['total_pnl'].std():.2f}")
print(f"Sharpe Ratio: {df['total_pnl'].mean() / df['total_pnl'].std():.2f}")

# График
df.plot(x='timestamp', y=['long_pnl', 'short_pnl'], figsize=(12, 6))
plt.title('PnL Over Time')
plt.show()
```

---

## ⚠️ Важные замечания

1. **CSV файлы создаются автоматически** при первом запуске бота
2. **Summary report генерируется при остановке** бота (Ctrl+C)
3. **Метрики сохраняются каждые 60 секунд** во время работы
4. **Графики требуют matplotlib** - установите через `pip install matplotlib`
5. **Данные хранятся в `data/`** - эта папка в .gitignore

---

## 📚 Дополнительная информация

- См. `PLAN.md` - полный план проекта
- См. `README.md` - общая документация
- См. `logs/` - текстовые логи бота

---

## 💡 Tips & Tricks

**Автоматический анализ после запуска:**
```bash
# Запустить бота, дождаться окончания, автоанализ
python src/main.py && python scripts/analyze.py --plot
```

**Экспорт отчета:**
```bash
# Сохранить анализ в файл
python scripts/analyze.py > my_analysis.txt
```

**Быстрая статистика:**
```bash
# Последняя строка метрик = текущее состояние
tail -n 1 data/performance_metrics.csv
```

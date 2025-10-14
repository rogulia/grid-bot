# 🔄 Руководство по круглосуточной работе бота

Два способа запустить бота в фоне:
1. **systemd service** (рекомендуется) - автозапуск при перезагрузке
2. **screen** (проще) - работает только пока сервер включен

---

## ⚡ БЫСТРЫЙ СТАРТ (если уже установили сервис)

Если вы уже установили systemd service (запускали `sudo bash scripts/setup_service.sh`), то управление ботом очень простое:

### Запустить бота:
```bash
sudo systemctl start sol-trader
```

**ВСЁ!** Бот запущен! 🎉

### Проверить что бот работает:
```bash
sudo systemctl status sol-trader
```

Должны увидеть:
- **Active: active (running)** зелёным цветом ✅
- Если видите **failed (красным)** - есть проблема ❌

### Смотреть логи в реальном времени:
```bash
sudo journalctl -u sol-trader -f
```

Для выхода нажмите `Ctrl+C`

### Остановить бота:
```bash
sudo systemctl stop sol-trader
```

### Перезапустить бота (например, после изменения config.yaml):
```bash
sudo systemctl restart sol-trader
```

**Готово!** Это всё что нужно для ежедневной работы! 🚀

---

## 📝 Если ещё НЕ установили сервис

Если это первый раз, читайте инструкцию ниже по установке.

---

## 🎯 Способ 1: systemd service (РЕКОМЕНДУЕТСЯ)

### ✅ Преимущества:
- ✅ Автоматический запуск при загрузке сервера
- ✅ Автоматический перезапуск при падении
- ✅ Логирование через journalctl
- ✅ Управление как системным сервисом
- ✅ Работает даже после закрытия терминала

### 📋 Установка (один раз):

```bash
# 1. Дать права на выполнение
chmod +x scripts/setup_service.sh
chmod +x scripts/bot_control.sh

# 2. Установить сервис (требует sudo)
sudo bash scripts/setup_service.sh
```

### 🚀 Управление ботом:

```bash
# Запустить бота
./scripts/bot_control.sh start

# Остановить бота
./scripts/bot_control.sh stop

# Перезапустить бота
./scripts/bot_control.sh restart

# Проверить статус
./scripts/bot_control.sh status

# Смотреть логи в реальном времени
./scripts/bot_control.sh logs

# Смотреть логи из файла
./scripts/bot_control.sh logs-bot
```

### ⚙️ Автозапуск:

```bash
# Включить автозапуск при загрузке (уже включен после установки)
./scripts/bot_control.sh enable

# Отключить автозапуск
./scripts/bot_control.sh disable
```

### 🔍 Проверка автозапуска:

```bash
# Проверить статус автозапуска
sudo systemctl is-enabled sol-trader

# Если вернет "enabled" - автозапуск включен
```

### 📊 Логи:

```bash
# Системные логи (через journalctl)
sudo journalctl -u sol-trader -f

# Или логи из файлов проекта
tail -f logs/bot_*.log
```

---

## 🖥️ Способ 2: screen (ПРОЩЕ, НО БЕЗ АВТОЗАПУСКА)

### ✅ Преимущества:
- ✅ Не требует sudo
- ✅ Простая установка
- ✅ Можно подключиться и посмотреть вывод

### ❌ Недостатки:
- ❌ НЕТ автозапуска при перезагрузке сервера
- ❌ Останавливается при выключении сервера

### 📋 Установка screen (один раз):

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install screen

# CentOS/RHEL
sudo yum install screen
```

### 🚀 Управление ботом:

```bash
# Дать права на выполнение (один раз)
chmod +x scripts/run_background.sh

# Запустить бота в фоне
./scripts/run_background.sh start

# Подключиться к боту (посмотреть вывод)
./scripts/run_background.sh attach

# Отключиться БЕЗ остановки бота
# (когда подключены): Ctrl+A, затем D

# Остановить бота
./scripts/run_background.sh stop

# Проверить статус
./scripts/run_background.sh status

# Смотреть логи
./scripts/run_background.sh logs
```

### ⚠️ Важно:
- При перезагрузке сервера бот НЕ запустится автоматически
- Нужно будет запустить вручную: `./scripts/run_background.sh start`

---

## 📋 Сравнение методов

| Функция                          | systemd | screen |
|----------------------------------|---------|--------|
| Работает в фоне                  | ✅       | ✅      |
| Автозапуск при перезагрузке      | ✅       | ❌      |
| Автоперезапуск при падении       | ✅       | ❌      |
| Требует sudo                     | ✅       | ❌      |
| Можно подключиться и посмотреть  | ❌       | ✅      |
| Системные логи                   | ✅       | ❌      |

---

## 🎯 Рекомендации

### Для production (реальные деньги):
✅ Используйте **systemd service**
- Надежнее
- Автоматический перезапуск
- Автозапуск при перезагрузке

### Для тестирования:
✅ Используйте **screen**
- Проще настроить
- Не требует sudo
- Можно посмотреть вывод

---

## 🔧 Устранение неполадок

### systemd service

**Проблема: Бот не запускается**
```bash
# Проверить статус
sudo systemctl status sol-trader

# Посмотреть полные логи
sudo journalctl -u sol-trader -n 100

# Проверить что venv существует
ls -la /home/iuriirogulia/projects/sol-trader/.venv/bin/python

# Проверить что main.py существует
ls -la /home/iuriirogulia/projects/sol-trader/src/main.py
```

**Проблема: Бот падает сразу после запуска**
```bash
# Посмотреть ошибки
sudo journalctl -u sol-trader -n 50

# Попробовать запустить вручную
cd /home/iuriirogulia/projects/sol-trader
source .venv/bin/activate
python src/main.py
```

**Проблема: Изменил конфигурацию, нужно перезапустить**
```bash
./scripts/bot_control.sh restart
```

### screen

**Проблема: screen не установлен**
```bash
sudo apt-get install screen
```

**Проблема: Не могу подключиться к screen**
```bash
# Проверить что бот запущен
./scripts/run_background.sh status

# Посмотреть все screen сессии
screen -list
```

**Проблема: Бот остановился после закрытия терминала**
```bash
# Убедитесь что вы отключились правильно (Ctrl+A, D)
# А не закрыли терминал с открытым screen
```

---

## 📝 Примеры использования

### Пример 1: Первый запуск с systemd

```bash
# 1. Убедиться что бот работает вручную
source .venv/bin/activate
python src/main.py
# Проверить что всё ок, остановить Ctrl+C

# 2. Установить как сервис
sudo bash scripts/setup_service.sh

# 3. Запустить
./scripts/bot_control.sh start

# 4. Проверить логи
./scripts/bot_control.sh logs

# 5. Закрыть терминал - бот продолжит работать!

# 6. При следующем входе проверить статус
./scripts/bot_control.sh status
```

### Пример 2: Первый запуск с screen

```bash
# 1. Убедиться что screen установлен
which screen

# 2. Запустить бота
./scripts/run_background.sh start

# 3. Подключиться и посмотреть
./scripts/run_background.sh attach

# 4. Отключиться НЕ останавливая
# Нажать: Ctrl+A, затем D

# 5. Закрыть терминал - бот продолжит работать!

# 6. При следующем входе подключиться снова
./scripts/run_background.sh attach
```

### Пример 3: Обновление конфигурации

```bash
# 1. Остановить бота
./scripts/bot_control.sh stop

# 2. Изменить config/config.yaml
nano config/config.yaml

# 3. Запустить снова
./scripts/bot_control.sh start

# 4. Проверить что изменения применились
./scripts/bot_control.sh logs
```

### Пример 4: Анализ после недели работы

```bash
# 1. Остановить бота
./scripts/bot_control.sh stop

# 2. Подождать пока сгенерируется summary report
sleep 5

# 3. Посмотреть отчет
cat data/summary_report.txt

# 4. Построить графики
source .venv/bin/activate
python scripts/analyze.py --plot

# 5. Если всё хорошо - запустить снова
./scripts/bot_control.sh start
```

---

## ⚠️ Важные предупреждения

### systemd:
1. **НЕ** редактируйте `/etc/systemd/system/sol-trader.service` напрямую
   - Всегда редактируйте `sol-trader.service` в проекте
   - Затем запускайте `sudo bash scripts/setup_service.sh`

2. После изменения service файла:
   ```bash
   sudo bash scripts/setup_service.sh
   ./scripts/bot_control.sh restart
   ```

3. Для просмотра логов используйте:
   ```bash
   ./scripts/bot_control.sh logs     # systemd logs
   ./scripts/bot_control.sh logs-bot # файловые логи
   ```

### screen:
1. **НЕ** закрывайте терминал если подключены к screen
   - Сначала отключитесь: Ctrl+A, D
   - Только потом закрывайте терминал

2. При перезагрузке сервера:
   - Бот НЕ запустится автоматически
   - Нужно запустить вручную

3. Если сервер выключится - бот остановится

---

## 🎉 Готово!

Теперь ваш бот может работать круглосуточно!

**Рекомендация:**
1. Сначала протестируйте несколько часов
2. Проверьте что всё работает стабильно
3. Только потом оставляйте на недели

**Не забывайте:**
- Периодически проверять логи
- Проверять баланс
- Анализировать результаты

Удачной торговли! 🚀

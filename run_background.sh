#!/bin/bash
# Запуск парсера в фоне с логированием

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Создаём директорию логов
mkdir -p logs

LOG_FILE="logs/parser_$(date +%Y%m%d_%H%M%S).log"
PID_FILE="parser.pid"

echo "======================================"
echo "🚀 AniLiberty Parser запущен"
echo "📝 Log: $LOG_FILE"
echo "======================================"

# Запускаем в фоне
nohup python3 aniliberty_fast.py > "$LOG_FILE" 2>&1 &
PID=$!

echo $PID > "$PID_FILE"
echo "✓ PID: $PID"

# Показываем прогресс
sleep 2
if tail -f "$LOG_FILE" &
  TAIL_PID=$!
  
  # Ждём завершения парсера
  wait $PID
  EXIT_CODE=$?
  
  # Убиваем tail
  kill $TAIL_PID 2>/dev/null || true
  
  if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "✅ Парсер завершился успешно"
    echo "📁 Результаты в: mirrors/"
    echo "🎬 Главный плейлист: aniliberty.m3u8"
  else
    echo ""
    echo "❌ Ошибка парсера (код $EXIT_CODE)"
    echo "📋 Смотрите: $LOG_FILE"
  fi
  
  rm -f "$PID_FILE"
fi

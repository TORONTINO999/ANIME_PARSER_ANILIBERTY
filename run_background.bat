@echo off
REM Запуск парсера в фоне на Windows

setlocal enabledelayedexpansion

REM Создаём папку логов
if not exist logs mkdir logs

REM Генерируем имя лога с датой/временем
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do (set mydate=%%c%%a%%b)
for /f "tokens=1-2 delims=/:" %%a in ('time /t') do (set mytime=%%a%%b)
set LOG_FILE=logs\parser_%mydate%_%mytime%.log

echo ======================================
echo. AniLiberty Parser запущен
echo. Log: %LOG_FILE%
echo ======================================
echo.

REM Запускаем в фоне
start /B python aniliberty_fast.py > "%LOG_FILE%" 2>&1

REM Ждём немного
timeout /t 2 /nobreak

REM Показываем начало лога
echo Отслеживание логов (закройте окно чтобы остановить)...
echo.
type "%LOG_FILE%"

REM Ждём завершения парсера
:wait_loop
if exist parser.pid (
    timeout /t 5 /nobreak
    goto wait_loop
)

echo.
echo ======================================
echo. Парсер завершился
echo. Результаты в: mirrors\
echo. Главный плейлист: aniliberty.m3u8
echo ======================================
pause

@echo off
rem รันบอท 1 รอบ (Task Scheduler เรียกไฟล์นี้ทุกชั่วโมง)
cd /d "%~dp0"
python gold_breakout_bot.py >> bot_console.log 2>&1

@echo off
REM Mở app desktop Domain Setup — double-click file này là chạy.
REM Không cần mở web, không cần đóng Chrome.
cd /d "%~dp0"
python app.py
if errorlevel 1 (
  echo.
  echo Loi: khong mo duoc app. Kiem tra Python: python --version
  echo Neu chua cai Python, cai o python.org roi chay lai file nay.
  pause
)

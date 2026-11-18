@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Dang cap nhat tool tu git...
git pull
echo.
echo ===== 3 commit moi nhat (dong dau tien phai la ban moi nhat) =====
git log --oneline -3
echo.
echo Neu thay loi o tren, chup man hinh gui lai.
pause

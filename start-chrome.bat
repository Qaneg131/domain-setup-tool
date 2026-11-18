@echo off
REM Mở Chrome với cổng remote-debugging 9222, dùng đúng profile thật của bạn
REM (đã đăng nhập sẵn Cloudflare + Namecheap).
REM Cách dùng: ĐÓNG hết cửa sổ Chrome đang mở, rồi double-click file này.
REM Sau đó chạy tool (web hoặc chrome_setup.py) — tool sẽ nối vào Chrome này,
REM không cần đóng/mở lại nữa. Để Chrome mở đó trong lúc tool chạy.
REM
REM Nếu Chrome của bạn cài ở chỗ khác, sửa đường dẫn chrome.exe bên dưới.
set CHROME="C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist %CHROME% set CHROME="C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not exist %CHROME% set CHROME="%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
echo Dang mo Chrome (debug port 9222) bang profile that cua ban...
start "" %CHROME% --remote-debugging-port=9222 --no-first-run
echo Xong. De Chrome mo do, roi chay:  python web_app.py
pause

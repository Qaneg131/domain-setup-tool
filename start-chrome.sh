#!/bin/bash
# Mở Chrome với cổng remote-debugging 9222, dùng đúng profile thật của bạn
# (đã đăng nhập sẵn Cloudflare + Namecheap).
# Cách dùng: ĐÓNG hết cửa sổ Chrome đang mở, rồi chạy:  bash start-chrome.sh
# Sau đó chạy tool (web hoặc chrome_setup.py) — tool sẽ nối vào Chrome này.
# Để Chrome mở đó trong lúc tool chạy.
if [[ "$OSTYPE" == "darwin"* ]]; then
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    --remote-debugging-port=9222 --no-first-run &
else
  google-chrome --remote-debugging-port=9222 --no-first-run &
fi
echo "Chrome da mo (debug port 9222). De Chrome mo do, roi chay:  python3 web_app.py"

#!/bin/bash
# Double-click to start the stock screener and open it in your browser.
cd "$(dirname "$0")" || exit 1
URL="http://localhost:8765"

# Already running? Just open the page.
if curl -s -o /dev/null "$URL/api/status"; then
  open "$URL"
  exit 0
fi

if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up (this takes a minute)..."
  python3 -m venv .venv || { echo "Python 3 is required."; read -r; exit 1; }
fi
if ! .venv/bin/python -c "import yfinance" 2>/dev/null; then
  .venv/bin/pip install --quiet -r requirements.txt || { echo "Install failed."; read -r; exit 1; }
fi

(sleep 1.5; open "$URL") &
echo "Close this window (or press Ctrl+C) to stop the screener."
exec .venv/bin/python server.py

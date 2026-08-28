#!/usr/bin/env bash
# 趋势全景 · 股票分析 启动脚本 (Mac / Linux)
cd "$(dirname "$0")"

echo "============================================"
echo "  趋势全景 · 股票分析  正在启动..."
echo "  启动后请访问: http://127.0.0.1:5000"
echo "  按 Ctrl+C 停止"
echo "============================================"

# 检查 Python
if ! command -v python3 >/dev/null 2>&1; then
    echo "[错误] 未检测到 Python3，请先安装 Python 3.10+"
    exit 1
fi

# 首次运行安装依赖
python3 -c "import flask, requests, pandas" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "[首次运行] 正在安装依赖..."
    python3 -m pip install -r requirements.txt
fi

# 自动打开浏览器
( sleep 2; python3 -c "import webbrowser; webbrowser.open('http://127.0.0.1:5000')" ) &

python3 app.py

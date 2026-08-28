#!/usr/bin/env bash
# 趋势全景 · 股票分析 —— 云端一键部署脚本（Ubuntu 22.04）
# 用法: 上传本 zip 到服务器后解压, 在项目目录执行:  sudo bash deploy/install.sh
set -e

echo "=========================================="
echo "  趋势全景 云端部署"
echo "=========================================="

# 1. 时区设为北京时间（定时扫描 15:35 依赖）
echo "[1/5] 设置时区 Asia/Shanghai ..."
timedatectl set-timezone Asia/Shanghai 2>/dev/null || ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime

# 2. 安装 Python3 + pip
echo "[2/5] 检查 Python3 / pip ..."
if ! command -v python3 >/dev/null; then
  apt-get update -y && apt-get install -y python3 python3-pip
fi
if ! command -v pip3 >/dev/null; then
  apt-get update -y && apt-get install -y python3-pip
fi
python3 --version

# 3. 安装依赖（用清华镜像加速）
echo "[3/5] 安装依赖（清华镜像） ..."
pip3 install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 4. 安装 systemd 常驻服务（开机自启 + 崩溃自动重启）
echo "[4/5] 安装 systemd 服务 ..."
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
sed "s|__APP_DIR__|$APP_DIR|g" deploy/trend-stock.service > /etc/systemd/system/trend-stock.service
systemctl daemon-reload
systemctl enable trend-stock
systemctl restart trend-stock

# 5. 放行防火墙（云服务器还需在控制台安全组放行 5000）
echo "[5/5] 防火墙放行 5000 ..."
if command -v ufw >/dev/null; then
  ufw allow 5000/tcp 2>/dev/null || true
fi

sleep 2
PUB_IP=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || echo "你的服务器公网IP")
echo ""
echo "=========================================="
echo "  部署完成！"
echo "  手机/电脑浏览器访问:  http://$PUB_IP:5000"
echo "  查看服务状态:          systemctl status trend-stock"
echo "  查看运行日志:          journalctl -u trend-stock -f"
echo "  （首次打开会实时拉取行情, 稍等几秒）"
echo "=========================================="

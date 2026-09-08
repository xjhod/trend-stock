# -*- coding: utf-8 -*-
"""重启辅助进程（独立运行，不受旧服务退出影响）。

在线更新完成后的自动重启流程：
  旧服务 -> 写标记并启动本脚本 -> 旧服务退出
  本脚本等待 5000 端口释放 -> 清理 PID 锁 -> 拉起新服务 -> 退出

用独立进程重启，避免"新进程启动慢/端口竞争"导致重启失败。
"""
import os
import socket
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = 5000


def _wait_port_released(timeout=30):
    """等待旧实例释放 5000 端口（最多 timeout 秒）"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            s = socket.create_connection((HOST, PORT), timeout=0.5)
            s.close()
        except OSError:
            return True  # 连不上 = 端口已释放
        time.sleep(0.5)
    return False


def main():
    # 1) 等待端口释放（旧实例退出）
    released = _wait_port_released()
    # 2) 清理 PID 锁，避免新实例误判"已在运行"
    try:
        lock = os.path.join(BASE_DIR, ".app.pid")
        if os.path.exists(lock):
            os.remove(lock)
    except Exception:
        pass
    # 3) 拉起新实例（新控制台窗口，与双击启动体验一致）
    try:
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NEW_CONSOLE
        subprocess.Popen([sys.executable, "app.py"], cwd=BASE_DIR,
                         creationflags=flags, close_fds=True)
        # 4) 给新实例几秒启动时间，若端口起来则说明成功
        time.sleep(4)
        try:
            s = socket.create_connection((HOST, PORT), timeout=1)
            s.close()
            print("[restart] 新实例已启动: http://127.0.0.1:5000")
        except OSError:
            print("[restart] 新实例启动较慢或未就绪（如端口未开，请手动双击 start.bat）")
    except Exception as e:  # noqa
        print("[restart] 启动新实例失败: %s（请手动双击 start.bat）" % e)
        if not released:
            print("[restart] 注意：旧端口可能仍被占用，请先运行 stop.bat")


if __name__ == "__main__":
    main()

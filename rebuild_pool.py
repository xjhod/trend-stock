# -*- coding: utf-8 -*-
"""重建高适配池脚本（后台运行，避免HTTP超时）"""
import sys
import time
sys.path.insert(0, '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis')
import pool_manager

def progress(stage, msg):
    print(f"[{time.strftime('%H:%M:%S')}] {stage}: {msg}", flush=True)

print(f"[{time.strftime('%H:%M:%S')}] 开始重建高适配池（30亿门槛）...", flush=True)
try:
    result = pool_manager.build_pool(30, progress=progress)
    pool_manager.set_threshold(30)
    print(f"[{time.strftime('%H:%M:%S')}] 重建完成!", flush=True)
    print(f"结果: {result}", flush=True)
except Exception as e:
    print(f"[{time.strftime('%H:%M:%S')}] 重建失败: {e}", flush=True)
    import traceback
    traceback.print_exc()

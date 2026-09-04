import scan_daily, time
t0 = time.time()
r = scan_daily.run_scan(workers=4)
el = time.time() - t0
print(f"全量扫描耗时: {el:.1f}秒 ({el/60:.1f}分钟), 信号 {len(r.get('signals',[]))} 只, env.mode={r.get('env',{}).get('mode')}, 建议仓位={r.get('env',{}).get('pos_pct')}")

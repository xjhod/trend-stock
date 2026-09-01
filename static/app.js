/* ===== 趋势全景 · 前端逻辑 ===== */
(function () {
  "use strict";

  let currentCode = null;
  let currentPeriod = "daily";
  let klineCache = {};          // code -> {daily:[], weekly:[], monthly:[]}
  let klineCacheOrder = [];     // LRU顺序，最新访问的在末尾
  const CACHE_MAX = 10;         // 最多缓存10只股票，防内存泄漏
  let stockData = null;         // 当前股票的完整分析数据
  let charts = {};
  let stockFetchCtrl = null;    // 当前股票请求的AbortController，切换时取消旧请求

  // ---------- 工具 ----------
  function fmt(v, digits) {
    if (v === null || v === undefined || isNaN(v)) return "--";
    return Number(v).toFixed(digits === undefined ? 2 : digits);
  }
  // fetch 带超时：避免数据源卡死导致页面无限转圈
  function fetchTimeout(url, opts, ms) {
    const ctrl = new AbortController();
    const timer = setTimeout(function () { ctrl.abort(); }, ms || 12000);
    return fetch(url, Object.assign({}, opts, { signal: ctrl.signal }))
      .finally(function () { clearTimeout(timer); });
  }
  function fmtWan(v) {   // 元 -> 万/亿 字符串
    if (v === null || v === undefined || isNaN(v)) return "--";
    if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(2) + "亿";
    if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(1) + "万";
    return fmt(v);
  }
  function cls(v) { return v > 0 ? "up" : v < 0 ? "down" : "flat"; }
  function sign(v) { return v > 0 ? "+" : ""; }

  // ---------- 自选股 ----------
  async function loadWatchlist() {
    try {
      const res = await fetch("/api/watchlist");
      const data = await res.json();
      renderWatchlist(data.items || []);
    } catch (e) {
      showStatus("自选股加载失败");
    }
  }

  function renderWatchlist(items) {
    const ul = document.getElementById("watchlist");
    ul.innerHTML = "";
    if (!items.length) {
      ul.innerHTML = '<li class="flat" style="color:var(--text-dim);font-size:13px;padding:20px 16px">暂无自选股，请在顶部添加</li>';
      return;
    }
    items.forEach(q => {
      const li = document.createElement("li");
      if (q.code === currentCode) li.className = "active";
      li.innerHTML =
        '<div class="wl-main">' +
          '<div class="wl-name">' + q.name + '</div>' +
          '<div class="wl-code">' + q.code + '</div>' +
        '</div>' +
        '<div class="wl-price">' +
          '<div class="p ' + cls(q.pct_chg) + '">' + fmt(q.price) + '</div>' +
          '<div class="c ' + cls(q.pct_chg) + '">' + sign(q.pct_chg) + fmt(q.pct_chg) + '%</div>' +
        '</div>' +
        '<button class="wl-del" data-code="' + q.code + '" title="删除">×</button>';
      li.addEventListener("click", () => selectStock(q.code));
      ul.appendChild(li);
    });
    // 删除按钮事件
    ul.querySelectorAll(".wl-del").forEach(btn => {
      btn.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        const code = btn.dataset.code;
        await fetch("/api/watchlist/" + code, { method: "DELETE" });
        if (currentCode === code) {
          currentCode = null;
          document.getElementById("stock-view").style.display = "none";
          document.getElementById("empty-state").style.display = "flex";
        }
        loadWatchlist();
      });
    });
  }

  // ---------- 添加 / 搜索 ----------
  const addInput = document.getElementById("add-input");
  const searchResult = document.getElementById("search-result");
  let searchTimer = null;

  addInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    const kw = addInput.value.trim();
    if (!kw) { searchResult.classList.remove("open"); searchResult.innerHTML = ""; return; }
    searchTimer = setTimeout(async () => {
      const res = await fetch("/api/search?q=" + encodeURIComponent(kw));
      const data = await res.json();
      const items = data.items || [];
      if (items.length) {
        searchResult.innerHTML = "";
        items.slice(0, 8).forEach(it => {
          const div = document.createElement("div");
          div.className = "sr-item";
          div.innerHTML = '<span>' + it.name + '</span><span class="sr-code">' + it.code + '</span>';
          div.addEventListener("click", () => { addStock(it.code); addInput.value = ""; });
          searchResult.appendChild(div);
        });
        searchResult.classList.add("open");
      } else {
        searchResult.innerHTML = "";
        searchResult.classList.remove("open");
      }
    }, 300);
  });

  addInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); addStock(addInput.value.trim()); }
  });

  document.getElementById("add-btn").addEventListener("click", () => {
    addStock(addInput.value.trim());
  });

  // ---------- 批量导入自选股（同花顺导出） ----------
  window.__openImport = function () {
    document.getElementById("import-text").value = "";
    document.getElementById("import-modal").style.display = "flex";
  };
  window.__closeImport = function () {
    document.getElementById("import-modal").style.display = "none";
  };
  window.__doImport = async function () {
    const text = document.getElementById("import-text").value;
    if (!text.trim()) { showStatus("请先粘贴自选股文本"); return; }
    showStatus("正在导入自选股…");
    const res = await fetch("/api/watchlist/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text })
    });
    const data = await res.json();
    if (data.ok) {
      document.getElementById("import-modal").style.display = "none";
      showStatus("导入成功：识别到 " + data.added.length + " 只，自选股共 " + data.watchlist.length + " 只");
      loadWatchlist();
    } else {
      showStatus(data.msg || "导入失败");
    }
  };
  document.getElementById("import-btn").addEventListener("click", window.__openImport);
  // 点击遮罩关闭
  document.getElementById("import-modal").addEventListener("click", (e) => {
    if (e.target.id === "import-modal") window.__closeImport();
  });

  async function addStock(kw) {
    if (!kw) return;
    const res = await fetch("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: kw })
    });
    const data = await res.json();
    addInput.value = "";
    searchResult.classList.remove("open");
    if (data.ok) {
      loadWatchlist();
      if (data.code) selectStock(data.code);
    } else {
      showStatus(data.msg || "添加失败");
    }
  }

  // ---------- 选择股票 ----------
  async function selectStock(code, isRetry) {
    // 取消旧请求，避免快速切换时旧请求返回覆盖新数据
    if (stockFetchCtrl) { try { stockFetchCtrl.abort(); } catch (e) {} }
    stockFetchCtrl = new AbortController();
    const myCtrl = stockFetchCtrl;

    currentCode = code;
    // 高亮
    document.querySelectorAll("#watchlist li, #hf-list li").forEach(li => li.classList.remove("active"));
    document.getElementById("empty-state").style.display = "none";
    document.getElementById("stock-view").style.display = "block";
    document.getElementById("stock-view").scrollTop = 0;

    // 加载
    document.getElementById("quote-bar").innerHTML =
      '<div class="flat" style="padding:10px">加载中…</div>' +
      '<div class="qb-grid" style="margin-left:auto">' +
        '<button class="reload-btn" onclick="window.__reloadStock()">↻ 重试</button>' +
      '</div>';
    showStatus(isRetry ? "首次加载失败，正在自动重试…" : "加载 " + code + " 数据中…");
    try {
      const res = await fetch("/api/stock/" + code, { signal: myCtrl.signal });
      if (myCtrl.signal.aborted) return;  // 已被新请求取消，忽略
      const data = await res.json();
      if (myCtrl.signal.aborted) return;
      if (!data.ok) throw new Error(data.msg || "加载失败");
      stockData = data;
      // LRU缓存：更新访问顺序，超出上限删除最旧的
      const idx = klineCacheOrder.indexOf(code);
      if (idx >= 0) klineCacheOrder.splice(idx, 1);
      klineCacheOrder.push(code);
      while (klineCacheOrder.length > CACHE_MAX) {
        const old = klineCacheOrder.shift();
        delete klineCache[old];
      }
      klineCache[code] = {
        daily: data.kline || [],
        weekly: data.kline_weekly || [],
        monthly: data.kline_monthly || []
      };
      renderStock(data);
      showStatus("数据更新于 " + new Date().toLocaleTimeString());
    } catch (e) {
      if (myCtrl.signal.aborted) return;  // 被取消的请求，不显示错误
      if (!isRetry) {
        // 数据源抖动，自动重试一次
        setTimeout(function () { selectStock(code, true); }, 800);
      } else {
        const timeout = e && e.name === "AbortError";
        showStatus(timeout ? "加载超时（12秒）" : "加载失败：" + e.message);
        document.getElementById("quote-bar").innerHTML =
          '<div class="flat" style="padding:10px;color:var(--up)">' +
          (timeout ? "加载超时：数据源响应过慢，请检查网络后重试" : "加载失败（网络或数据源抖动），请检查网络后点重试") +
          '</div>' +
          '<div class="qb-grid" style="margin-left:auto">' +
            '<button class="reload-btn" onclick="window.__reloadStock()">↻ 重试</button>' +
          '</div>';
      }
    }
  }
  window.__reloadStock = function () { if (currentCode) selectStock(currentCode); };

  // ---------- 渲染 ----------
  function renderStock(d) {
    renderQuoteBar(d);
    renderTrends(d);
    renderLayers(d);
    renderTech(d);
    renderConclusion(d);
    renderTechSummary(d);
    renderKline();
    renderFundChart(d);
    renderFundamentalChart(d);
  }

  function renderQuoteBar(d) {
    const q = d.quote || {};
    const el = document.getElementById("quote-bar");
    const c = cls(q.pct_chg);
    const chgSign = sign(q.pct_chg);
    el.innerHTML =
      '<div>' +
        '<div class="qb-name">' + (q.name || d.code) + '</div>' +
        '<div class="qb-code">' + d.code + '</div>' +
      '</div>' +
      '<div class="qb-price">' +
        '<div class="big ' + c + '">' + fmt(q.price) + '</div>' +
        '<div class="chg ' + c + '">' + chgSign + fmt(q.change) + '  ' + chgSign + fmt(q.pct_chg) + '%</div>' +
      '</div>' +
      '<div class="qb-grid">' +
        cell("今开", fmt(q.open)) +
        cell("昨收", fmt(q.pre_close)) +
        cell("最高", fmt(q.high)) +
        cell("最低", fmt(q.low)) +
        cell("成交量", fmtWan(q.volume) + "手") +
        cell("成交额", fmtWan(q.amount)) +
        cell("换手率", fmt(q.turnover) + "%") +
        cell("市盈率", fmt(q.pe)) +
        cell("市净率", fmt(q.pb)) +
        cell("总市值", fmtWan(q.total_mv)) +
      '</div>' +
      '<button class="reload-btn" onclick="window.__reloadStock()" title="重新加载数据">↻</button>';
  }

  function cell(k, v) {
    return '<div class="qb-cell"><div class="k">' + k + '</div><div class="v">' + v + '</div></div>';
  }

  function renderLayers(d) {
    const el = document.getElementById("layers-row");
    if (!el) return;
    const L = d.layers || {};
    const dmap = { up: "↑ 上升", down: "↓ 下降", side: "→ 震荡", unknown: "— 不明" };
    const clsMap = { up: "up", down: "down", side: "flat", unknown: "flat" };
    const m = L.market || {}, i = L.industry || {}, s = L.stock || {};
    const tlMap = { up: "↑ 上升趋势线", down: "↓ 下降趋势线", none: "— 无趋势线" };
    let html = '<div class="layer-line">';
    html += '<div class="layer-item"><span class="lbl">大盘</span><span class="val ' + clsMap[m.direction] + '">' + (dmap[m.direction] || "—") + '</span></div>';
    html += '<div class="layer-arrow">→</div>';
    html += '<div class="layer-item"><span class="lbl">行业·' + (i.name || "—") + '</span><span class="val ' + clsMap[i.direction] + '">' + (dmap[i.direction] || "—") + '</span></div>';
    html += '<div class="layer-arrow">→</div>';
    html += '<div class="layer-item"><span class="lbl">个股</span><span class="val">' + (tlMap[s.trendline] || "—") + '</span></div>';
    if (L.resonance_bull) html += '<div class="layer-reso reso-bull">✦ 看涨共振</div>';
    else if (L.resonance_bear) html += '<div class="layer-reso reso-bear">✦ 看跌共振</div>';
    else html += '<div class="layer-reso reso-none">未共振</div>';
    html += '</div>';
    // 持仓卖出提示（仅"我的持仓"股显示；推荐/未持仓股无卖出信号）
    const pos = d.position || {};
    if (pos.in_position) {
      const advMap = {
        "持有": ["exit-hold", "持有", "持仓中"],
        "建议止损": ["exit-stop", "止损", "回撤超10%"],
        "建议离场": ["exit-watch", "离场", "破线+大盘转弱"]
      };
      const st = advMap[pos.advice] || ["exit-hold", "持有", ""];
      html += '<div class="layer-exit ' + st[0] + '"><span class="ex-badge">持仓·' + st[1] + '</span><span class="ex-desc">' + (pos.note || st[2]) + '　买入 ' + (pos.buy_date || "--") + ' @' + pos.buy_price + (pos.ret_pct != null ? '（收益 ' + (pos.ret_pct >= 0 ? "+" : "") + pos.ret_pct + '%）' : '') + '</span></div>';
    } else {
      html += '<div class="layer-exit exit-none"><span class="ex-badge">未持仓</span><span class="ex-desc">未登记持仓，不显示卖出提示</span></div>';
    }
    el.innerHTML = html;
  }

  function renderTrends(d) {
    const el = document.getElementById("trends-row");
    const order = ["daily", "weekly", "monthly"];
    const names = { daily: "日线", weekly: "周线", monthly: "月线" };
    const arrows = { up: "↗", down: "↘", sideways: "→", unknown: "·" };
    const labels = { up: "上升趋势", down: "下降趋势", sideways: "震荡趋势", unknown: "数据不足" };
    const strength = { strong: "强", medium: "中", weak: "弱" };
    el.innerHTML = "";
    order.forEach(k => {
      const t = (d.trends && d.trends[k]) || {};
      const dir = t.direction || "unknown";
      const st = t.strength || "weak";
      const card = document.createElement("div");
      card.className = "trend-card";
      card.innerHTML =
        '<div class="tc-head">' +
          '<span class="tc-arrow">' + arrows[dir] + '</span>' +
          '<span class="tc-period">' + names[k] + '</span>' +
          '<span class="tc-badge ' + dir + '">' + labels[dir] + (dir !== "unknown" ? " · " + strength[st] : "") + '</span>' +
        '</div>' +
        '<div class="tc-desc">' + (t.desc || "--") + '</div>';
      el.appendChild(card);
    });
  }

  function renderTech(d) {
    const el = document.getElementById("tech-row");
    const tech = d.tech || {};
    const macd = tech.macd || {};
    const rsi = tech.rsi || {};
    const vol = tech.volume || {};
    const macdC = macd.state && macd.state.indexOf("金叉") >= 0 ? "up" : macd.state && macd.state.indexOf("死叉") >= 0 ? "down" : "flat";
    const rsiC = (rsi.state === "超买") ? "down" : (rsi.state === "超卖") ? "up" : (rsi.state === "偏强") ? "up" : (rsi.state === "偏弱") ? "down" : "flat";
    el.innerHTML =
      techCard("MACD", fmt(macd.bar), macd.state || "--", macdC) +
      techCard("RSI(14)", fmt(rsi.value, 1), rsi.state || "--", rsiC) +
      techCard("量能", vol.state || "--", vol.state || "--", "flat");
  }

  function techCard(label, value, state, c) {
    return '<div class="tech-card">' +
      '<div class="t-label">' + label + '</div>' +
      '<div style="text-align:right">' +
        '<div class="t-value ' + c + '">' + value + '</div>' +
        '<div class="t-state ' + c + '">' + state + '</div>' +
      '</div>' +
    '</div>';
  }

  function renderConclusion(d) {
    const el = document.getElementById("conclusion");
    const c = d.conclusion || {};
    const pos = d.position || {};
    // 规避/卖出提示只对"我的持仓"股显示；推荐、未持仓股无卖出信号
    const isExit = pos.in_position && (pos.advice === "建议止损" || pos.advice === "建议离场");
    const base = c.rating === "偏多" ? "偏多" :
      c.rating === "谨慎偏多" ? "谨慎偏多" :
      c.rating === "偏空" ? "偏空" :
      c.rating === "谨慎偏空" ? "谨慎偏空" : "中性";
    const ratingText = isExit ? "规避" : base;
    const cls = isExit ? "c-rating neg" : "c-rating";
    let text = c.sentence || "--";
    if (isExit) text = "你的持仓（买入 " + (pos.buy_date || "--") + " @" + pos.buy_price + "）触发" + pos.advice + "：" + (pos.note || "") + "。" + text;
    el.innerHTML = '<div class="' + cls + '">' + ratingText + '</div><div class="c-text">' + text + '</div>';
  }

  // 技术面综合解读：根据蜡烛图/支撑阻力/量价理论生成一段文字总结
  var PAT_MEAN = {
    "上吊线": "上涨趋势顶部的看跌预警", "看跌吞没": "顶部看跌吞没，反转信号",
    "乌云盖顶": "顶部乌云盖顶，看跌信号", "黄昏星": "顶部黄昏之星，看跌反转",
    "三只乌鸦": "顶部三只乌鸦，看跌信号", "锤子线": "底部锤子线，看涨信号",
    "看涨吞没": "底部看涨吞没，反转信号", "穿刺形态": "底部穿刺形态，看涨信号",
    "启明星": "底部启明星，看涨反转", "红三兵": "底部红三兵，看涨信号"
  };
  function renderTechSummary(d) {
    const el = document.getElementById("tech-summary-text");
    const rows = d.kline || [];
    if (!rows.length) { el.innerHTML = '<div class="ts-line">数据不足，无法解读。</div>'; return; }
    const close = rows[rows.length - 1].close;
    const lines = [];
    // 1 趋势
    const dn = { up: "上升", down: "下降", sideways: "震荡", unknown: "不明" };
    const stn = { strong: "强", medium: "中", weak: "弱" };
    const dt = (d.trends && d.trends.daily) || {};
    const wt = (d.trends && d.trends.weekly) || {};
    const dDir = dn[dt.direction] || "不明", dSt = stn[dt.strength] || "";
    const wDir = dn[wt.direction] || "不明";
    let trendLine = "日线处于<strong>" + dDir + "趋势</strong>（强度" + dSt + "），周线" + wDir + "趋势";
    if (wt.direction === dt.direction && dt.direction !== "sideways" && dt.direction !== "unknown") trendLine += "，<strong>多周期共振</strong>";
    else if (dt.direction !== wt.direction && dt.direction !== "sideways" && dt.direction !== "unknown") trendLine += "，与周线方向不一致，短期波动为主";
    lines.push("【趋势】" + trendLine + "。");
    // 2 支撑/阻力
    const sr = calcSupportResistance(rows);
    const tch = function (n) { return n >= 3 ? "多次触及、较为重要" : (n === 2 ? "两次触及" : "近期触及"); };
    let srLine = "当前价 " + close;
    if (sr.resist.length) srLine += "，上方第一阻力 " + sr.resist[0].price + "（" + tch(sr.resist[0].n) + "）";
    if (sr.support.length) srLine += "，下方第一支撑 " + sr.support[0].price + "（" + tch(sr.support[0].n) + "）";
    if (!sr.support.length && !sr.resist.length) srLine += "，近期无明显支撑/阻力结构";
    lines.push("【支撑/阻力】" + srLine + "。");
    // 3 量价
    const div = detectVolumeDivergence(rows);
    const tech = d.tech || {};
    const volSt = (tech.volume && tech.volume.state) || "";
    let volLine = volSt || "量能平稳";
    if (div.length) {
      div.forEach(function (x) {
        volLine += x.type === "顶背离" ? "，出现<strong>顶背离</strong>（价创新高但量未跟上，上涨动能存疑）" : "，出现<strong>缩量见底</strong>（二次谷底缩量，抛压衰竭）";
      });
    } else {
      volLine += "，价格与成交量未见明显背离";
    }
    lines.push("【量价】" + volLine + "。");
    // 4 形态（看涨→强信号，看跌→参考；结合适配度）
    const pats = detectPatterns(rows).filter(function (p) { return p.index >= rows.length - 30; });
    const bullP = [], bearP = [];
    pats.forEach(function (p) { p.names.forEach(function (n) {
      if (BULL_PATTERNS.indexOf(n) >= 0) bullP.push(n);
      else if (BEAR_PATTERNS.indexOf(n) >= 0) bearP.push(n);
    }); });
    const adaptNow = calcAdapt();
    const adaptWarn = adaptNow.level === "low" ? "（该股波动偏大/市值偏小，形态信号参考价值低）" :
      (adaptNow.level === "high" ? "（大市值低波动，形态信号可信度高）" : "");
    if (bullP.length) {
      const desc = bullP.slice(0, 3).map(function (n) { return n + "（" + (PAT_MEAN[n] || "") + "）"; }).join("、");
      lines.push("【形态】近期出现看涨信号 " + desc + "。" + adaptWarn);
    } else if (bearP.length) {
      const desc = bearP.slice(0, 3).map(function (n) { return n + "（" + (PAT_MEAN[n] || "") + "）"; }).join("、");
      lines.push("【形态】近期出现看跌参考信号 " + desc + "（看跌形态命中率偏低，仅供参考）。" + adaptWarn);
    } else if (d.scan_detail && d.scan_detail.pats && d.scan_detail.pats.length) {
      // 统一口径：今日机会识别出的形态（机会检测不要求趋势方向）
      const desc = d.scan_detail.pats.slice(0, 3).map(function (n) { return n + "（" + (PAT_MEAN[n] || "") + "）"; }).join("、");
      const it = d.scan_detail.type === "rebound" ? "超跌反弹·抄底信号" : "趋势机会信号";
      lines.push("【形态】今日机会识别出 " + desc + "（" + it + "，机会检测不要求趋势方向）。" + adaptWarn);
    } else {
      lines.push("【形态】近30日无确认形态。" + adaptWarn);
    }
    // 4.5 层级共振
    if (d.layers) {
      const L = d.layers;
      const dd = { up: "上升", down: "下降", side: "震荡", unknown: "不明" };
      const md = (L.market && L.market.direction) || "unknown";
      const id = (L.industry && L.industry.direction) || "unknown";
      const sl = (L.stock && L.stock.trendline) || "none";
      const iname = (L.industry && L.industry.name) || "";
      if (L.resonance_bull) {
        lines.push("【层级共振】大盘" + dd[md] + " + 行业(" + iname + ")" + dd[id] + " + 个股上升趋势线，<strong>自上而下三线看涨共振</strong>（回测实证：共振后持有收益约为无共振的2倍，胜率54-56%）。");
      } else if (L.resonance_bear) {
        lines.push("【层级共振】大盘" + dd[md] + " + 行业(" + iname + ")" + dd[id] + " + 个股下降趋势线，三线看跌共振（注：A股下跌常快于形态，此信号参考价值弱）。");
      } else {
        lines.push("【层级共振】未形成共振：大盘" + dd[md] + "、" + (iname ? "行业(" + iname + ")" + dd[id] : "行业不明") + "、" + (sl === "up" ? "个股上升趋势线" : (sl === "down" ? "个股下降趋势线" : "个股无趋势线")) + "。");
      }
    }
    // 4.6 持仓卖出提示（仅"我的持仓"股显示；推荐/未持仓股无卖出信号）
    const pos = d.position || {};
    if (pos.in_position) {
      const rt = pos.ret_pct == null ? "--" : (pos.ret_pct >= 0 ? "+" : "") + pos.ret_pct + "%";
      if (pos.advice === "建议止损") {
        lines.push("【持仓】你的持仓触发<strong>止损建议</strong>（" + (pos.note || "回撤超10%") + "），买入 " + (pos.buy_date || "--") + " @" + pos.buy_price + "，当前收益 " + rt + "。");
      } else if (pos.advice === "建议离场") {
        lines.push("【持仓】" + (pos.note || "跌破MA20+大盘转弱") + "，建议<strong>离场</strong>。买入 " + (pos.buy_date || "--") + " @" + pos.buy_price + "，当前收益 " + rt + "。");
      } else {
        lines.push("【持仓】该股在你持仓中，买入 " + (pos.buy_date || "--") + " @" + pos.buy_price + "，当前收益 " + rt + "，趋势完好继续持有。");
      }
    }
    // 5 指标
    const macd = tech.macd || {};
    const rsi = tech.rsi || {};
    const macdSt = macd.state || "无";
    const rsiSt = rsi.state || "无";
    const rsiV = (rsi.value == null || isNaN(rsi.value)) ? "--" : Number(rsi.value).toFixed(1);
    lines.push("【指标】MACD " + macdSt + "，RSI(14) " + rsiV + "（" + rsiSt + "）。");
    // 6 综合倾向
    const c = d.conclusion || {};
    const ex6 = (d.layers && d.layers.exit) || {};
    let ratingLine = "【倾向】综合价格趋势、资金与基本面，当前评级 <strong>" + (c.rating || "中性") + "</strong>。";
    if (d.in_scan) {
      const it = d.in_scan === "rebound" ? "超跌反弹·抄底" : "趋势机会";
      const sd = d.scan_detail || {};
      const star = ({ 1: "★", 2: "★★", 3: "★★★" })[sd.level] || "";
      let ratHint = "";
      if (d.in_scan === "rebound") {
        ratHint = (c.rating && c.rating !== "偏多") ? "（当前评级" + c.rating + "，超跌反弹属博弈买点、趋势未确认，注意控制仓位）" : "（超跌反弹博弈机会）";
      }
      ratingLine = "【倾向】评级 <strong>" + (c.rating || "中性") + "</strong>，今日已列为<strong>" + it + "机会</strong>" + star + "（买入视角）。" + ratHint;
    } else if (ex6.state === "stop" || ex6.state === "exit_signal") {
      ratingLine = "【倾向】当前评级 <strong>" + (c.rating || "中性") + "</strong>，但持仓已处于<strong>" + (ex6.label || "离场") + "</strong>状态（" + (ex6.desc || "") + "），<strong>以离场信号为准，暂不宜新买入</strong>。";
    }
    lines.push(ratingLine);
    el.innerHTML = lines.map(function (l) { return '<div class="ts-line">' + l + '</div>'; }).join("");
  }

  // ---------- K线图 ----------
  // 跳空缺口检测：当前K线最低>昨高=向上缺口；当前K线最高<昨低=向下缺口
  function detectGaps(rows) {
    const gaps = [];
    for (let i = 1; i < rows.length; i++) {
      const c = rows[i], p = rows[i - 1];
      if (c.low > p.high) gaps.push({ index: i, type: "up" });
      else if (c.high < p.low) gaps.push({ index: i, type: "down" });
    }
    return gaps;
  }
  // K线形态识别
  // 判断某根K线所处局部趋势：up=上涨 / down=下跌 / flat=横盘
  // 用形态之前K线的 MA5 与 MA20 排列判断（避免把形态本身算进趋势）
  function trendAt(rows, i) {
    const end = i - 1;
    if (end < 5) return "flat";
    let s5 = 0;
    for (let k = end - 4; k <= end; k++) s5 += rows[k].close;
    s5 /= 5;
    const s20 = Math.max(0, end - 19);
    if (end - s20 + 1 < 10) return "flat";
    let m20 = 0;
    for (let k = s20; k <= end; k++) m20 += rows[k].close;
    m20 /= (end - s20 + 1);
    const c = rows[end].close;
    if (s5 > m20 && c > m20) return "up";
    if (s5 < m20 && c < m20) return "down";
    return "flat";
  }

  function detectPatterns(rows) {
    const out = [];
    const trends = [];
    for (let i = 0; i < rows.length; i++) trends.push(trendAt(rows, i));
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      const body = Math.abs(r.close - r.open);
      const rng = r.high - r.low;
      if (rng <= 0) continue;
      const upper = r.high - Math.max(r.open, r.close);
      const lower = Math.min(r.open, r.close) - r.low;
      const bull = r.close > r.open, bear = r.close < r.open;
      const t = trends[i];
      const names = [];
      let strong = false; // 形态强度：强形态(吞没实体比>1.5/锤子下影比>2.5/乌云或穿刺插入>70%)
      // 伞形线：下影线长(≥2倍实体)、上影极短，颜色无关；按所处趋势分：涨→上吊线(顶部看跌)，跌→锤子线(底部看涨)
      if (lower >= rng * 0.6 && upper <= rng * 0.15 && body >= rng * 0.05) {
        if (t === "up") names.push("上吊线");
        else if (t === "down") names.push("锤子线");
        if (lower / Math.max(body, 0.001) >= 2.5) strong = true;
      }
      // 吞没（必须在一段明显趋势中）
      if (i >= 1) {
        const p = rows[i - 1];
        const pBody = Math.abs(p.close - p.open);
        if (bull && p.close < p.open && r.open <= p.close && r.close >= p.open && body > pBody * 1.05 && t === "down") { names.push("看涨吞没"); if (body / Math.max(pBody, 0.001) >= 1.5) strong = true; }
        if (bear && p.close > p.open && r.open >= p.close && r.close <= p.open && body > pBody * 1.05 && t === "up") { names.push("看跌吞没"); if (body / Math.max(pBody, 0.001) >= 1.5) strong = true; }
      }
      // 乌云盖顶：上涨趋势中，白实体后黑实体跳空高开，收盘深插白实体(≥一半)
      if (i >= 1 && t === "up") {
        const p = rows[i - 1];
        const pBody = p.close - p.open;
        if (pBody > 0 && bear && r.open > p.high) {
          const ins = (p.close - r.close) / pBody;
          if (ins >= 0.5) { names.push("乌云盖顶"); if (ins >= 0.7) strong = true; }
        }
      }
      // 穿刺形态：下跌趋势中，黑实体后白实体跳空低开，收盘深刺黑实体(≥一半)
      if (i >= 1 && t === "down") {
        const p = rows[i - 1];
        const pBody = p.open - p.close;
        if (pBody > 0 && bull && r.open < p.low) {
          const ins = (r.close - p.close) / pBody;
          if (ins >= 0.5) { names.push("穿刺形态"); if (ins >= 0.7) strong = true; }
        }
      }
      // 三根组合（红三兵/三只乌鸦/启明星/黄昏星均结合趋势）
      if (i >= 2) {
        const a = rows[i - 2], b = rows[i - 1];
        if (t === "down" && a.close > a.open && b.close > b.open && bull && b.close > a.close && r.close > b.close) names.push("红三兵");
        if (t === "up" && a.close < a.open && b.close < b.open && bear && b.close < a.close && r.close < b.close) names.push("三只乌鸦");
        const bBody = Math.abs(b.close - b.open), bRng = b.high - b.low;
        if (bRng > 0 && bBody <= bRng * 0.3) {
          if (t === "down" && a.close < a.open && bull && r.close > (a.open + a.close) / 2) names.push("启明星");
          if (t === "up" && a.close > a.open && bear && r.close < (a.open + a.close) / 2) names.push("黄昏星");
        }
      }
      if (names.length) {
        // 量能确认：形态当日量 ≥ 1.3× 前5日均量（实测大市值/蓝筹上命中率显著提升）
        let vSum = 0;
        for (let k = Math.max(0, i - 5); k < i; k++) vSum += rows[k].volume;
        const vBase = vSum / (i >= 5 ? 5 : Math.max(1, i));
        if (vBase > 0 && rows[i].volume >= vBase * 1.3) {
          out.push({ index: i, names: names, strong: strong });
        }
      }
    }
    return out;
  }
  // 只在图上标注的强信号形态（弱信号/不在趋势上的不标）
  var STRONG_PATTERNS = ["看涨吞没", "看跌吞没", "锤子线", "上吊线", "启明星", "黄昏星", "红三兵", "三只乌鸦", "乌云盖顶", "穿刺形态"];
  // 看涨/看跌分组（回测实证：看涨形态命中率显著高于看跌 → 看涨标强、看跌标参考）
  var BULL_PATTERNS = ["看涨吞没", "锤子线", "启明星", "红三兵", "穿刺形态"];
  var BEAR_PATTERNS = ["看跌吞没", "上吊线", "黄昏星", "三只乌鸦", "乌云盖顶"];
  // 形态适配度：市值 + 近一年年化波动率（实证：大市值低波动票形态更有效）
  function calcAdapt() {
    const d = stockData || {};
    const daily = (klineCache[currentCode] && klineCache[currentCode].daily) || [];
    let score = 0;
    const mv = (d.quote && d.quote.total_mv) || 0;
    if (mv >= 1e11) score += 2; else if (mv >= 5e10) score += 1;
    if (daily && daily.length >= 60) {
      const closes = daily.map(r => r.close);
      const arr = [];
      for (let i = Math.max(1, closes.length - 250); i < closes.length; i++) {
        if (closes[i - 1] > 0) arr.push((closes[i] - closes[i - 1]) / closes[i - 1]);
      }
      if (arr.length >= 30) {
        const m = arr.reduce((a, b) => a + b, 0) / arr.length;
        const v = Math.sqrt(arr.reduce((a, b) => a + (b - m) * (b - m), 0) / arr.length) * Math.sqrt(250);
        if (v <= 0.35) score += 2; else if (v <= 0.50) score += 1;
      }
    }
    if (score >= 3) return { level: "high", text: "高适配" };
    if (score >= 2) return { level: "mid", text: "中适配" };
    return { level: "low", text: "低适配" };
  }

  // ---------- 西方趋势分析方法 ----------
  // 布林带：中轨MA20 ± 2倍标准差
  function calcBoll(rows, n) {
    const closes = rows.map(r => r.close), up = [], low = [];
    for (let i = 0; i < closes.length; i++) {
      if (i < n - 1) { up.push("-"); low.push("-"); continue; }
      let s = 0;
      for (let j = i - n + 1; j <= i; j++) s += closes[j];
      const m = s / n; let v = 0;
      for (let j = i - n + 1; j <= i; j++) v += (closes[j] - m) * (closes[j] - m);
      const sd = Math.sqrt(v / n);
      up.push(+(m + 2 * sd).toFixed(2));
      low.push(+(m - 2 * sd).toFixed(2));
    }
    return { up: up, low: low };
  }
  // 自动趋势线：取最近窗口内显著低点连线（上升），显著高点连线（下降），并延长
  function calcTrendLines(rows) {
    const n = Math.min(rows.length, 80);
    const start = rows.length - n;
    const lows = [], highs = [];
    for (let i = 1; i < rows.length - 1; i++) {
      if (rows[i].low <= rows[i - 1].low && rows[i].low <= rows[i + 1].low) lows.push({ idx: i, price: rows[i].low });
      if (rows[i].high >= rows[i - 1].high && rows[i].high >= rows[i + 1].high) highs.push({ idx: i, price: rows[i].high });
    }
    const marks = [];
    const inWin = p => p.idx >= start;
    const lowsW = lows.filter(inWin), highsW = highs.filter(inWin);
    // 上升趋势线：连接窗口内最低低点 → 最新低点（低点整体抬高）
    if (lowsW.length >= 2) {
      const last = lowsW[lowsW.length - 1];
      let min = lowsW[0];
      for (let i = 1; i < lowsW.length; i++) if (lowsW[i].price < min.price) min = lowsW[i];
      if (min.idx < last.idx && last.price > min.price) {
        // 延长趋势线到最新K线之后（按斜率外推），放大后仍可见
        const slope = (last.price - min.price) / (last.idx - min.idx);
        const endIdx = Math.min(rows.length - 1, last.idx + 15);
        const endPrice = last.price + slope * (endIdx - last.idx);
        marks.push([
          { xAxis: rows[min.idx].date, yAxis: min.price, lineStyle: { type: "dashed", color: "#f59e0b", width: 1.6 }, label: { formatter: "上升趋势线", color: "#f59e0b", position: "insideStartTop" } },
          { xAxis: rows[endIdx].date, yAxis: endPrice }
        ]);
      }
    }
    // 下降趋势线：连接窗口内最高高点 → 最新高点（高点整体走低）
    if (highsW.length >= 2) {
      const last = highsW[highsW.length - 1];
      let max = highsW[0];
      for (let i = 1; i < highsW.length; i++) if (highsW[i].price > max.price) max = highsW[i];
      if (max.idx < last.idx && last.price < max.price) {
        // 延长趋势线到最新K线之后（按斜率外推），放大后仍可见
        const slope = (last.price - max.price) / (last.idx - max.idx);
        const endIdx = Math.min(rows.length - 1, last.idx + 15);
        const endPrice = last.price + slope * (endIdx - last.idx);
        marks.push([
          { xAxis: rows[max.idx].date, yAxis: max.price, lineStyle: { type: "dashed", color: "#38bdf8", width: 1.6 }, label: { formatter: "下降趋势线", color: "#38bdf8", position: "insideStartTop" } },
          { xAxis: rows[endIdx].date, yAxis: endPrice }
        ]);
      }
    }
    return marks;
  }
  // 斐波那契回撤：最近窗口内最高/最低，按 0.236/0.382/0.5/0.618/0.786 画水平线
  function calcFibLevels(rows) {
    const n = Math.min(rows.length, 120), start = rows.length - n;
    let hi = -Infinity, lo = Infinity, hiIdx = 0, loIdx = 0;
    for (let i = start; i < rows.length; i++) {
      if (rows[i].high > hi) { hi = rows[i].high; hiIdx = i; }
      if (rows[i].low < lo) { lo = rows[i].low; loIdx = i; }
    }
    const range = hi - lo;
    if (range <= 0) return [];
    // 趋势方向：最高点出现在最低点之后 → 上升，回撤从高点往下
    const uptrend = hiIdx > loIdx;
    const levels = [0.236, 0.382, 0.5, 0.618, 0.786];
    const marks = [];
    levels.forEach(function (lv) {
      const price = uptrend ? hi - range * lv : lo + range * lv;
      marks.push({
        yAxis: price,
        lineStyle: { type: "dashed", color: "#e6b91e", width: 1.4 },
        label: { formatter: "斐波 " + lv, color: "#ffe08a", position: "end", fontSize: 10, backgroundColor: "rgba(13,17,23,.7)", padding: [2, 4], borderRadius: 3 }
      });
    });
    return marks;
  }

  // 支撑/阻力位：对窗口内局部高/低点聚类，按触及次数+成交量+时间加权取强支撑与阻力
  function calcSupportResistance(rows) {
    const win = Math.min(rows.length, 60);
    const start = rows.length - win;
    const pts = [];
    for (let i = 1; i < rows.length - 1; i++) {
      if (i < start) continue;
      const r = rows[i];
      const isHigh = r.high >= rows[i - 1].high && r.high >= rows[i + 1].high;
      const isLow = r.low <= rows[i - 1].low && r.low <= rows[i + 1].low;
      if (isHigh) pts.push({ price: r.high, vol: r.volume, idx: i });
      if (isLow) pts.push({ price: r.low, vol: r.volume, idx: i });
    }
    const close = rows[rows.length - 1].close;
    const out = { marks: [], support: [], resist: [] };
    if (pts.length < 2) return out;
    pts.sort(function (a, b) { return a.price - b.price; });
    // 相近价位聚类（阈值约 1.2%）
    const th = close * 0.012;
    const groups = [];
    let cur = { price: pts[0].price, vols: [pts[0].vol], idxs: [pts[0].idx], n: 1 };
    for (let k = 1; k < pts.length; k++) {
      if (Math.abs(pts[k].price - cur.price) <= th) {
        cur.vols.push(pts[k].vol); cur.idxs.push(pts[k].idx); cur.n++;
        cur.price = (cur.price * (cur.n - 1) + pts[k].price) / cur.n;
      } else { groups.push(cur); cur = { price: pts[k].price, vols: [pts[k].vol], idxs: [pts[k].idx], n: 1 }; }
    }
    groups.push(cur);
    // 评分：触及次数×2 + 量能(相对平均)×2 + 近期触及加分
    const seg = rows.slice(start);
    const avgVol = seg.reduce(function (a, r) { return a + r.volume; }, 0) / win;
    const lastIdx = rows.length - 1;
    groups.forEach(function (g) {
      const maxVol = Math.max.apply(null, g.vols);
      const lastTouch = Math.max.apply(null, g.idxs);
      g.score = g.n * 2 + (maxVol / (avgVol || 1)) * 2 + (lastIdx - lastTouch < 10 ? 1.5 : 0);
    });
    const support = groups.filter(function (g) { return g.price < close; }).sort(function (a, b) { return b.score - a.score; }).slice(0, 2);
    const resist = groups.filter(function (g) { return g.price > close; }).sort(function (a, b) { return b.score - a.score; }).slice(0, 2);
    support.forEach(function (g) {
      out.support.push({ price: Math.round(g.price), n: g.n, score: g.score });
      out.marks.push({ yAxis: g.price, lineStyle: { type: "dashed", color: "rgba(48,164,108,.6)", width: 1 }, label: { formatter: "支撑 " + Math.round(g.price) + " ×" + g.n, color: "#4ec98a", position: "insideStartTop", fontSize: 10 } });
    });
    resist.forEach(function (g) {
      out.resist.push({ price: Math.round(g.price), n: g.n, score: g.score });
      out.marks.push({ yAxis: g.price, lineStyle: { type: "dashed", color: "rgba(229,72,77,.6)", width: 1 }, label: { formatter: "阻力 " + Math.round(g.price) + " ×" + g.n, color: "#ff8a8a", position: "insideStartTop", fontSize: 10 } });
    });
    return out;
  }

  // 量价背离：顶背离（价新高量未跟上，上涨趋势）+ 底背离（二次谷底缩量，下跌趋势）
  function detectVolumeDivergence(rows) {
    const out = [];
    const win = Math.min(rows.length, 60);
    const start = rows.length - win;
    const highs = [], lows = [];
    for (let i = 2; i < rows.length - 2; i++) {
      if (i < start) continue;
      const r = rows[i];
      if (r.high >= rows[i - 1].high && r.high >= rows[i + 1].high && r.high >= rows[i - 2].high && r.high >= rows[i + 2].high)
        highs.push({ idx: i, price: r.high, vol: r.volume });
      if (r.low <= rows[i - 1].low && r.low <= rows[i + 1].low && r.low <= rows[i - 2].low && r.low <= rows[i + 2].low)
        lows.push({ idx: i, price: r.low, vol: r.volume });
    }
    // 顶背离：最近两高点价升量缩（仅上涨趋势）
    if (highs.length >= 2) {
      const a = highs[highs.length - 2], b = highs[highs.length - 1];
      if (b.price > a.price && b.vol < a.vol * 0.9 && trendAt(rows, b.idx) === "up")
        out.push({ idx: b.idx, type: "顶背离", price: b.price, vol: b.vol });
    }
    // 底背离：最近两低点价低或相近、量明显缩（二次谷底缩量，仅下跌趋势）
    if (lows.length >= 2) {
      const a = lows[lows.length - 2], b = lows[lows.length - 1];
      if (b.price <= a.price && b.vol < a.vol * 0.7 && trendAt(rows, b.idx) === "down")
        out.push({ idx: b.idx, type: "缩量见底", price: b.price, vol: b.vol });
    }
    return out;
  }

  function renderKline() {
    const container = document.getElementById("kline-chart");
    if (!charts.kline) {
      charts.kline = echarts.init(container);
    }
    const rows = klineCache[currentCode] ? klineCache[currentCode][currentPeriod] : [];
    if (!rows.length) {
      charts.kline.clear();
      charts.kline.showLoading("default", { text: "暂无数据" });
      return;
    }
    // 跳空 + 形态
    const gapByIndex = {}, patByIndex = {};
    detectGaps(rows).forEach(g => gapByIndex[g.index] = g.type);
    // 层级方向（用于逆势判断）：看涨形态需大盘/行业/个股全down，看跌需全up
    const L = (stockData && stockData.layers) || {};
    const mktDir = (L.market && L.market.direction) || "unknown";
    const indDir = (L.industry && L.industry.direction) || "unknown";
    detectPatterns(rows).forEach(p => {
      const strongNames = p.names.filter(n => STRONG_PATTERNS.indexOf(n) >= 0);
      const nm = strongNames[0] || p.names[0];
      const bearish = BEAR_PATTERNS.indexOf(nm) >= 0;
      const stockTrend = trendAt(rows, p.index);
      // 逆势：看涨形态需大盘+行业+个股全down；看跌形态需全up
      const contra = bearish
        ? (mktDir === "up" && indDir === "up" && stockTrend === "up")
        : (mktDir === "down" && indDir === "down" && stockTrend === "down");
      // 强信号 = 逆势共振 + 形态强度（回测实证：看涨逆势+强形态10日胜率56.9%）
      const isSuperStrong = contra && p.strong;
      patByIndex[p.index] = { all: p.names, strong: strongNames, contra: contra, superStrong: isSuperStrong, bearish: bearish };
    });
    const gapMarks = [], patMarks = [];
    const sr = calcSupportResistance(rows);
    const srMarks = sr.marks;
    const divMarks = detectVolumeDivergence(rows).map(function (dv) {
      const r = rows[dv.idx];
      const bearish = dv.type === "顶背离";
      return {
        coord: [r.date, bearish ? r.high * 1.03 : r.low * 0.97],
        value: dv.type,
        symbol: "circle", symbolSize: 7,
        itemStyle: { color: bearish ? "#e5484d" : "#30a46c", borderColor: "#0d1117", borderWidth: 1 },
        label: { show: true, formatter: dv.type, position: bearish ? "top" : "bottom", distance: 4, color: bearish ? "#ff8a8a" : "#4ec98a", fontSize: 11, backgroundColor: "rgba(13,17,23,.75)", padding: [2, 4], borderRadius: 3 }
      };
    });
    // 形态标签只在最近一段内标注，避免全图堆满；标签上下交替防止重叠
    const adaptInfo = calcAdapt();
    var PAT_START = Math.max(0, rows.length - 20);
    rows.forEach(function (r, i) {
      if (gapByIndex[i] === "up") gapMarks.push({ coord: [r.date, r.high * 1.006], value: "高开缺口", symbol: "triangle", symbolRotate: 0, symbolSize: 11, itemStyle: { color: "#e5484d" }, label: { show: false } });
      if (gapByIndex[i] === "down") gapMarks.push({ coord: [r.date, r.low * 0.994], value: "低开缺口", symbol: "triangle", symbolRotate: 180, symbolSize: 11, itemStyle: { color: "#30a46c" }, label: { show: false } });
      const pt = patByIndex[i];
      if (pt && pt.strong.length && i >= PAT_START) {
        const nm = pt.strong[0];
        const bearish = pt.bearish;
        // 强信号 = 逆势共振 + 形态强度（回测实证：看涨逆势+强形态10日胜率56.9%）
        const isSuper = pt.superStrong;
        // 分级：强信号→★醒目标注；看涨形态→金色；看跌形态→参考(灰)；低适配→全部弱化
        const isRef = bearish || adaptInfo.level === "low";
        const lbl = isSuper ? "★" + nm : (isRef ? nm + "·参考" : nm);
        // 形态标在柱体外部：看跌→柱体上方(压力侧)，看涨→柱体下方(支撑侧)，避免遮挡蜡烛
        const yPos = bearish ? r.high * 1.025 : r.low * 0.975;
        const pos = bearish ? "top" : "bottom";
        // 强信号用更醒目的颜色和大小
        const superColor = bearish ? "#ff4d4f" : "#ff7a45";
        const superLabelColor = bearish ? "#ff7875" : "#ffa940";
        patMarks.push({
          coord: [r.date, yPos], value: lbl,
          symbol: isSuper ? "star" : "diamond", symbolSize: isSuper ? 12 : (isRef ? 6 : 9),
          itemStyle: { color: isSuper ? superColor : (bearish ? "#64748b" : (adaptInfo.level === "low" ? "#94a3b8" : "#f59e0b")), borderColor: "#0d1117", borderWidth: 1 },
          label: { show: true, formatter: lbl, position: pos, distance: 6, color: isSuper ? superLabelColor : (bearish ? "#94a3b8" : (adaptInfo.level === "low" ? "#cbd5e1" : "#fcd34d")), fontSize: isSuper ? 12 : 11, fontWeight: isSuper ? "bold" : "normal", backgroundColor: isSuper ? "rgba(255,122,69,.18)" : "rgba(13,17,23,.75)", padding: [2, 5], borderRadius: 3 }
        });
      }
    });
    const dates = rows.map(r => r.date);
    const k = rows.map(r => [r.open, r.close, r.low, r.high]);
    const vols = rows.map(r => r.volume);
    const closes = rows.map(r => r.close);
    const ma = n => {
      const arr = [];
      for (let i = 0; i < closes.length; i++) {
        if (i < n - 1) { arr.push("-"); continue; }
        let s = 0;
        for (let j = i - n + 1; j <= i; j++) s += closes[j];
        arr.push(+(s / n).toFixed(2));
      }
      return arr;
    };

    const volColors = rows.map(r => (r.close >= r.open ? "rgba(229,72,77,.55)" : "rgba(48,164,108,.55)"));

    // ---- 叠加图层：核心信号固定（趋势线/支撑阻力/形态/跳空/背离），辅助工具开关（均线/布林带/斐波那契） ----
    const ovMa = document.getElementById("ov-ma") && document.getElementById("ov-ma").classList.contains("on");
    const ovBoll = document.getElementById("ov-boll") && document.getElementById("ov-boll").classList.contains("on");
    const ovFib = document.getElementById("ov-fib") && document.getElementById("ov-fib").classList.contains("on");
    let boll = null, fibMarks = [];
    const trendMarks = calcTrendLines(rows);   // 强信号：趋势线固定
    if (ovBoll) boll = calcBoll(rows, 20);
    if (ovFib) fibMarks = calcFibLevels(rows);
    const series = [
      {
        name: "K线", type: "candlestick", data: k,
        itemStyle: {
          color: "#e5484d", color0: "#30a46c",
          borderColor: "#e5484d", borderColor0: "#30a46c"
        },
        markPoint: {
          data: gapMarks.concat(patMarks, divMarks),
          animation: false, label: { show: false }
        },
        markLine: {
          silent: true, symbol: ["none", "none"],
          label: { show: true, fontSize: 11 },
          data: trendMarks.concat(fibMarks, srMarks)
        }
      }
    ];
    if (boll) {
      series.splice(0, 0,
        { name: "布林上轨", type: "line", data: boll.up, showSymbol: false, lineStyle: { width: 1, type: "dashed", color: "rgba(148,163,184,.5)" } },
        { name: "布林下轨", type: "line", data: boll.low, showSymbol: false, lineStyle: { width: 1, type: "dashed", color: "rgba(148,163,184,.5)" } }
      );
    }
    if (ovMa) {
      series.push(
        { name: "MA5", type: "line", data: ma(5), smooth: true, showSymbol: false, lineStyle: { width: 1, color: "#f59e0b" } },
        { name: "MA10", type: "line", data: ma(10), smooth: true, showSymbol: false, lineStyle: { width: 1, color: "#38bdf8" } },
        { name: "MA20", type: "line", data: ma(20), smooth: true, showSymbol: false, lineStyle: { width: 1, color: "#a78bfa" } },
        { name: "MA60", type: "line", data: ma(60), smooth: true, showSymbol: false, lineStyle: { width: 1, color: "#34d399" } }
      );
    }
    series.push({
      name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: vols,
      itemStyle: { color: p => volColors[p.dataIndex] }
    });
    const legendData = ["K线"];
    if (ovMa) legendData.push("MA5", "MA10", "MA20", "MA60");
    if (boll) legendData.splice(0, 0, "布林上轨", "布林下轨");

    // 先 clear 再 setOption：确保 markLine（趋势线/斐波那契）空数组时也能正确清除
    charts.kline.clear();
    charts.kline.setOption({
      backgroundColor: "transparent",
      animation: false,
      tooltip: {
        trigger: "axis", axisPointer: { type: "cross" },
        backgroundColor: "#1c2230", borderColor: "#2d333b", textStyle: { color: "#e6edf3", fontSize: 12 },
        formatter: function (ps) {
          var lines = [];
          for (var i = 0; i < ps.length; i++) {
            var p = ps[i];
            if (p.seriesName === "K线") {
              var r = rows[p.dataIndex];
              if (!r) continue;
              lines.push("<b>" + r.date + "</b>");
              lines.push("开盘: " + r.open);
              lines.push("收盘: " + r.close);
              lines.push("最低: " + r.low);
              lines.push("最高: " + r.high);
              lines.push("成交量: " + r.volume);
              var gp = gapByIndex[p.dataIndex];
              if (gp === "up") lines.push('<span style="color:#ff6b6b">跳空: ▲ 向上缺口(高开)</span>');
              if (gp === "down") lines.push('<span style="color:#4ec98a">跳空: ▼ 向下缺口(低开)</span>');
              var pt = patByIndex[p.dataIndex];
              if (pt && pt.all.length) lines.push("形态: " + pt.all.join("、"));
            } else {
              lines.push(p.marker + p.seriesName + ": " + (p.value == null ? "--" : p.value));
            }
          }
          return lines.join("<br/>");
        }
      },
      legend: {
        data: legendData,
        textStyle: { color: "#8b949e" }, top: 0, left: 8
      },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [
        { left: 60, right: 20, top: 28, height: "58%" },
        { left: 60, right: 20, top: "76%", height: "16%" }
      ],
      xAxis: [
        { type: "category", data: dates, boundaryGap: true, axisLine: { lineStyle: { color: "#2d333b" } }, axisLabel: { color: "#8b949e", fontSize: 11 } },
        { type: "category", gridIndex: 1, data: dates, boundaryGap: true, axisLine: { lineStyle: { color: "#2d333b" } }, axisLabel: { show: false } }
      ],
      yAxis: [
        { scale: true, splitLine: { lineStyle: { color: "#1c2230" } }, axisLabel: { color: "#8b949e", fontSize: 11 } },
        { gridIndex: 1, scale: true, splitLine: { show: false }, axisLabel: { show: false } }
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1], start: 45, end: 100 },
        { type: "slider", xAxisIndex: [0, 1], start: 45, end: 100, bottom: 2, height: 16, textStyle: { color: "#8b949e" } }
      ],
      series: series
    }, true);
  }

  // ---------- 资金图 ----------
  function renderFundChart(d) {
    const container = document.getElementById("fund-chart");
    if (!charts.fund) charts.fund = echarts.init(container);
    const rows = d.fund_flow || [];
    if (!rows.length) {
      charts.fund.clear();
      charts.fund.setOption({
        backgroundColor: "transparent",
        graphic: [{ type: "text", left: "center", top: "middle",
          style: { text: "资金流数据暂不可用（网络或数据源异常）", fill: "#8b949e", fontSize: 12 } }]
      });
      return;
    }
    const dates = rows.map(r => r.date);
    // 主力净流入单位: 元 -> 亿
    const vals = rows.map(r => (r.main_net == null ? 0 : +(r.main_net / 1e8).toFixed(2)));
    charts.fund.setOption({
      backgroundColor: "transparent", animation: false,
      tooltip: {
        trigger: "axis",
        formatter: p => {
          const i = p[0].dataIndex;
          const v = vals[i];
          return dates[i] + "<br/>主力净流入：" + (v >= 0 ? "+" : "") + v.toFixed(2) + " 亿<br/>净占比：" + fmt(rows[i].main_pct) + "%";
        },
        backgroundColor: "#1c2230", borderColor: "#2d333b", textStyle: { color: "#e6edf3", fontSize: 12 }
      },
      grid: { left: 66, right: 20, top: 16, bottom: 24 },
      xAxis: { type: "category", data: dates, axisLine: { lineStyle: { color: "#2d333b" } }, axisLabel: { color: "#8b949e", fontSize: 10 } },
      yAxis: { splitLine: { lineStyle: { color: "#1c2230" } },
        axisLabel: { color: "#8b949e", fontSize: 11,
          formatter: function (v) { return (v > 0 ? "+" : "") + v.toFixed(1) + "亿"; } } },
      series: [{
        type: "bar", data: vals,
        itemStyle: { color: p => (vals[p.dataIndex] >= 0 ? "#e5484d" : "#30a46c") },
        barWidth: "55%"
      }]
    }, true);
  }

  // ---------- 基本面图 ----------
  function renderFundamentalChart(d) {
    const container = document.getElementById("fundamental-chart");
    if (!charts.fundamental) charts.fundamental = echarts.init(container);
    const fin = d.financials || [];
    document.getElementById("fund-desc").textContent = (d.fundamentals && d.fundamentals.desc) || "";
    if (!fin.length) { charts.fundamental.clear(); return; }
    const dates = fin.map(r => r.date.slice(0, 7));
    // 同比 / 毛利率（%）
    const rev = fin.map(r => r.rev_yoy);
    const np = fin.map(r => r.np_yoy);
    const gm = fin.map(r => r.gm);
    // 绝对值（元 -> 亿）
    const revAbs = fin.map(r => r.revenue == null ? null : +(r.revenue / 1e8).toFixed(1));
    const npAbs = fin.map(r => r.net_profit == null ? null : +(r.net_profit / 1e8).toFixed(1));
    charts.fundamental.setOption({
      backgroundColor: "transparent", animation: false,
      tooltip: {
        trigger: "axis",
        backgroundColor: "#1c2230", borderColor: "#2d333b", textStyle: { color: "#e6edf3", fontSize: 12 },
        formatter: function (params) {
          if (!params || !params.length) return "";
          const date = params[0].axisValue || "";
          let html = '<div style="font-weight:600;margin-bottom:4px">' + date + '</div>';
          params.forEach(function (p) {
            if (p.value == null) return;
            let unit = "";
            if (p.seriesName.indexOf("(亿)") >= 0) unit = " 亿";
            else if (p.seriesName.indexOf("%") >= 0) unit = "%";
            html += '<div style="display:flex;align-items:center;gap:6px">' + p.marker + p.seriesName + ': <b>' + p.value + unit + '</b></div>';
          });
          return html;
        }
      },
      legend: { data: ["营收(亿)", "净利(亿)", "营收同比%", "净利同比%", "毛利率%"], textStyle: { color: "#8b949e" }, top: 0, left: 8, type: "scroll" },
      grid: { left: 60, right: 64, top: 36, bottom: 26 },
      xAxis: { type: "category", data: dates, axisLine: { lineStyle: { color: "#2d333b" } }, axisLabel: { color: "#8b949e", fontSize: 11 } },
      yAxis: [
        { type: "value", name: "%", nameTextStyle: { color: "#8b949e" }, splitLine: { lineStyle: { color: "#1c2230" } }, axisLabel: { color: "#8b949e", fontSize: 11, formatter: "{value}%" } },
        { type: "value", name: "亿元", nameTextStyle: { color: "#8b949e" }, splitLine: { show: false }, axisLabel: { color: "#8b949e", fontSize: 11 } }
      ],
      series: [
        { name: "营收(亿)", type: "bar", yAxisIndex: 1, data: revAbs, barWidth: "34%", itemStyle: { color: "rgba(245,158,11,.38)", borderRadius: [3, 3, 0, 0] } },
        { name: "净利(亿)", type: "bar", yAxisIndex: 1, data: npAbs, barWidth: "34%", itemStyle: { color: "rgba(56,189,248,.38)", borderRadius: [3, 3, 0, 0] } },
        { name: "营收同比%", type: "line", yAxisIndex: 0, data: rev, smooth: true, showSymbol: true, symbolSize: 6, lineStyle: { width: 2, color: "#f59e0b" }, itemStyle: { color: "#f59e0b" } },
        { name: "净利同比%", type: "line", yAxisIndex: 0, data: np, smooth: true, showSymbol: true, symbolSize: 6, lineStyle: { width: 2, color: "#38bdf8" }, itemStyle: { color: "#38bdf8" } },
        { name: "毛利率%", type: "line", yAxisIndex: 0, data: gm, smooth: true, showSymbol: true, symbolSize: 6, lineStyle: { width: 2, color: "#34d399" }, itemStyle: { color: "#34d399" } }
      ]
    }, true);
  }

  // ---------- 周期切换 ----------
  document.getElementById("period-tabs").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    document.querySelectorAll("#period-tabs button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentPeriod = btn.dataset.p;
    if (currentCode) renderKline();
  });

  // ---------- 叠加图层开关：内联 onclick（最可靠） + document 事件委托（兼容） ----------
  window.__toggleOverlay = function (id) {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.classList.toggle("on");
    if (currentCode) renderKline();
  };
  document.addEventListener("click", function (e) {
    const btn = e.target.closest ? e.target.closest(".ov-btn") : null;
    if (!btn) return;
    if (btn.id !== "ov-ma" && btn.id !== "ov-boll" && btn.id !== "ov-fib") return;
    window.__toggleOverlay(btn.id);
  });

  // ---------- 状态提示 ----------
  let statusTimer = null;
  function showStatus(msg) {
    const el = document.getElementById("market-status");
    el.textContent = msg;
    if (statusTimer) clearTimeout(statusTimer);
    if (msg) statusTimer = setTimeout(() => { el.textContent = ""; }, 4000);
  }

  // ---------- 信号回测弹窗 ----------
  const btModal = document.getElementById("bt-modal");
  const btBody = document.getElementById("bt-body");
  const btSub = document.getElementById("bt-sub");
  let btBusy = false;
  window.__closeBt = function () { btModal.style.display = "none"; };

  // ================== 规则使用手册 ==================
  const RULES = [
    { id: "order", title: "〇 使用顺序", html: `
<div class="rule-sec">
  <h3>使用总顺序（自上而下，层层过滤）</h3>
  <ol class="rule-list">
    <li><b>看大环境</b>：先定大盘方向（上证 MA20/60）。大盘走弱时系统性风险高，不做多。</li>
    <li><b>看个股趋势</b>：日 / 周 / 月三周期，判断当前处于上升 / 下降 / 震荡。</li>
    <li><b>层级共振确认</b>：大盘↑ + 行业↑ + 个股上升趋势线，三层同向看涨才考虑做多（实证胜率最高、收益2倍基准）。</li>
    <li><b>趋势内看形态</b>：形态只是"触发器"，必须出现在趋势中才有效；不在趋势上的形态直接忽略。</li>
    <li><b>支撑阻力定点位</b>：用支撑 / 阻力找具体买入、卖出价（命中率最高的信号类）。</li>
    <li><b>量价验证</b>：形态 + 放量确认才可靠；缩量上涨 / 量价背离要警惕。</li>
    <li><b>回测过滤</b>：软件内"信号回测"按钮看每个信号的历史命中率，&lt;50% 的信号谨慎。</li>
    <li><b>离场管理</b>：共振建仓后，用"破线+大盘转弱双确认"持有，破线后回撤≥10% 止损。</li>
  </ol>
  <div class="rule-note"><b>核心结论（回测实证）</b>：趋势方向单独看准确率约 50%（接近随机），<b>不能单独依赖</b>；必须叠加层级共振与趋势内形态。三层共振看涨持有 10 日胜率 54.7%、40 日 56.3%，均收益约为随机基准的 2 倍。反之<b>三层共振看跌系统性失效</b>（5 日方向准确率仅 17%），A 股下跌快于形态，看跌信号不值得依赖。</div>
</div>` },
    { id: "trend", title: "一 趋势判断", html: `
<div class="rule-sec">
  <h3>三周期趋势（日线 / 周线 / 月线）</h3>
  <ul class="rule-list">
    <li>用 MA5 / MA10 / MA20 / MA60 判断：<b>多头排列</b>（MA5&gt;MA10&gt;MA20 且价&gt;20日线）= 上升；<b>空头排列</b> = 下降。</li>
    <li>周期越长越稳定：日线噪声大，月线看大方向，周线居中。</li>
    <li><b>趋势方向单独准确率约 48-50%（接近随机）</b>，只能作为"背景"，不能作为买卖的唯一依据。</li>
    <li>上升趋势看支撑、下降趋势看压力；趋势是形态有效性的前提。</li>
  </ul>
  <div class="rule-note">软件在每只股票顶部展示日/周/月三张趋势卡，含 MA5/10/20/60、斜率与强弱。</div>
</div>` },
    { id: "resonance", title: "二 层级共振", html: `
<div class="rule-sec">
  <h3>大盘 → 行业 → 个股 三层共振（本软件核心）</h3>
  <ul class="rule-list">
    <li><b>大盘层</b>：上证指数 MA20/MA60 方向。</li>
    <li><b>行业层</b>：行业成分股等权指数方向。</li>
    <li><b>个股层</b>：严格上升趋势线（≥3 触点 / 跨度≥40 日 / 容差 1.5%）。</li>
    <li><b>看涨共振</b>：三层全部看涨 → 强做多信号。</li>
  </ul>
  <div class="rule-note"><b>实证数据</b>：三层共振看涨持有 10 日均收益 <b>+2.93%</b>（胜率 54.7%）、20 日 <b>+5.74%</b>（54.3%）、40 日 <b>+9.63%</b>（56.3%）；同窗口随机基准仅 +1.20% / +2.40% / +4.69%。<br><b>共振看跌系统性失效</b>（5 日 17%、10 日 26%）：A 股下跌快于形态，看跌信号不可依赖，软件不做看跌共振。<br>当前大盘/行业震荡时共振不触发（震荡市无共振，避免噪声）。</div>
</div>` },
    { id: "pattern", title: "三 蜡烛图形态", html: `
<div class="rule-sec">
  <h3>形态必须结合趋势（不在趋势上的形态不显示、不采用）</h3>
  <h4>反转形态（趋势末端出现才有意义）</h4>
  <ul class="rule-list">
    <li><b>伞形线</b>：下跌趋势中=锤子线（可看涨），上涨趋势中=上吊线（需次日验证，收盘低于实体才可靠）。</li>
    <li><b>吞没形态</b>：2 根 K 线，后者实体包住前者实体，须在明显趋势中；第二根实体越大、量越大越有效。</li>
    <li><b>乌云盖顶</b>（看跌）/ <b>穿刺形态</b>（看涨）：插入前实体越深，反转越强。</li>
    <li><b>启明星 / 黄昏星</b>：3 根 K 线，中间小实体与前后跳空；第二根为十字线时信号更强（弃婴形态极罕见）。</li>
    <li><b>流星 / 倒锤子</b>：分别出现在上涨 / 下跌趋势后，需要次日验证。</li>
  </ul>
  <h4>持续形态（趋势中确认）</h4>
  <ul class="rule-list">
    <li><b>红三兵 / 三只乌鸦</b>：三根同向 K 线，收盘近最高/最低；是趋势持续信号。</li>
    <li><b>上升 / 下降三法</b>：长实体 + 2-3 根小实体（在长实体范围内）+ 长实体收尾，中间不能是十字线。</li>
    <li><b>窗口（缺口）</b>：向上跳空 = 支撑，向下跳空 = 压力；软件在 K 线图上标注跳空。</li>
  </ul>
  <h4>单根与弱信号</h4>
  <ul class="rule-list">
    <li><b>十字线</b>：顶部比底部有效；需出现在长实体之后 / 重要位置 / 超买超卖，单独出现无意义。</li>
    <li><b>纺锤线 / 孕线 / 平头</b>：弱信号，须结合后续确认，不单独采用。</li>
  </ul>
  <div class="rule-note"><b>量能确认</b>：形态当日成交量 ≥ 1.3× 前 5 日均量时命中率显著提升（大市值蓝筹实测）。软件只在"趋势内 + 放量"时标记形态。</div>
</div>` },
    { id: "sr", title: "四 支撑 / 阻力", html: `
<div class="rule-sec">
  <h3>支撑与阻力的来源</h3>
  <ul class="rule-list">
    <li>下影线低点 = <b>支撑</b>；上影线高点 = <b>阻力</b>。</li>
    <li>看涨吞没 → 取两根 K 线<b>下影线低点</b>为支撑；看跌吞没 → 取<b>上影线高点</b>为阻力。</li>
    <li>乌云盖顶的上影线 = 阻挡位；穿刺的下影线 = 支撑位。</li>
    <li>缺口、长蜡烛线、整数价位 = 潜在支撑 / 阻力。</li>
    <li><b>极性转换</b>：原支撑被跌破后变阻力，原阻力被突破后变支撑。</li>
  </ul>
  <div class="rule-note"><b>实证</b>：软件个股回测中<b>支撑位守住率约 79%、阻力位压制率约 77%</b>——是所有信号类中命中率最高的一类，适合用来定买卖点位。<br>重要度看：成交量越大、触达次数越多、距当前越近，支撑/阻力越有效。</div>
</div>` },
    { id: "volume", title: "五 量价关系", html: `
<div class="rule-sec">
  <h3>用成交量验证价格信号</h3>
  <ul class="rule-list">
    <li><b>量价齐升</b>：正常，趋势可延续（趋势未破坏前持有）。</li>
    <li><b>价新高、量不新高</b>（上涨中）：危险信号，量领先于价。</li>
    <li><b>价涨量缩</b>（下跌中反弹）：卖方惜售，不是真强，反弹空间有限。</li>
    <li><b>抛物线暴涨</b>：量价指数式拉升后迅速回落，后知后觉，不可追。</li>
    <li><b>抛售高潮</b>（长期下跌后突然巨量）：可能见底。</li>
    <li><b>二次谷底缩量</b>：第二个谷底量明显小于第一个 → 看涨（软件在解读中提示"缩量见底"）。</li>
    <li><b>量大价不动</b>：涨势中=派发（看空）；跌势中=吸筹（看涨）。</li>
  </ul>
  <div class="rule-note">缩量上涨需分辨：超跌后或长期横盘后的缩量上涨才安全（筹码集中、散户少）。</div>
</div>` },
    { id: "trendline", title: "六 趋势线", html: `
<div class="rule-sec">
  <h3>趋势线规则</h3>
  <ul class="rule-list">
    <li>连接 2 个以上低点 = <b>支撑线</b>；连接 2 个以上高点 = <b>阻力线</b>。</li>
    <li>软件采用<b>严格趋势线</b>：≥3 个触点、跨度≥40 日、容差 1.5%，避免随手画的假线。</li>
    <li>触点越多、跨越时间越长越有效；越陡峭越易被突破。</li>
    <li><b>突破后</b>：上升线跌破→约 47% 概率下跌（弱信号，需结合大盘）；三层共振时的突破准确率提升到约 52%。</li>
    <li><b>趋势线延长</b>：被突破后，原支撑变阻力、原阻力变支撑（极性转换）。</li>
  </ul>
  <div class="rule-note">短期（2-4 周）趋势线可靠性低，长期（数月以上）更有效。</div>
</div>` },
    { id: "exit", title: "七 离场管理", html: `
<div class="rule-sec">
  <h3>离场双确认（共振建仓后的持仓管理）</h3>
  <ul class="rule-list">
    <li><b>趋势完好</b>：未跌破上升趋势线 → 继续持有。</li>
    <li><b>双确认中</b>：已破线但大盘未转弱 → <b>继续持有</b>（避免卖飞，等大盘确认）。</li>
    <li><b>双确认离场</b>：跌破上升趋势线 <b>且</b> 大盘转弱 → 离场信号成立。</li>
    <li><b>止损</b>：破线后回撤建仓价 ≥10% → 强制止损（控风险）。</li>
  </ul>
  <div class="rule-note"><b>实证对比</b>：破线即走单笔 +7.4%、盈亏比 5.95；<b>破线+大盘转弱双确认</b>单笔 +71%、盈亏比 13.3（避免卖飞），但纯双确认持仓过久最大亏损 -76%；<b>加 10% 止损</b>后最大亏损压到 -41%、盈亏比 14.1、年化 52%。<br>结论：双确认 <b>必须配套</b> 破线后 10% 止损。</div>
</div>` },
    { id: "bt", title: "八 回测准确率", html: `
<div class="rule-sec">
  <h3>用历史命中率过滤信号</h3>
  <ul class="rule-list">
    <li>软件内"<b>信号回测</b>"按钮：用约 4 年历史，在每点用<b>当时可见数据</b>生成信号，再对照之后走势统计命中率。</li>
    <li>信号项：形态信号（趋势内）、支撑位守住率、阻力位压制率、量价背离、趋势方向。</li>
    <li>支撑 / 阻力命中率最高（约 77-79%），趋势方向约 47-50%，形态需结合趋势。</li>
    <li><b>样本 &lt;10 时仅供参考</b>，不构成决策依据。</li>
  </ul>
  <div class="rule-note">命中率是概率参考，不是确定预测；命中率高 ≠ 每次都准，用于给信号"打分"而非"保证"。</div>
</div>` },
    { id: "discipline", title: "九 使用纪律", html: `
<div class="rule-sec">
  <h3>纪律与边界</h3>
  <ul class="rule-list">
    <li>本软件适用于<b>大市值（≥1000 亿）机构白马</b>：波动小、形态更符合经典理论（高适配池）。</li>
    <li><b>只看趋势上的信号</b>：非趋势信号直接忽略（弱信号不显示）。</li>
    <li><b>看跌 / 做空信号参考价值低</b>：A 股下跌快于形态，主要关注做多信号。</li>
    <li>所有信号是<b>概率参考</b>：结合基本面（营收 / 净利 / 毛利率趋势）一起判断。</li>
    <li>共振只在趋势市出现；震荡市无共振是正常现象，不是故障。</li>
  </ul>
  <div class="rule-note">建议阅读顺序：〇使用顺序 → 二层级共振 → 三形态 → 四支撑阻力 → 七离场，其余按需查阅。</div>
</div>` },
  ];
  let _rulesRendered = false;
  function renderRules() {
    const nav = document.getElementById("rules-nav");
    const body = document.getElementById("rules-body");
    if (!nav || !body || _rulesRendered) return;
    nav.innerHTML = ""; body.innerHTML = "";
    RULES.forEach(function (r) {
      const n = document.createElement("div");
      n.className = "rule-nav-item" + (r.id === "order" ? " active" : "");
      n.textContent = r.title;
      n.addEventListener("click", function () {
        document.querySelectorAll(".rule-nav-item").forEach(x => x.classList.remove("active"));
        n.classList.add("active");
        const sec = document.getElementById("rule-" + r.id);
        if (sec) sec.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      nav.appendChild(n);
      const d = document.createElement("div");
      d.className = "rule-card";
      d.id = "rule-" + r.id;
      d.innerHTML = r.html;
      body.appendChild(d);
    });
    _rulesRendered = true;
  }
  window.__openRules = function () {
    renderRules();
    document.getElementById("rules-modal").style.display = "flex";
  };
  window.__closeRules = function () {
    document.getElementById("rules-modal").style.display = "none";
  };
  function openBt() {
    if (!currentCode) { showStatus("请先选择一只自选股", true); return; }
    btModal.style.display = "flex";
    runBacktest();
  }
  async function runBacktest() {
    if (btBusy) return;
    btBusy = true;
    const code = currentCode;
    btBody.innerHTML = '<div class="bt-loading">正在用历史数据回测 <b>' + code + '</b> ...</div>';
    try {
      const res = await fetch("/api/backtest/" + code);
      const d = await res.json();
      if (!d.ok) { btBody.innerHTML = '<div class="bt-loading">' + (d.error || "回测失败") + '</div>'; btBusy = false; return; }
      btSub.textContent = (d.name ? d.name + " " : "") + d.code + " · " + d.n + "根 · " + d.range;
      let html = '<table class="bt-table"><thead><tr><th>信号</th><th>命中率</th><th>命中/样本</th><th>参考</th></tr></thead><tbody>';
      const rows = [
        ["pat", "形态信号(趋势内)"], ["pat_bear", "看跌形态"], ["pat_bull", "看涨形态"],
        ["sr_support", "支撑位守住率"], ["sr_resist", "阻力位压制率"],
        ["div", "量价背离"], ["trend", "趋势方向(未来10日)"]
      ];
      rows.forEach(function (r) {
        const v = d.result[r[0]];
        if (!v || !v.total) return;
        const rate = v.rate == null ? "--" : v.rate.toFixed(1) + "%";
        const ref = (v.total < 10) ? "样本少,仅供参考" : refText(r[0], v.rate);
        const cls = rateClass(v.rate);
        html += '<tr><td>' + r[1] + '</td><td class="rate ' + cls + '">' + rate + '</td><td>' + v.hit + "/" + v.total + '</td><td class="ref">' + ref + '</td></tr>';
      });
      html += '</tbody></table>';
      html += '<div class="bt-note">' + noteText() + '</div>';
      btBody.innerHTML = html;
    } catch (e) {
      btBody.innerHTML = '<div class="bt-loading">回测请求失败：' + e.message + '</div>';
    }
    btBusy = false;
  }
  function rateClass(rate) {
    if (rate == null) return "na";
    if (rate >= 65) return "good";
    if (rate >= 55) return "mid";
    return "low";
  }
  function refText(key, rate) {
    if (rate == null) return "样本不足";
    if (key === "trend") return rate >= 55 ? "优于随机" : "≈随机,勿当择时依据";
    if (key.indexOf("sr_") === 0) return rate >= 65 ? "较可靠" : (rate >= 55 ? "一般" : "偏弱,防极性转换");
    if (key.indexOf("pat") === 0) return rate >= 65 ? "有效信号" : "参考有限";
    if (key === "div") return "样本较少,参考";
    return "";
  }
  function noteText() {
    return "说明：形态/支撑/阻力/背离均为<strong>概率性信号</strong>，命中率基于约4年历史，不代表未来。形态已加<strong>量能确认</strong>（形态日量≥1.3×前5日均量，实测在白马/大市值上命中率更高）。趋势方向本身接近随机（约50%），它的价值在于<strong>过滤</strong>哪些形态信号有效，而非直接预测涨跌。";
  }
  document.getElementById("btn-backtest").addEventListener("click", openBt);
  document.getElementById("bt-refresh").addEventListener("click", runBacktest);
  btModal.addEventListener("click", function (e) {
    if (e.target === btModal) btModal.style.display = "none";
  });

  // ---------- 形态可靠性测试弹窗 ----------
  const ptModal = document.getElementById("pt-modal");
  const ptBody = document.getElementById("pt-body");
  const ptSub = document.getElementById("pt-sub");
  let ptBusy = false;
  window.__closePt = function () { ptModal.style.display = "none"; };
  function openPt() {
    if (!currentCode) { setStatus("请先在左侧选择一只股票"); return; }
    ptModal.style.display = "flex";
    runPatternTest();
  }
  async function runPatternTest() {
    if (ptBusy) return;
    ptBusy = true;
    const code = currentCode;
    ptBody.innerHTML = '<div class="bt-loading">正在扫描 <b>' + code + '</b> 历史形态并统计后市走势 ...</div>';
    try {
      const res = await fetch("/api/pattern_test/" + code);
      const d = await res.json();
      if (!d.ok) { ptBody.innerHTML = '<div class="bt-loading">' + (d.error || "测试失败") + '</div>'; ptBusy = false; return; }
      ptSub.textContent = (d.name ? d.name + " " : "") + d.code + " · " + d.n + "根 · " + d.range;
      const H = d.horizons || [3, 6, 10];
      const thH = H.map(function (h) { return "<th>" + h + "日胜率</th><th>" + h + "日均收益</th>"; }).join("");
      let html = '<table class="bt-table"><thead><tr><th>形态</th><th>方向</th><th>样本</th>' + thH + '</tr></thead><tbody>';
      // 方向汇总行
      function sumRow(label, key, color) {
        const sm = d.summary && d.summary[key];
        if (!sm) return "";
        let cells = "";
        H.forEach(function (h) {
          const b = sm[String(h)];
          if (!b || !b.total) { cells += "<td>--</td><td>--</td>"; return; }
          const r = b.rate == null ? "--" : '<span class="rate ' + rateClass(b.rate) + '">' + b.rate.toFixed(1) + "%</span>";
          cells += "<td>" + r + " (" + b.hit + "/" + b.total + ")</td>";
          cells += "<td>" + (b.avg_ret == null ? "--" : (b.avg_ret >= 0 ? "+" : "") + b.avg_ret.toFixed(2) + "%") + "</td>";
        });
        return '<tr class="pt-sum" style="color:' + color + '"><td><b>' + label + '（全部）</b></td><td>' + (key === "bull" ? "看涨" : "看跌") + '</td><td>' + (sm[H[0]] ? sm[H[0]].total : "--") + '</td>' + cells + '</tr>';
      }
      html += sumRow("看涨形态", "bull", "#30a46c");
      html += sumRow("看跌形态", "bear", "#e5484d");
      (d.patterns || []).forEach(function (p) {
        const isBull = p.dir === "看涨";
        const color = isBull ? "#30a46c" : "#e5484d";
        let cells = "";
        H.forEach(function (h) {
          const b = p.by_day[String(h)];
          if (!b || !b.total) { cells += "<td>--</td><td>--</td>"; return; }
          const r = b.rate == null ? "--" : '<span class="rate ' + rateClass(b.rate) + '">' + b.rate.toFixed(1) + "%</span>";
          cells += "<td>" + r + " (" + b.hit + "/" + b.total + ")</td>";
          cells += "<td>" + (b.avg_ret == null ? "--" : (b.avg_ret >= 0 ? "+" : "") + b.avg_ret.toFixed(2) + "%") + "</td>";
        });
        const cls = isBull ? "pt-bull" : "pt-bear";
        html += '<tr class="' + cls + '"><td>' + p.name + '</td><td style="color:' + color + '">' + p.dir + '</td><td>' + p.count + '</td>' + cells + '</tr>';
      });
      html += '</tbody></table>';
      html += '<div class="bt-note">说明：胜率 = 看涨形态出现后 N 日上涨 / 看跌形态出现后 N 日下跌 的比例；均收益 = 后 N 日相对形态日收盘价的平均涨跌幅。样本数不足 10 时仅供参考。形态识别口径与图上标注完全一致（趋势内 + 量能确认）。</div>';
      ptBody.innerHTML = html;
    } catch (e) {
      ptBody.innerHTML = '<div class="bt-loading">测试请求失败：' + e.message + '</div>';
    }
    ptBusy = false;
  }
  document.getElementById("btn-pattest").addEventListener("click", openPt);
  document.getElementById("pt-refresh").addEventListener("click", runPatternTest);
  ptModal.addEventListener("click", function (e) {
    if (e.target === ptModal) ptModal.style.display = "none";
  });

  // ---------- 窗口大小调整 ----------
  window.addEventListener("resize", () => {
    Object.values(charts).forEach(c => c && c.resize());
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".add-box") && !e.target.closest(".search-result")) {
      searchResult.classList.remove("open");
      searchResult.innerHTML = "";
    }
  });

  // ---------- 高适配池（按行业分组） ----------
  let hfData = null;
  async function loadHighfit() {
    const box = document.getElementById("hf-list");
    try {
      // 拉池子门槛信息（供高适配页签显示"市值≥X亿"）
      fetch("/api/pool/info").then(r => r.json()).then(function (pi) {
        window.__poolInfo = pi;
        if (hfData) renderHighfit(hfData);
      }).catch(function () {});
      const res = await fetch("/api/highfit");
      const data = await res.json();
      hfData = data;
      renderHighfit(data);
    } catch (e) {
      document.getElementById("hf-count").textContent = "高适配池加载失败";
      box.innerHTML = '<div class="flat" style="color:var(--text-dim);font-size:12px;padding:14px">加载失败，请重试</div>';
    }
  }
  function renderHighfit(data) {
    // 显示池子门槛（来自 /api/pool/info，不阻塞列表渲染）
    let thrTxt = "";
    try {
      const pi = window.__poolInfo;
      if (pi && pi.threshold) thrTxt = "（市值≥" + pi.threshold + "亿）";
    } catch (e) {}
    document.getElementById("hf-count").textContent = "高适配池 " + data.total + " 只 · " + data.groups.length + " 个行业" + thrTxt;
    const box = document.getElementById("hf-list");
    box.innerHTML = "";
    data.groups.forEach(function (g) {
      const div = document.createElement("div");
      div.className = "hf-group";
      const title = document.createElement("div");
      title.className = "hf-title";
      title.innerHTML = '<span>' + g.ind + '（' + g.count + '）</span><span class="hf-arrow">▼</span>';
      title.addEventListener("click", function () { div.classList.toggle("collapsed"); });
      const ul = document.createElement("ul");
      ul.className = "hf-items";
      g.items.forEach(function (it) {
        const li = document.createElement("li");
        if (it.code === currentCode) li.className = "active";
        li.innerHTML =
          '<div class="wl-main"><div class="wl-name">' + it.name + '</div><div class="wl-code">' + it.code + '</div></div>' +
          '<div class="wl-price"><div class="p ' + cls(it.pct_chg) + '">' + fmt(it.price) + '</div><div class="c ' + cls(it.pct_chg) + '">' + sign(it.pct_chg) + fmt(it.pct_chg) + '%</div></div>';
        li.addEventListener("click", function () { selectStock(it.code); });
        ul.appendChild(li);
      });
      div.appendChild(title);
      div.appendChild(ul);
      box.appendChild(div);
    });
  }
  // Tab 切换（自选股 / 高适配 / 今日机会 / 模拟持仓）
  const tabWatch = document.getElementById("tab-watch");
  const tabHigh = document.getElementById("tab-highfit");
  const tabScan = document.getElementById("tab-scan");
  const tabPaper = document.getElementById("tab-paper");
  const tabPos = document.getElementById("tab-pos");
  function switchTab(name) {
    const isWatch = name === "watch";
    const isHigh = name === "highfit";
    const isScan = name === "scan";
    const isPaper = name === "paper";
    const isPos = name === "pos";
    tabWatch.classList.toggle("active", isWatch);
    tabHigh.classList.toggle("active", isHigh);
    tabScan.classList.toggle("active", isScan);
    if (tabPaper) tabPaper.classList.toggle("active", isPaper);
    if (tabPos) tabPos.classList.toggle("active", isPos);
    document.getElementById("panel-watch").style.display = isWatch ? "" : "none";
    document.getElementById("panel-highfit").style.display = isHigh ? "" : "none";
    document.getElementById("panel-scan").style.display = isScan ? "" : "none";
    if (document.getElementById("panel-paper")) document.getElementById("panel-paper").style.display = isPaper ? "" : "none";
    if (document.getElementById("panel-pos")) document.getElementById("panel-pos").style.display = isPos ? "" : "none";
    if (isHigh && !hfData) loadHighfit();
    if (isScan) renderScanList();
    if (isPaper) renderPaper();
    if (isPos) renderPositions();
  }
  tabWatch.addEventListener("click", function () { switchTab("watch"); });
  tabHigh.addEventListener("click", function () { switchTab("highfit"); });
  tabScan.addEventListener("click", function () { switchTab("scan"); });
  if (tabPaper) tabPaper.addEventListener("click", function () { switchTab("paper"); });
  if (tabPos) tabPos.addEventListener("click", function () { switchTab("pos"); });
  document.getElementById("hf-refresh").addEventListener("click", function () { loadHighfit(); });
  document.getElementById("scan-run").addEventListener("click", function () {
    const infoEl = document.getElementById("scan-info");
    if (infoEl) infoEl.textContent = "扫描中(约1-3分钟)…";
    fetch("/api/scan/run", { method: "POST" }).then(r => r.json()).then(function (d) {
      if (d.ok) {
        if (infoEl) infoEl.textContent = "扫描完成，机会 " + (d.signals || []).length + " 只";
        renderEnvBar(d.env || null);
        renderScanList();
      } else if (infoEl) infoEl.textContent = d.msg || "扫描失败";
    }).catch(function () { if (infoEl) infoEl.textContent = "扫描失败"; });
  });

  // ---------- 市场环境条 ----------
  function renderEnvBar(env) {
    const bar = document.getElementById("env-bar");
    if (!bar) return;
    if (!env) { bar.style.display = "none"; return; }
    const sc = env.score;
    if (sc === null || sc === undefined) { bar.style.display = "none"; return; }
    const cls = env.action === "filter_out" ? "env-bad" : "env-ok";
    const modeTxt = { auto: "自动", bull: "进取·牛市", bear: "稳健·熊市" }[env.mode] || env.mode;
    bar.innerHTML = '<span class="' + cls + '">环境 ' + sc + '/6</span>' +
      ' · 模式 ' + modeTxt +
      ' · 阈值 ' + env.threshold +
      ' · 建议仓位 ' + (env.pos_pct || 0) + '%' +
      ' · ' + (env.note || "");
    bar.style.display = "";
  }

  // ---------- 今日机会列表 ----------
  function renderScanList() {
    const listEl = document.getElementById("scan-list");
    const infoEl = document.getElementById("scan-info");
    if (!listEl) return;
    fetch("/api/scan/status").then(r => r.json()).then(function (d) {
      const sigs = d.signals || [];
      const st = d.status || {};
      if (infoEl) infoEl.textContent = (d.date ? d.date + " · " : "") + "机会 " + sigs.length + " 只" +
        " · ★=信号强度（抄底最高2★；趋势3★=含周线确认的真趋势，2★以下=日线反弹短线）" +
        (st.running ? " · 扫描中..." : (st.msg ? " · " + st.msg : ""));
      renderEnvBar(d.env || null);
      if (!sigs.length) {
        listEl.innerHTML = '<div class="scan-empty">暂无符合条件的信号<br><span>大盘/行业震荡或未放量时规则不触发属正常。<br>点"▶ 扫描"可立即重跑。</span></div>';
        return;
      }
      const stars = { 1: "★", 2: "★★", 3: "★★★" };
      function itemHtml(it) {
        const isR = it.type === "rebound";
        const col = it.level >= 2 ? "var(--accent)" : "var(--text-dim)";
        const rat = it.rating || "";
        const ratCls = (rat === "偏多" || rat === "谨慎偏多") ? "up" : (rat === "偏空" || rat === "谨慎偏空") ? "down" : "flat";
        const ratHtml = rat ? '<span class="scan-rat ' + ratCls + '" title="综合评级">' + rat + '</span>' : '';
        return '<div class="scan-item" data-code="' + it.code + '" title="点击查看图形">' +
          '<div class="scan-item-head"><span class="scan-type ' + (isR ? "reb" : "trd") + '">' + (isR ? "抄底" : "趋势") + '</span>' +
          '<span class="scan-name">' + it.name + ' <em>' + it.code + '</em></span>' +
          '<span class="scan-level" style="color:' + col + '">' + (stars[it.level] || "★") + '</span></div>' +
          '<div class="scan-item-sub">' + ratHtml + ' ' + (it.ind || "") + " · " + it.price + " " + (it.change_pct >= 0 ? "+" : "") + it.change_pct + '%</div>' +
          '<div class="scan-item-sub">' + (it.ind || "") + " · " + it.price + " " + (it.change_pct >= 0 ? "+" : "") + it.change_pct + '%</div>' +
          '<div class="scan-tags">' + (it.tags || []).map(function (t) { return "<span>" + t + "</span>"; }).join("") + '</div>' +
          '</div>';
      }
      const rebound = sigs.filter(function (s) { return s.type === "rebound"; });
      const trend = sigs.filter(function (s) { return s.type !== "rebound"; });
      let html = "";
      if (rebound.length) html += '<div class="scan-group-title">超跌反弹 · 抄底（' + rebound.length + '）</div>' + rebound.map(itemHtml).join("");
      if (trend.length) html += '<div class="scan-group-title">趋势机会 · 追涨（' + trend.length + '）</div>' + trend.map(itemHtml).join("");
      listEl.innerHTML = html;
      listEl.querySelectorAll(".scan-item").forEach(function (el) {
        el.addEventListener("click", function () { selectStock(el.getAttribute("data-code")); });
      });
    }).catch(function () { if (infoEl) infoEl.textContent = "加载失败"; });
  }

  // ---------- 模拟持仓 ----------
  function renderPaper() {
    const listEl = document.getElementById("paper-list");
    const sumEl = document.getElementById("paper-summary");
    const infoEl = document.getElementById("paper-info");
    if (!listEl) return;
    fetch("/api/paper").then(r => r.json()).then(function (d) {
      const sum = d.summary || {};
      const hold = d.holdings || [];
      const closed = d.closed || [];
      if (infoEl) infoEl.textContent = "持仓 " + sum.holding + " · 已卖 " + sum.closed + " · 胜率 " + (sum.wins || 0) + "胜";
      if (sumEl) {
        sumEl.innerHTML =
          '<div class="paper-s-card"><b>' + (sum.total || 0) + '</b><span>总票数</span></div>' +
          '<div class="paper-s-card"><b>' + (sum.holding || 0) + '</b><span>持仓中</span></div>' +
          '<div class="paper-s-card"><b>' + (sum.closed || 0) + '</b><span>已卖出</span></div>' +
          '<div class="paper-s-card ' + (sum.realized_ret_pct < 0 ? "neg" : "pos") + '"><b>' + (sum.realized_ret_pct == null ? "0" : sum.realized_ret_pct) + '%</b><span>已实现收益</span></div>';
      }
      function rowHtml(h) {
        const cur = h.cur_price || h.entry_price || 0;
        const ret = h.entry_price ? (cur / h.entry_price - 1) * 100 : 0;
        const isR = h.type === "rebound";
        const c = ret >= 0 ? "pos" : "neg";
        const stText = h.status === "holding" ? "持有" : (h.reason || "已离场");
        return '<div class="scan-item" data-code="' + h.code + '" title="点击查看图形">' +
          '<div class="scan-item-head"><span class="scan-type ' + (isR ? "reb" : "trd") + '">' + (isR ? "抄底" : "趋势") + '</span>' +
          '<span class="scan-name">' + h.name + ' <em>' + h.code + '</em></span>' +
          '<span class="scan-level ' + c + '">' + (ret >= 0 ? "+" : "") + ret.toFixed(1) + '%</span></div>' +
          '<div class="scan-item-sub">成本 ' + h.entry_price + ' · 现 ' + cur + ' · 第' + (h.days || 0) + '日 · ' + stText + '</div>' +
          '<div class="scan-tags">' + (h.tags || []).map(function (t) { return "<span>" + t + "</span>"; }).join("") + '</div>' +
          '</div>';
      }
      const holdingHtml = hold.map(rowHtml).join("");
      const closedHtml = closed.map(rowHtml).join("");
      listEl.innerHTML = (hold.length ? '<div class="scan-group-title">持仓中（' + hold.length + '）</div>' + holdingHtml : "") +
        (closed.length ? '<div class="scan-group-title">已卖出（' + closed.length + '）</div>' + closedHtml : "") ||
        '<div class="scan-empty">暂无模拟持仓<br>可在「今日机会」扫描后使用重新建仓</div>';
      listEl.querySelectorAll(".scan-item").forEach(function (el) {
        el.addEventListener("click", function () { selectStock(el.getAttribute("data-code")); });
      });
    }).catch(function () { if (infoEl) infoEl.textContent = "加载失败"; });
  }
  document.getElementById("paper-refresh").addEventListener("click", function () {
    const infoEl = document.getElementById("paper-info");
    if (infoEl) infoEl.textContent = "更新中…";
    fetch("/api/paper/refresh", { method: "POST" }).then(r => r.json()).then(function (d) {
      if (infoEl) infoEl.textContent = "已更新 · 今日离场 " + ((d.closed_today || []).length) + " 只";
      renderPaper();
    }).catch(function () { if (infoEl) infoEl.textContent = "更新失败"; });
  });

  // ---------- 我的持仓 ----------
  function renderPositions() {
    const listEl = document.getElementById("pos-list");
    const sumEl = document.getElementById("pos-summary");
    const infoEl = document.getElementById("pos-info");
    if (!listEl) return;
    fetch("/api/positions").then(r => r.json()).then(function (d) {
      const sum = d.summary || {};
      const ps = d.positions || [];
      if (infoEl) infoEl.textContent = "持仓 " + sum.total + " 只 · 盈利 " + sum.wins + " · 建议卖出 " + sum.sell_count;
      if (sumEl) {
        sumEl.innerHTML =
          '<div class="paper-s-card"><b>' + (sum.total || 0) + '</b><span>持仓数</span></div>' +
          '<div class="paper-s-card"><b>' + (sum.wins || 0) + '</b><span>盈利中</span></div>' +
          '<div class="paper-s-card ' + (sum.sell_count > 0 ? "neg" : "") + '"><b>' + (sum.sell_count || 0) + '</b><span>建议卖出</span></div>';
      }
      function rowHtml(p) {
        const ret = p.ret_pct == null ? "--" : (p.ret_pct >= 0 ? "+" : "") + p.ret_pct + "%";
        const c = p.ret_pct >= 0 ? "pos" : "neg";
        const advC = p.advice === "建议止损" ? "exit-stop" : p.advice === "建议离场" ? "exit-watch" : "";
        const advHtml = p.advice && p.advice !== "持有" ? '<span class="ex-badge" style="background:rgba(229,72,77,.28);color:#ff6b6b">' + p.advice + '</span>' : '';
        return '<div class="scan-item" data-code="' + p.code + '" title="点击查看图形">' +
          '<div class="scan-item-head"><span class="scan-name">' + (p.name || p.code) + ' <em>' + p.code + '</em></span>' +
          '<span class="scan-level ' + c + '">' + ret + '</span></div>' +
          '<div class="scan-item-sub">买入 ' + (p.buy_date || "--") + ' @' + p.buy_price + (p.qty ? ' ×' + p.qty : "") + ' · 现 ' + (p.cur_price || "--") + '</div>' +
          '<div class="scan-tags">' + advHtml + (p.note ? '<span>' + p.note + '</span>' : '<span>持有中</span>') + '</div>' +
          '<span class="pos-del" data-code="' + p.code + '" title="删除持仓">✕</span>' +
          '</div>';
      }
      listEl.innerHTML = ps.length ? ps.map(rowHtml).join("") : '<div class="scan-empty">尚未登记持仓<br>在上方输入代码、买入日期/价格后点「＋登记」</div>';
      listEl.querySelectorAll(".scan-item").forEach(function (el) {
        el.addEventListener("click", function (ev) {
          if (ev.target.classList.contains("pos-del")) return;
          selectStock(el.getAttribute("data-code"));
        });
      });
      listEl.querySelectorAll(".pos-del").forEach(function (el) {
        el.addEventListener("click", function (ev) {
          ev.stopPropagation();
          const code = el.getAttribute("data-code");
          fetch("/api/positions/" + code, { method: "DELETE" }).then(function () { renderPositions(); });
        });
      });
    }).catch(function () { if (infoEl) infoEl.textContent = "加载失败"; });
  }
  // 登记持仓
  function addPos() {
    const code = (document.getElementById("pos-code").value || "").trim();
    const date = document.getElementById("pos-date").value || "";
    const price = parseFloat(document.getElementById("pos-price").value);
    const qty = parseInt(document.getElementById("pos-qty").value) || 0;
    if (!code) { alert("请输入股票代码或名称"); return; }
    if (!date) { alert("请选择买入日期"); return; }
    if (!price || isNaN(price) || price <= 0) { alert("请输入买入价"); return; }
    fetch("/api/positions", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: code, buy_date: date, buy_price: price, qty: qty })
    }).then(r => r.json()).then(function (d) {
      if (d.ok) {
        document.getElementById("pos-code").value = "";
        document.getElementById("pos-price").value = "";
        document.getElementById("pos-qty").value = "";
        renderPositions();
      } else { alert(d.msg || "登记失败"); }
    }).catch(function () { alert("登记失败"); });
  }
  document.getElementById("pos-add-btn").addEventListener("click", addPos);
  document.getElementById("pos-refresh").addEventListener("click", function () {
    const infoEl = document.getElementById("pos-info");
    if (infoEl) infoEl.textContent = "刷新中…";
    renderPositions();
  });
  // 买入日期默认今天
  (function () {
    const dt = document.getElementById("pos-date");
    if (dt) {
      const d = new Date();
      dt.value = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
    }
  })();

  // ---------- 推送设置弹窗 ----------
  function cfgVal(id) {
    const el = document.getElementById(id);
    return el ? el.value.trim() : "";
  }
  function openConfig() {
    fetch("/api/config").then(r => r.json()).then(function (c) {
      const w = c.wechat || {}, sms = c.sms || {};
      const on = document.getElementById("cfg-wechat-on");
      const prov = document.getElementById("cfg-provider");
      const tok = document.getElementById("cfg-wechat-token");
      const smsOn = document.getElementById("cfg-sms-on");
      if (on) on.checked = !!w.enabled;
      if (prov) prov.value = w.provider || "serverchan";
      if (tok) tok.value = w.token || "";
      if (smsOn) smsOn.checked = !!sms.enabled;
      ["cfg-sms-phone", "cfg-sms-key", "cfg-sms-secret", "cfg-sms-sign", "cfg-sms-template"].forEach(function (id) {
        const e = document.getElementById(id);
        const k = id.replace("cfg-sms-", "");
        if (e && sms[k] !== undefined) e.value = sms[k];
      });
      const m = c.market || {};
      const mm = document.getElementById("cfg-market-mode");
      if (mm) mm.value = m.mode || "auto";
      const mt = document.getElementById("cfg-market-threshold");
      if (mt) mt.value = (m.threshold !== undefined ? m.threshold : 4);
      const mp = document.getElementById("cfg-market-minpos");
      if (mp) mp.value = (m.min_pos !== undefined ? m.min_pos : 30);
      const pt = document.getElementById("cfg-pool-threshold");
      if (pt) pt.value = (m.pool_threshold !== undefined ? m.pool_threshold : 100);
      // 显示当前环境评分
      fetch("/api/env").then(r => r.json()).then(function (ev) {
        const badge = document.getElementById("cfg-env-score");
        const note = document.getElementById("cfg-env-note");
        if (badge && ev.score !== undefined && ev.score !== null) {
          badge.textContent = ev.score + " / 6";
          badge.className = "env-badge" + (ev.score >= ev.threshold ? " hi" : " lo");
        }
        if (note) note.textContent = (ev.note || "");
        // 离场策略（按牛熊模式）
        const es = document.getElementById("cfg-exit-style");
        if (es) {
          if (ev && ev.det && ev.det.score !== undefined) {
            const style = ev.style || (ev.mode === "bull" ? "hold" : (ev.mode === "bear" ? "exit" : (ev.score >= ev.threshold ? "hold" : "exit")));
            es.innerHTML = (style === "exit" ? "<b>破线即走</b>（及时止盈·控回撤，熊市/震荡更稳）" : "<b>双确认</b>（破MA20+大盘转弱才离场·让利润奔跑，牛市更优）") +
              "<br><span style='font-size:11px;color:var(--muted)'>" + (ev.note || "") + "</span>";
          } else {
            es.textContent = "数据不足";
          }
        }
      }).catch(function () {});
      // 高适配池信息
      fetch("/api/pool/info").then(r => r.json()).then(function (pi) {
        const sel = document.getElementById("cfg-pool-threshold");
        const cnt = document.getElementById("cfg-pool-count");
        const info = document.getElementById("cfg-pool-info");
        function updCount() {
          if (cnt && pi && pi.cand_counts) {
            cnt.textContent = "候选：" + (pi.cand_counts[sel.value] || "-") + " 只";
          }
        }
        if (sel && pi && pi.cand_counts) {
          updCount();
          sel.addEventListener("change", updCount);
        }
        if (info && pi) info.textContent = "当前池 " + pi.current_pool + " 只（门槛" + pi.threshold + "亿）";
      }).catch(function () {});
    }).catch(function () {});
    document.getElementById("cfg-modal").style.display = "flex";
  }
  function closeConfig() {
    document.getElementById("cfg-modal").style.display = "none";
  }
  function saveConfig() {
    const body = {
      wechat: {
        enabled: document.getElementById("cfg-wechat-on").checked,
        provider: document.getElementById("cfg-provider").value,
        token: document.getElementById("cfg-wechat-token").value.trim()
      },
      sms: {
        enabled: document.getElementById("cfg-sms-on").checked,
        phone: cfgVal("cfg-sms-phone"),
        access_key: cfgVal("cfg-sms-key"),
        access_secret: cfgVal("cfg-sms-secret"),
        sign_name: cfgVal("cfg-sms-sign"),
        template: cfgVal("cfg-sms-template")
      },
      market: {
        mode: document.getElementById("cfg-market-mode").value,
        threshold: parseFloat(document.getElementById("cfg-market-threshold").value) || 4,
        min_pos: parseFloat(document.getElementById("cfg-market-minpos").value) || 30,
        pool_threshold: parseInt(document.getElementById("cfg-pool-threshold").value, 10) || 100
      }
    };
    fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(r => r.json()).then(function () { alert("设置已保存"); })
      .catch(function () { alert("保存失败"); });
  }
  // 重建高适配池（动态更新）
  document.getElementById("cfg-pool-build").addEventListener("click", function () {
    const thr = parseInt(document.getElementById("cfg-pool-threshold").value, 10) || 100;
    const btn = document.getElementById("cfg-pool-build");
    const prog = document.getElementById("cfg-pool-progress");
    btn.disabled = true;
    prog.textContent = "正在重建池子（拉取K线评估流动性/波动，约1-3分钟）…";
    // 先保存门槛配置，再触发重建
    saveConfigQuiet();
    fetch("/api/pool/build", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ threshold: thr }) })
      .then(r => r.json()).then(function (d) {
        if (d && d.ok) {
          prog.innerHTML = "完成：池子 " + d.pool_size + " 只（跳过" + d.skipped + "），行业指数 " + d.ind_count + " 个，耗时 " + d.elapsed + " 秒。" +
            "请到「今日机会」点「▶ 扫描」重新扫描。";
          const info = document.getElementById("cfg-pool-info");
          if (info) info.textContent = "当前池 " + d.pool_size + " 只（门槛" + d.threshold + "亿）";
        } else {
          prog.textContent = "失败：" + ((d && d.msg) || "未知错误");
        }
      }).catch(function (e) { prog.textContent = "请求失败：" + e; })
      .finally(function () { btn.disabled = false; });
  });
  function saveConfigQuiet() {
    const body = {
      market: { pool_threshold: parseInt(document.getElementById("cfg-pool-threshold").value, 10) || 100 }
    };
    fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .catch(function () {});
  }
  document.getElementById("cfg-test-btn").addEventListener("click", function () {
    saveConfig();
    fetch("/api/config/test", { method: "POST" }).then(r => r.json()).then(function (res) {
      alert("微信: " + (res.wechat || "发送中") + "\n短信: " + (res.sms || "未启用"));
    }).catch(function () { alert("测试请求失败"); });
  });
  document.getElementById("cfg-save-btn").addEventListener("click", saveConfig);
  window.__openConfig = openConfig;
  window.__closeConfig = closeConfig;

  // ---------- 在线更新 ----------
  function updShow(txt, more) {
    document.getElementById("upd-result").innerHTML = '<div style="font-size:12.5px;line-height:1.9;color:var(--text)">' + txt + '</div>' +
      (more || "");
    document.getElementById("update-modal").style.display = "flex";
  }
  function updHide() {
    document.getElementById("update-modal").style.display = "none";
  }
  function checkUpdate(silent) {
    document.getElementById("upd-cur").textContent = "检查中…";
    fetchTimeout("/api/update/check", {}, 40000).then(function (r) { return r.json(); }).then(function (d) {
      const curEl = document.getElementById("upd-cur");
      if (d && d.ok) {
        curEl.textContent = d.current || "-";
        if (d.has_update) {
          updShow("发现新版本 <b>" + d.latest + "</b>（当前 " + d.current + "）" +
            (d.note ? '<br><br>更新说明：' + d.note : ""),
            '<div style="margin-top:10px;font-size:11px;color:var(--text-dim)">更新只替换代码文件，你的自选股、机会数据、推送设置都会保留。</div>');
          const go = document.getElementById("upd-go-btn");
          go.style.display = "";
          go.onclick = function () {
            go.disabled = true; go.textContent = "正在提交…";
            fetch("/api/update/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ download: d.download }) })
              .then(function (r) { return r.json(); })
              .then(function (r2) {
                if (r2 && r2.started) {
                  updShow("正在更新…<br><br><span id='upd-prog' style='color:var(--accent)'>准备中…</span>" +
                    "<div style='margin-top:10px;font-size:11px;color:var(--text-dim)'>更新在后台进行，页面不会被卡住；完成后按提示重启软件即可。请勿在更新期间关闭页面。</div>");
                  go.style.display = "none";
                  var poll = setInterval(function () {
                    fetch("/api/update/progress").then(function (r) { return r.json(); }).then(function (st) {
                      var p = document.getElementById("upd-prog");
                      if (!p) { clearInterval(poll); return; }
                      if (st.state === "done") {
                        clearInterval(poll);
                        updShow("✅ " + st.msg + "<br><br>请<b>关闭软件窗口后重新双击启动</b>，即可使用最新版。");
                      } else if (st.state === "error") {
                        clearInterval(poll);
                        updShow("更新失败：" + st.msg + "<br><br>可稍后重试或检查网络。");
                        go.style.display = ""; go.disabled = false; go.textContent = "重新更新";
                      } else {
                        p.textContent = st.msg;
                      }
                    }).catch(function () {});
                  }, 2000);
                } else {
                  updShow("更新失败：" + ((r2 && r2.msg) || "未知错误"));
                  go.disabled = false; go.textContent = "立即更新";
                }
              }).catch(function () { updShow("更新请求失败，请检查网络"); go.disabled = false; go.textContent = "立即更新"; });
          };
        } else {
          if (!silent) updShow("已是最新版本（v" + d.current + "）。");
        }
      } else {
        curEl.textContent = "-";
        if (!silent) updShow((d && d.msg) || "检查更新失败（未配置更新源或网络异常）。");
      }
    }).catch(function () {
      if (!silent) updShow("检查更新失败（网络异常）。");
    });
  }
  window.__checkUpdate = function () { checkUpdate(false); };
  window.__closeUpdate = updHide;
  // 启动静默检查：有新版才提示，避免打扰
  setTimeout(function () { checkUpdate(true); }, 4000);

  // ---------- 顶部市场模式切换（常驻可见，一键牛熊） ----------
  function loadModeSwitch() {
    const sel = document.getElementById("ms-mode");
    const sc = document.getElementById("ms-score");
    if (!sel) return;
    fetch("/api/env").then(r => r.json()).then(function (ev) {
      if (ev && ev.score !== undefined && ev.score !== null) {
        if (sc) {
          sc.textContent = ev.score + "/6";
          sc.className = "env-badge" + (ev.score >= ev.threshold ? " hi" : " lo");
          sc.title = "市场环境评分（6分制）";
        }
        sel.value = ev.mode || "auto";
      } else if (sc) {
        sc.textContent = "--";
      }
    }).catch(function () {});
    sel.addEventListener("change", function () {
      const label = { auto: "自动", bull: "进取·牛市", bear: "稳健·熊市" }[sel.value] || sel.value;
      fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ market: { mode: sel.value } }) })
        .then(r => r.json()).then(function () {
          showStatus("市场模式已切换为「" + label + "」，请到「今日机会」重新扫描");
          loadModeSwitch();
        }).catch(function () { alert("切换失败，请重试"); });
    });
  }

  // ---------- 初始化 ----------
  // 窗口resize防抖：避免频繁重绘导致卡顿
  let resizeTimer = null;
  window.addEventListener("resize", function () {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      if (charts.kline) charts.kline.resize();
      if (charts.fund) charts.fund.resize();
      if (charts.fundamental) charts.fundamental.resize();
    }, 200);
  });
  loadWatchlist();
  loadModeSwitch();
  // 自动选中第一只自选股
  setTimeout(async () => {
    const res = await fetch("/api/watchlist");
    const data = await res.json();
    if ((data.items || []).length) selectStock(data.items[0].code);
  }, 300);
})();

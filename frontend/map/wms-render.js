// ==================== Markdown 解析 + UI组件 + 代码高亮 ====================

// ★ 功能区: 地图/影像 (map/) | OpenLayers 地图容器 + WMS 影像 + Markdown 表格/图表渲染
// ★ 改造 (v3.0): 卡片墙 (flex 堆叠 + CSS transform 缩放) → OpenLayers 地图容器 (图层叠加 + 地理缩放)
//   每张影像/结果变成一个地图图层, 原图与掩膜通过图层透明度叠加 (替代旧 overlay 混色图)

const md = window.markdownit({ html: false, breaks: true, linkify: true, typographer: true });

function enhanceMarkdownTables(container) {
  const tables = container.querySelectorAll('table:not(.rich-table)');
  tables.forEach(table => {
    let title = '';
    let prev = table.previousElementSibling;
    while (prev) {
      if (/^H[1-6]$/.test(prev.tagName)) { title = prev.textContent.trim(); prev.remove(); break; }
      if (prev.tagName === 'P' && prev.textContent.trim()) break;
      if (prev.classList && prev.classList.contains('rich-table-card')) break;
      prev = prev.previousElementSibling;
    }
    const headers = [];
    table.querySelectorAll('thead th').forEach(th => headers.push(th.textContent.trim()));
    const rows = [];
    table.querySelectorAll('tbody tr').forEach(tr => {
      const row = [];
      tr.querySelectorAll('td').forEach(td => row.push(td.textContent.trim()));
      rows.push(row);
    });
    if (headers.length === 0 && rows.length > 0) {
      const firstRow = rows.shift();
      firstRow.forEach(cell => headers.push(cell));
    }
    if (rows.length === 0) {
      table.replaceWith(buildEmptyRichTable(title));
    } else {
      table.replaceWith(buildRichTable(title, headers, rows));
    }
  });
}

function buildEmptyRichTable(title) {
  const card = document.createElement('div');
  card.className = 'rich-table-card';
  card.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:var(--fs-lg);">(空表)</div>`;
  return card;
}

// ★ 复制表格到剪贴板 (TSV 格式, 可直接粘贴到 Excel)
function copyTable(btn, title, headers, rows) {
  const tsv = [headers.join('\t'), ...rows.map(row => row.map(c => (c ?? '').toString()).join('\t'))].join('\n');
  const checkSVG = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
  const copySVG = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  navigator.clipboard.writeText(tsv).then(() => {
    btn.innerHTML = checkSVG;
    btn.classList.add('copied');
    setTimeout(() => { btn.innerHTML = copySVG; btn.classList.remove('copied'); }, 2000);
  }).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = tsv; ta.style.position = 'fixed'; ta.style.left = '-9999px';
    document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
    btn.innerHTML = checkSVG;
    btn.classList.add('copied');
    setTimeout(() => { btn.innerHTML = copySVG; btn.classList.remove('copied'); }, 2000);
  });
}

function buildRichTable(title, headers, rows) {
  const card = document.createElement('div');
  card.className = 'rich-table-card';
  // ★ 表头: 标题 + 复制按钮
  if (title) {
    const header = document.createElement('div');
    header.className = 'rich-table-header';
    header.innerHTML = '<span>' + escapeHtml(title) + '</span>';
    const copyBtn = document.createElement('button');
    copyBtn.className = 'rich-table-copy-btn';
    copyBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
    copyBtn.title = '复制表格';
    copyBtn.onclick = function() { copyTable(copyBtn, title, headers, rows); };
    header.appendChild(copyBtn);
    card.appendChild(header);
  }
  const scroll = document.createElement('div');
  scroll.className = 'rich-table-scroll';
  const tableEl = document.createElement('table');
  tableEl.className = 'rich-table';
  let sortState = { col: -1, asc: true };
  const thead = document.createElement('thead');
  const headerRow = document.createElement('tr');
  headers.forEach((h, i) => {
    const th = document.createElement('th');
    th.innerHTML = `${escapeHtml(h)} <span class="sort-icon"></span>`;
    th.addEventListener('click', () => {
      if (sortState.col === i) { sortState.asc = !sortState.asc; }
      else { sortState.col = i; sortState.asc = true; }
      headerRow.querySelectorAll('th').forEach((el, j) => {
        el.classList.remove('sorted-asc', 'sorted-desc');
        el.querySelector('.sort-icon').textContent = '';
        if (j === i) {
          el.classList.add(sortState.asc ? 'sorted-asc' : 'sorted-desc');
          el.querySelector('.sort-icon').textContent = sortState.asc ? '▲' : '▼';
        }
      });
      renderRichTableBody(tableEl, headers, rows, sortState);
    });
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);
  tableEl.appendChild(thead);
  renderRichTableBody(tableEl, headers, rows, sortState);
  scroll.appendChild(tableEl);
  card.appendChild(scroll);
  const footer = document.createElement('div');
  footer.className = 'rich-table-footer';
  footer.innerHTML = `<span>共 ${rows.length} 条记录</span>`;
  card.appendChild(footer);
  return card;
}

function renderRichTableBody(tableEl, headers, rows, sortState) {
  const oldTbody = tableEl.querySelector('tbody');
  if (oldTbody) oldTbody.remove();
  let sorted = [...rows];
  if (sortState.col >= 0 && sortState.col < headers.length) {
    sorted.sort((a, b) => {
      const va = (a[sortState.col] || '').toString();
      const vb = (b[sortState.col] || '').toString();
      const na = parseFloat(va), nb = parseFloat(vb);
      if (!isNaN(na) && !isNaN(nb)) return sortState.asc ? na - nb : nb - na;
      return sortState.asc ? va.localeCompare(vb, 'zh-CN') : vb.localeCompare(va, 'zh-CN');
    });
  }
  const tbody = document.createElement('tbody');
  sorted.forEach(row => {
    const tr = document.createElement('tr');
    row.forEach(cell => {
      const td = document.createElement('td');
      if (cell === null || cell === undefined || cell === 'NULL' || cell === 'None') {
        td.innerHTML = '<span class="cell-null">—</span>';
      } else {
        const str = String(cell);
        td.textContent = str.length > 60 ? str.slice(0, 60) + '…' : str;
        if (!isNaN(parseFloat(str.replace(/,/g, ''))) && isFinite(parseFloat(str.replace(/,/g, '')))) {
          td.classList.add('cell-number');
        }
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  tableEl.appendChild(tbody);
}

// ==================== 图表渲染 (Canvas, 不走地图) ====================

function _cssVar(name, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch (e) { return fallback; }
}

function renderChart(container, data) {
  const { chart_type, title, data: chartData } = data;
  const canvas = document.createElement('canvas');
  container.appendChild(canvas);
  const dpr = window.devicePixelRatio || 1;
  const w = container.offsetWidth || 400;
  const h = 180;
  canvas.width = w * dpr; canvas.height = h * dpr;
  canvas.style.width = w + 'px'; canvas.style.height = h + 'px';
  const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr);
  const cats = chartData.categories || [];
  const vals = chartData.values || [];
  const maxVal = Math.max(...vals, 1);
  // 图表系列色取自 map 色板 (--map-chart-1..6); 每色保留原值兜底, 防 colors.css 未就绪时失色
  const colors = [
    _cssVar('--map-chart-1', '#4d6bfe'), _cssVar('--map-chart-2', '#10b981'),
    _cssVar('--map-chart-3', '#f59e0b'), _cssVar('--map-chart-4', '#ef4444'),
    _cssVar('--map-chart-5', '#8b5cf6'), _cssVar('--map-chart-6', '#ec4899'),
  ];
  const pad = { top: 30, right: 16, bottom: 30, left: 40 };
  const chartW = w - pad.left - pad.right;
  const chartH = h - pad.top - pad.bottom;
  ctx.fillStyle = _cssVar('--text-primary', '#1a1a1a'); ctx.font = 'bold 13px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText(title || chart_type, w / 2, 18);
  if (chart_type === 'pie') {
    const total = vals.reduce((a, b) => a + b, 0);
    const cx = w / 2, cy = pad.top + chartH / 2, r = Math.min(chartW, chartH) / 2 - 10;
    let startAngle = -Math.PI / 2;
    vals.forEach((v, i) => {
      const slice = (v / total) * Math.PI * 2;
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.arc(cx, cy, r, startAngle, startAngle + slice);
      ctx.fillStyle = colors[i % colors.length]; ctx.fill();
      const mid = startAngle + slice / 2;
      const lx = cx + (r + 14) * Math.cos(mid), ly = cy + (r + 14) * Math.sin(mid);
      ctx.fillStyle = _cssVar('--text-secondary', '#6b7280'); ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText(cats[i] || '', lx, ly + 3);
      startAngle += slice;
    });
  } else {
    const barW = chartW / cats.length;
    ctx.strokeStyle = _cssVar('--border-strong', '#c9cdd4'); ctx.beginPath(); ctx.moveTo(pad.left, pad.top); ctx.lineTo(pad.left, pad.top + chartH); ctx.stroke();
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + chartH - (chartH * i / 4);
      ctx.strokeStyle = _cssVar('--border', '#e3e3e6'); ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(w - pad.right, y); ctx.stroke();
      ctx.fillStyle = _cssVar('--text-muted', '#9ca3af'); ctx.font = '10px sans-serif'; ctx.textAlign = 'right';
      ctx.fillText(Math.round(maxVal * i / 4), pad.left - 6, y + 3);
    }
    if (chart_type === 'line' || chart_type === 'scatter') {
      ctx.beginPath(); ctx.strokeStyle = _cssVar('--accent', '#4d6bfe'); ctx.lineWidth = 2;
      vals.forEach((v, i) => {
        const x = pad.left + barW * i + barW / 2, y = pad.top + chartH - (v / maxVal) * chartH;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
      vals.forEach((v, i) => {
        const x = pad.left + barW * i + barW / 2, y = pad.top + chartH - (v / maxVal) * chartH;
        ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fillStyle = _cssVar('--accent', '#4d6bfe'); ctx.fill();
      });
    } else {
      vals.forEach((v, i) => {
        const x = pad.left + barW * i + barW * 0.15, bw = barW * 0.7;
        const bh = (v / maxVal) * chartH, y = pad.top + chartH - bh;
        ctx.fillStyle = colors[i % colors.length]; ctx.fillRect(x, y, bw, bh);
      });
    }
    ctx.fillStyle = _cssVar('--text-secondary', '#6b7280'); ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
    cats.forEach((c, i) => { ctx.fillText(c, pad.left + barW * i + barW / 2, pad.top + chartH + 16); });
  }
  return { chart_type, title, rendered: true, data_points: vals.length };
}

function renderPanel(container, data) {
  const { panel_name, action } = data;
  const wrapper = document.createElement('div'); wrapper.className = 'panel-mock';
  const title = document.createElement('div'); title.style.cssText = 'margin-bottom:8px;color:var(--accent-green);font-weight:bold;font-family:var(--mono);'; title.textContent = `${panel_name} 面板`; wrapper.appendChild(title);
  if (action === 'hide') { const status = document.createElement('div'); status.style.color = 'var(--accent-red)'; status.textContent = '状态: 已隐藏'; wrapper.appendChild(status); container.appendChild(wrapper); return { panel_name, action, status: 'success' }; }
  const rows = 3 + Math.floor(Math.random() * 5), cols = 2 + Math.floor(Math.random() * 4);
  const headers = ['ID', '名称', '数值', '状态', '日期', '备注'].slice(0, cols);
  const statuses = ['正常', '警告', '异常', '正常', '正常', '离线'];
  const cellW = 100, cellH = 32, pad = 12;
  const cw = pad * 2 + cols * cellW, ch = pad * 2 + (rows + 1) * cellH;
  const canvas = document.createElement('canvas'); canvas.style.cssText = 'display:block;border-radius:4px;max-width:100%;'; wrapper.appendChild(canvas); container.appendChild(wrapper);
  requestAnimationFrame(() => {
    const dpr = window.devicePixelRatio || 1; canvas.width = cw * dpr; canvas.height = ch * dpr; canvas.style.width = cw + 'px'; canvas.style.height = ch + 'px';
    const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr);
    ctx.fillStyle = _cssVar('--bg-panel', '#f7f7f8'); ctx.fillRect(pad, pad, cols * cellW, cellH);
    for (let r = 0; r < rows; r++) { ctx.fillStyle = r % 2 === 0 ? '#ffffff' : _cssVar('--bg-inset', '#f7f7f8'); ctx.fillRect(pad, pad + (r + 1) * cellH, cols * cellW, cellH); }
    ctx.strokeStyle = _cssVar('--border', '#e3e3e6'); ctx.lineWidth = 0.5;
    for (let r = 0; r <= rows + 1; r++) { ctx.beginPath(); ctx.moveTo(pad, pad + r * cellH); ctx.lineTo(pad + cols * cellW, pad + r * cellH); ctx.stroke(); }
    for (let c = 0; c <= cols; c++) { ctx.beginPath(); ctx.moveTo(pad + c * cellW, pad); ctx.lineTo(pad + c * cellW, pad + (rows + 1) * cellH); ctx.stroke(); }
    ctx.fillStyle = _cssVar('--text-primary', '#1a1a1a'); ctx.font = 'bold 12px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    for (let c = 0; c < cols; c++) { ctx.fillText(headers[c], pad + c * cellW + cellW / 2, pad + cellH / 2); }
    ctx.fillStyle = _cssVar('--text-primary', '#1a1a1a'); ctx.font = '12px sans-serif';
    for (let r = 0; r < rows; r++) {
      const rowData = [String(r + 1), ['Alpha','Beta','Gamma','Delta','Epsilon','Zeta','Eta'][r % 7], (Math.random()*1000).toFixed(1), statuses[Math.floor(Math.random()*statuses.length)], `2026-03-${String(Math.floor(Math.random()*28)+1).padStart(2,'0')}`, '—'].slice(0, cols);
      for (let c = 0; c < cols; c++) {
        ctx.fillStyle = c===3&&rowData[c]==='异常' ? _cssVar('--accent-red','#ef4444')
                      : c===3&&rowData[c]==='警告' ? _cssVar('--accent-amber','#f59e0b')
                      : _cssVar('--text-primary', '#1a1a1a');
        ctx.fillText(rowData[c], pad+c*cellW+cellW/2, pad+(r+1.5)*cellH);
      }
    }
  });
  return { panel_name, action, status: 'success', rows, cols };
}

// ==================== 图层控制 / 下载 / 表格 (走地图或独立渲染) ====================

function renderLayerControl(container, data) {
  const { layer_name, action, workspace, wms_url, bbox } = data;
  // ★ container 可能为 null (前端操作静默执行, 不创建 DOM 卡片)
  if (container) {
    const actionMap = { show: '显示并定位', hide: '隐藏', toggle: '切换' };
    const el = document.createElement('div');
    el.innerHTML = `<div style="font-weight:600;margin-bottom:2px;color:var(--accent);">${escapeHtml(layer_name)}</div><div style="font-size:var(--fs-md);color:var(--text-secondary);">操作: ${actionMap[action]||action}${workspace ? ' · ' + workspace : ''}</div>`;
    container.appendChild(el);
  }
  if (action === 'show' || action === 'toggle') {
    // 返回 Promise: 等待 WMS 影像真实加载结果
    return showWmsInPanel(layer_name, workspace, bbox, wms_url, '显示图层 ' + layer_name).then(function(result) {
      return {
        layer_name: layer_name,
        action: action,
        status: result.loaded ? 'success' : 'error',
        visible: result.loaded,
        wms_loaded: result.loaded,
        error: result.error || null,
      };
    });
  }
  return Promise.resolve({ layer_name: layer_name, action: action, status: 'success', visible: action === 'show' });
}

function renderLayerDisplay(container, data) {
  const { layer_name, type, statistics, bbox, workspace, wms_url } = data;
  const el = document.createElement('div');
  if (statistics) {
    el.innerHTML = `<div style="font-weight:600;color:var(--accent);">${escapeHtml(layer_name)}</div>`
      + `<div style="font-size:var(--fs-md);color:var(--text-secondary);margin-top:2px;">类型: ${type} | 均值: ${statistics.mean_value} | 范围: ${statistics.min_value}~${statistics.max_value} | 样本: ${statistics.count}</div>`;
  } else {
    el.innerHTML = `<div>已加载图层: <span style="color:var(--accent)">${escapeHtml(layer_name)}</span></div>`;
  }
  container.appendChild(el);
  // 返回 Promise: 等待 WMS 影像真实加载结果
  return showWmsInPanel(layer_name, workspace, bbox, wms_url, (statistics ? '分析结果 ' : '加载图层 ') + layer_name).then(function(result) {
    return {
      layer_name: layer_name,
      status: result.loaded ? 'success' : 'error',
      loaded: result.loaded,
      wms_loaded: result.loaded,
      error: result.error || null,
    };
  });
}

function renderDownload(container, data) {
  const { layer_name, file_type, download_url, file_size } = data;
  // ★ container 可能为 null (executeFrontendAction 静默派发 download 动作时传 null)。
  //   DOM 卡片只在容器存在时创建; 下载本身不依赖容器。
  const el = document.createElement('div');
  if (download_url) {
    var sizeText = file_size ? ' (' + Math.round(file_size / 1024) + 'KB)' : '';
    el.innerHTML = `<div style="color:var(--accent-green);">下载完成</div><div style="margin-top:4px;color:var(--text-primary);font-size:var(--fs-md);">文件: ${escapeHtml(layer_name)}.${file_type||'tif'}${sizeText}</div>`;
    if (container) container.appendChild(el);
    // 触发浏览器下载
    var a = document.createElement('a');
    a.href = download_url;
    a.download = layer_name + '.' + (file_type || 'tif');
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(function() { document.body.removeChild(a); }, 100);
    return { layer_name, file_type, status: 'success', download_url: download_url };
  }
  el.innerHTML = `<div style="color:var(--accent-amber);">正在从 GeoServer 下载: ${escapeHtml(layer_name)}.${file_type||'tif'}</div>`;
  if (container) container.appendChild(el);
  return { layer_name, file_type, status: 'downloading' };
}

function renderTable(container, data) {
  const { table_name, headers, rows } = data;
  if (!rows || rows.length === 0) { const empty = buildEmptyRichTable(table_name); container.appendChild(empty); return { table_name, rows: 0, status: 'empty' }; }
  const richTable = buildRichTable(table_name, headers, rows); container.appendChild(richTable);
  return { table_name, rows: rows.length, cols: headers.length, status: 'success' };
}

// ★ SamSeg 分割/变化检测结果 → 地图容器加载
// 改造后: 原图作底图层 + 变化面(GeoServer WMS 或本地矢量) + 边缘线(GeoServer WMS 或本地矢量)。
//   不再使用 mask_tif_url 半透明栅格掩膜叠加 (已移除, TIF 仅保留为可下载产物)。
// 后端 params 提供: image_url(原图), polygon_layer(变化面), edge_layer(边缘线 WMS),
//                   edge_vector_url(边缘线本地兜底), input_image_path(查坐标), legend(图例), has_crs(CRS 判定)
// polygon_layer / edge_layer 形态: {layer_name,workspace,bbox,wms_url}(WMS) 或 {vector_url}(GeoServer不可用降级)
function renderImage(container, data) {
  // 入参:
  //   data: 遥感结果载荷, 含原图、变化面、边缘线与图例等地图渲染信息。
  // 方法:
  //   先把原图作为当前结果的底图加入地图, 再把变化面/边缘线按加载顺序叠加到其上;
  //   普通叠加不再强制改动用户视图, 只在首张结果图进入地图时允许一次定位。
  // 出参:
  //   返回 Promise<{status,loaded,error?...}>; loaded=true 表示该次结果图至少已成功进入地图。
  const { image_url, caption, legend, input_image_path, polygon_layer, edge_layer, edge_vector_url, has_crs, artifact_path, base_layer } = data;
  if (container) {
    const el = document.createElement('div');
    el.innerHTML = `<div style="color:var(--accent-green);">分析结果已生成</div>`
      + `<div style="margin-top:4px;font-size:var(--fs-md);color:var(--text-secondary);">${escapeHtml(caption || '遥感分析结果')}</div>`;
    container.appendChild(el);
  }

  // ★ Step 1: 原图作底图层 — 所有影像必须先上传 GeoServer, 再从图层面板定位加载
  //   方法: fetch bbox → showWmsInPanel (与 locateLayerFromPanel 一致)
  var basePromise;
  if (base_layer && base_layer.layer_name) {
    basePromise = fetch(
      '/api/v1/geoserver/layers/' + encodeURIComponent(base_layer.workspace) + '/' + encodeURIComponent(base_layer.layer_name) + '/bbox'
    ).then(function(r) { return r.json(); }).then(function(bboxData) {
      var bbox4326 = null;
      if (bboxData.status === 'success' && bboxData.bbox) {
        var b = bboxData.bbox;
        bbox4326 = [b.minx, b.miny, b.maxx, b.maxy];
      }
      return showWmsInPanel(
        base_layer.layer_name,
        base_layer.workspace,
        bbox4326,
        base_layer.wms_url,
        (caption || '遥感分析结果') + ' [WMS]',
        false,
        { forceFit: true }
      );
    });
  } else {
    basePromise = Promise.resolve({ loaded: false, error: '原始影像未发布到 GeoServer, 请先上传' });
  }

  // ★ Step 2: 变化面叠加 (矢量 → WFS GetFeature 加载)
  //   - polygon_layer.layer_name 存在 → 走 GeoServer WFS (showWfsInPanel)
  //   - polygon_layer.vector_url 存在 → 本地矢量兜底 (showVectorFile)
  if (polygon_layer) {
    if (polygon_layer.layer_name) {
      basePromise.then(function() {
        try {
          showWfsInPanel(
            polygon_layer.layer_name, polygon_layer.workspace,
            polygon_layer.bbox, polygon_layer.wms_url,
            (caption || '变化面') + ' [WFS]', true, { forceFit: false }
          );
        } catch (e) { console.debug('[WFS] 变化面加载失败:', e); }
      });
    } else if (polygon_layer.vector_url) {
      basePromise.then(function() {
        try { showVectorFile(polygon_layer.vector_url, (caption || '变化面') + ' [本地矢量]', true, { forceFit: false }); } catch (e) { console.debug('[Vector] 变化面降级加载失败:', e); }
      });
    }
  }

  // ★ Step 3: 边缘线叠加 (矢量 → WFS GetFeature 加载)
  //   - edge_layer.layer_name 存在 → 走 GeoServer WFS (showWfsInPanel)
  //   - edge_layer.vector_url 或 edge_vector_url 存在 → 本地矢量兜底 (showVectorFile)
  if (edge_layer && edge_layer.layer_name) {
    basePromise.then(function() {
      try {
        showWfsInPanel(
          edge_layer.layer_name, edge_layer.workspace,
          edge_layer.bbox, edge_layer.wms_url,
          (caption || '边缘线') + ' [WFS]', true, { forceFit: false }
        );
      } catch (e) { console.debug('[WFS] 边缘线加载失败:', e); }
    });
  } else {
    var edgeLocal = (edge_layer && edge_layer.vector_url) || edge_vector_url;
    if (edgeLocal) {
      basePromise.then(function() {
        try { showVectorFile(edgeLocal, (caption || '边缘线') + ' [矢量]', true, { forceFit: false }); } catch (e) { console.debug('[Vector] 边缘线加载失败:', e); }
      });
    }
  }

  // ★ Step 4: 图例渲染为地图右上角浮层
  if (legend && legend.length) {
    _renderLegendOverlay(legend, caption || '图例');
  }

  return basePromise.then(function(result) {
    return {
      image_url: image_url,
      polygon_layer: polygon_layer,
      edge_vector_url: edge_vector_url,
      status: result.loaded ? 'success' : 'error',
      loaded: result.loaded,
      error: result.error || null,
    };
  });
}

// ==================== OpenLayers 地图容器 (核心) ====================
// 单例 map, 所有影像/结果作为图层叠加。无 CRS 影像拒绝渲染 (系统只接受带坐标影像)。

let _map = null;                   // ol.Map 单例
let _imageMetaCache = {};          // url/path → {bbox, crs, width, height} 缓存
let _popupOverlay = null;          // GetFeatureInfo 弹窗 Overlay
const _LAYER_TAG = 'business';     // 业务图层标识 (用于 _clearMapLayers 区分底图)
let _isRestoringMapLayers = false; // 对话恢复期间禁止自动抢占视图
let _mapViewSyncBound = false;     // 地图视图变化事件仅绑定一次
const _TDT_KEY = '4431a9dc7a19f651fcef126ea62b6d24';
const _TDT_BASE_URL = 'https://t0.tianditu.gov.cn';
const _TDT_LEVELS = 18;

// ★ 图层注册表: layerKey → {layer, config}
//   统一管理所有业务图层的增删查, 替代散落的 _LAYER_TAG 标签遍历。
//   WMS: key = workspace:layerName, ImageStatic: key = img_ts, Vector: key = vec_ts
const _layerRegistry = {};

function _registerLayer(key, layer, config) {
  // ★ 兜底去重: 同 key 已存在时, 先把旧图层从地图移除再覆盖注册, 防止两个同名图层同时叠加
  //   (如不同入口对同一图层重复加载 → 旧 SLD 标注 + 新标注各画一次 name 字段 → 重影)。
  //   showWmsInPanel 入口已做主动去重, 这里是防御性二次清理, 覆盖任何旁路调用。
  var existing = _layerRegistry[key];
  if (existing && existing.layer && _map && existing.layer !== layer) {
    _map.removeLayer(existing.layer);
  }
  _layerRegistry[key] = { layer, config };
}

function _unregisterLayer(key) {
  delete _layerRegistry[key];
}

function _findLayerByKey(key) {
  var entry = _layerRegistry[key];
  return entry ? entry.layer : null;
}

function _getAllRegisteredLayers() {
  return Object.values(_layerRegistry).map(function(entry) { return entry.layer; });
}

function _getRegisteredCount() {
  return Object.keys(_layerRegistry).length;
}

function _createTdtLayer(layerName, tileMatrixSet, opacity) {
  // 入参:
  //   layerName: 天地图图层名, 如 img / cia.
  //   tileMatrixSet: 天地图矩阵集, 这里固定使用 w.
  //   opacity: 图层透明度, 底图与注记一般为 1.
  // 方法:
  //   构造天地图 WMTS 图层, 以 EPSG:3857 瓦片方式提供底图与中文注记。
  //   这样可以把原来空白的地图底座补回来, 业务 WMS 仍保持原有经纬度逻辑。
  // 出参:
  //   返回已配置好的 ol.layer.Tile 实例。
  var projection = ol.proj.get('EPSG:3857');
  var projectionExtent = projection.getExtent();
  var tileGrid = new ol.tilegrid.WMTS({
    origin: ol.extent.getTopLeft(projectionExtent),
    resolutions: Array.from({ length: _TDT_LEVELS }, function(_, z) {
      return ol.extent.getWidth(projectionExtent) / 256 / Math.pow(2, z);
    }),
    matrixIds: Array.from({ length: _TDT_LEVELS }, function(_, z) { return String(z); }),
  });
  var source = new ol.source.WMTS({
    url: _TDT_BASE_URL + '/' + layerName + '_' + tileMatrixSet + '/wmts?tk=' + encodeURIComponent(_TDT_KEY),
    layer: layerName,
    matrixSet: tileMatrixSet,
    format: 'tiles',
    style: 'default',
    wrapX: true,
    crossOrigin: 'anonymous',
    projection: 'EPSG:3857',
    tileGrid: tileGrid,
  });
  return new ol.layer.Tile({ source: source, opacity: opacity });
}

function _ensureBaseLayers() {
  // 入参:
  //   无。
  // 方法:
  //   在地图首次初始化后注入天地图影像底图和中文注记图层, 并标记为持久层。
  //   为什么: 当前原生前端只创建了空 layers, 导致地图看起来像“没显示”。
  // 出参:
  //   无; 仅在地图实例存在且底图尚未加入时生效。
  if (!_map) return;
  if (_findLayerByKey('tdt:img')) return;
  var baseLayer = _createTdtLayer('img', 'w', 1);
  var annoLayer = _createTdtLayer('cia', 'w', 1);
  baseLayer.setZIndex(0);
  annoLayer.setZIndex(1);
  _registerLayer('tdt:img', baseLayer, { type: 'base', persistent: true, provider: 'tianditu' });
  _registerLayer('tdt:cia', annoLayer, { type: 'base', persistent: true, provider: 'tianditu' });
  _map.addLayer(baseLayer);
  _map.addLayer(annoLayer);
}

// ★ 地图单例访问器 (供 layer-panel.js 等外部模块读写业务图层, 不直接暴露 _map 到 window)
//   返回当前 ol.Map 实例 (可能为 null, 调用方需自行判空)
function _getMap() { return _map; }

// 入参:
//   无显式入参, 读取当前活跃对话与地图视图实例。
// 方法:
//   把当前地图的中心点、缩放级别、旋转角同步回当前对话状态, 并持久化到 localStorage;
//   这样切换到图层面板、切回地图、切换对话后都能回到用户离开前的坐标状态。
// 出参:
//   无返回值; 成功时更新当前对话的 mapView 快照。
function _syncCurrentConvMapView() {
  if (!_map || typeof cs !== 'function') return;
  var st = cs();
  if (!st) return;
  var view = _map.getView();
  var center = view.getCenter();
  st.mapView = {
    center: center ? center.slice() : [0, 0],
    zoom: view.getZoom(),
    rotation: view.getRotation() || 0,
  };
  if (typeof _persistConvMapView === 'function' && activeConvId) {
    _persistConvMapView(activeConvId);
  }
}

// 入参:
//   convId: 需要恢复地图视图的对话 ID; 若该对话没有历史视图则按默认值回退。
// 方法:
//   将该对话保存的 center/zoom/rotation 应用到当前地图视图, 保证切换 Tab 或对话后坐标系不重置。
// 出参:
//   返回布尔值; true 表示已成功恢复历史视图, false 表示使用方仍可选择做首次定位。
function _restoreMapViewForConv(convId) {
  if (!_map || !convId || typeof convStates === 'undefined') return false;
  var st = convStates[convId];
  if (!st || !st.mapView || !st.mapView.center || typeof st.mapView.zoom !== 'number') return false;
  var view = _map.getView();
  view.setCenter(st.mapView.center.slice());
  view.setZoom(st.mapView.zoom);
  view.setRotation(st.mapView.rotation || 0);
  return true;
}

// 入参:
//   extentInViewProj: 已转换到地图视图投影的图层范围; forceFit 表示是否允许本次加载主动调整视图。
// 方法:
//   把“加载图层”和“移动地图”拆开:
//   仅在地图当前没有任何业务图层, 或调用方显式要求定位时才执行 fit;
//   普通叠加只把图层加到现有视图之上, 不打断用户当前浏览状态。
// 出参:
//   无返回值; 若满足条件则更新地图视图并同步当前对话的 mapView。
function _fitMapToExtent(extentInViewProj, forceFit) {
  if (!_map || !extentInViewProj || ol.extent.isEmpty(extentInViewProj)) return;
  if (_isRestoringMapLayers) return;
  // ★ 使用注册表计数 (O(1)), 替代旧标签遍历
  if (!forceFit && _getRegisteredCount() > 1) return;
  _map.getView().fit(extentInViewProj, { padding: [40, 40, 40, 40], maxZoom: 18, size: _map.getSize() });
  _syncCurrentConvMapView();
}

// 入参:
//   无显式入参, 直接依赖全局地图单例。
// 方法:
//   给地图视图绑定 center/zoom/rotation 的变更监听, 用户拖拽缩放后实时持久化当前坐标状态。
// 出参:
//   无返回值; 首次调用后地图视图变化会自动同步到当前对话状态。
function _bindMapViewStateSync() {
  if (!_map || _mapViewSyncBound) return;
  _mapViewSyncBound = true;
  var view = _map.getView();
  ['change:center', 'change:resolution', 'change:rotation'].forEach(function(evtName) {
    view.on(evtName, function() { _syncCurrentConvMapView(); });
  });
}

// 入参:
//   restoring: 布尔值, true 表示接下来会进行对话恢复式图层重建; false 表示恢复结束。
// 方法:
//   在批量恢复旧图层期间暂时关闭自动 fit, 防止恢复过程中的每个图层都抢一次视图。
// 出参:
//   无返回值; 成功时更新内部恢复标记。
function setMapRestoreMode(restoring) {
  _isRestoringMapLayers = restoring === true;
}

// 入参:
//   convId: 需要恢复地图状态的对话 ID。
// 方法:
//   先尝试按该对话的 mapView 恢复用户离开前的坐标系, 若无历史记录则保持当前地图默认状态不动。
// 出参:
//   返回布尔值; true 表示恢复成功, false 表示不存在历史视图。
function restoreMapViewForConv(convId) {
  return _restoreMapViewForConv(convId);
}

// ★ 叠加语义 (v2.6 重构):
//   不用 zIndex, 回归 OpenLayers 默认的"添加顺序=堆叠顺序": addLayer 后加的图层自然叠在上方。
//   用户诉求: 加载一个影像显示出来, 再加另一个数据自动叠到上方即可 —— 这正是默认行为。
//   视角保持: 仅在"地图当前无业务图层"时才 fit 定位; 已有图层叠加时不打断用户视角。

// ★ 生成"仅显示边界线(无填充)"的 SLD 样式
//   入参: layerName (string) - GeoServer 完整图层名 (如 cd_shp:chengduqu)
//         strokeColor (string) - 边界线和文字颜色 (CSS 色值, 如 #4d6bfe)
//   方法: 面透明填充 + 边界线描边 + name 字段文字标注 (白色光晕)
//   出参: SLD XML 字符串
function _makeBoundarySLD(layerName, strokeColor) {
  var c = strokeColor || '#2c6fbd';
  return '<?xml version="1.0" encoding="UTF-8"?>' +
    '<StyledLayerDescriptor version="1.0.0" xmlns="http://www.opengis.net/sld" xmlns:ogc="http://www.opengis.net/ogc">' +
    '<NamedLayer><Name>' + layerName + '</Name><UserStyle><FeatureTypeStyle><Rule>' +
    '<PolygonSymbolizer>' +
    '<Fill><CssParameter name="fill">#000000</CssParameter>' +
    '<CssParameter name="fill-opacity">0</CssParameter></Fill>' +
    '<Stroke><CssParameter name="stroke">' + c + '</CssParameter>' +
    '<CssParameter name="stroke-width">2</CssParameter></Stroke>' +
    '</PolygonSymbolizer>' +
    '<TextSymbolizer>' +
    '<Label><ogc:PropertyName>name</ogc:PropertyName></Label>' +
    '<Fill><CssParameter name="fill">' + c + '</CssParameter></Fill>' +
    '<Font><CssParameter name="font-family">Microsoft YaHei, sans-serif</CssParameter>' +
    '<CssParameter name="font-size">12</CssParameter></Font>' +
    '<Halo><Fill><CssParameter name="fill">#ffffff</CssParameter></Fill>' +
    '<Radius><ogc:Literal>1</ogc:Literal></Radius></Halo>' +
    '</TextSymbolizer>' +
    '</Rule></FeatureTypeStyle></UserStyle></NamedLayer></StyledLayerDescriptor>';
}

// ==================== 全局矢量主题色 SLD (所有 WMS 路径统一配色) ====================
// 设计: 矢量图层经 WMS 渲染时, 若不指定 SLD, GeoServer 回退内置灰色默认样式。
//   本组函数在 showWmsInPanel 内部统一兜底: 调用方未传 sldBody 时, 异步查图层类型,
//   矢量则注入主题色 SLD (读 colors.css 的 --map-vector-stroke/fill), 与本地矢量视觉一致。
//   栅格图层不套矢量 SLD (避免 GeoServer 渲染失败)。

// 全局图层类型缓存: workspace:layerName → 'vector'|'raster'|'unknown'
var _layerTypeCache = {};
var _layerTypeCachePromise = null;

// 异步解析图层类型 (优先复用图层面板工作空间树缓存, 缺失则拉一次 services 填充全局缓存)
//   入参: workspace(string), layerName(string)
//   方法: 先查 _layerTypeCache → 再借 layer-panel.js _lookupLayerType 读 _layerTreeCache → 最后拉 services
//   出参: Promise<'vector'|'raster'|'unknown'>; services 拉取失败回退 'unknown'
function _resolveLayerType(workspace, layerName) {
  var key = (workspace || '') + ':' + layerName;
  if (_layerTypeCache[key]) return Promise.resolve(_layerTypeCache[key]);
  if (typeof _lookupLayerType === 'function') {
    var cached = _lookupLayerType(workspace, layerName);
    if (cached && cached !== 'unknown') { _layerTypeCache[key] = cached; return Promise.resolve(cached); }
  }
  if (!_layerTypeCachePromise) {
    _layerTypeCachePromise = fetch('/api/v1/geoserver/services')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        (data.workspaces || []).forEach(function(ws) {
          var layers = ws.layers_typed || (ws.layers || []).map(function(ln) {
            return { name: ln, type: 'unknown' };
          });
          layers.forEach(function(lyr) {
            _layerTypeCache[ws.workspace + ':' + lyr.name] = lyr.type || 'unknown';
          });
        });
        _layerTypeCachePromise = null;
      })
      .catch(function() { _layerTypeCachePromise = null; });
  }
  return _layerTypeCachePromise.then(function() {
    return _layerTypeCache[key] || 'unknown';
  });
}

// ★ 初始化地图 (由 init.js 调用一次)
function initWmsMap() {
  // 入参:
  //   无显式入参, 依赖页面中的地图宿主 DOM 节点。
  // 方法:
  //   初始化 OpenLayers 地图单例、状态条与弹窗, 并绑定地图视图状态同步;
  //   这样地图初始化后, 后续所有图层叠加与视图保持都围绕同一个 map 实例进行。
  // 出参:
  //   无返回值; 成功时 _map 被赋值为可复用的地图单例。
  const host = document.getElementById('wmsViewport');
  if (!host || _map) return;
  if (typeof ol === 'undefined') {
    console.error('[OL] OpenLayers 未加载 (CDN 失败?), 地图容器不可用');
    return;
  }

  // ★ 右下角统一状态条: 经纬度 + 比例尺 收进同一个组件 (同一深底/字体/字号)
  //   先建 DOM, 再让 ScaleLine 指定 target = 比例尺槽 (不再由 OL 自由放置到左下角)
  var stage = document.getElementById('wmsStage');
  var statusbar = document.createElement('div');
  statusbar.id = 'wmsStatusbar';
  statusbar.className = 'wms-statusbar';
  statusbar.innerHTML =
    '<span class="wms-statusbar__coord" id="wmsCoordDisplay">' +
      '<span class="wms-statusbar__coord-label">经度</span> ' +
      '<span class="wms-statusbar__coord-value" id="wmsLonDisplay">—</span>°' +
      '<span class="wms-statusbar__coord-sep">, </span>' +
      '<span class="wms-statusbar__coord-label">纬度</span> ' +
      '<span class="wms-statusbar__coord-value" id="wmsLatDisplay">—</span>°' +
    '</span>' +
    '<span class="wms-statusbar__scale-label">比例尺</span>' +
    '<span class="wms-statusbar__scale" id="wmsScaleSlot"></span>';
  if (stage) stage.appendChild(statusbar);
  var scaleSlot = statusbar.querySelector('#wmsScaleSlot');

  // ★ 控件: 使用 ol.control.defaults.defaults() 的 getArray() 获取普通数组 (兼容 OL 9 Collection)
  var controls = ol.control.defaults.defaults({ attributionOptions: { collapsible: true } }).getArray().slice();
  // 比例尺挂到状态条的槽位 (而非 OL 默认的地图右下角), 与经纬度同处一组件
  controls.push(new ol.control.ScaleLine({ target: scaleSlot }));
  _map = new ol.Map({
    target: host,
    layers: [],
    view: new ol.View({
      // ★ 默认中心点: 成都市 (坐标取自 tests/data/cd/chengduqu.shp 的几何中心,
      //   bbox=[102.991678, 30.091386, 104.896132, 31.437765] 的中点, WGS84/EPSG:4326)。
      //   加载影像/结果前先聚焦成都, 避免空地图落在 [0,0] 海上。
      center: [103.943905, 30.764575],
      zoom: 9,
      projection: 'EPSG:4326',
    }),
    controls: controls,
  });
  _ensureBaseLayers();

  // ★ 鼠标经纬度实时显示: pointermove 更新状态条里的经纬度槽
  var lonEl = statusbar.querySelector('#wmsLonDisplay');
  var latEl = statusbar.querySelector('#wmsLatDisplay');
  _map.on('pointermove', function(evt) {
    if (!evt.coordinate) {
      lonEl.textContent = '—';
      latEl.textContent = '—';
      return;
    }
    // view projection 是 EPSG:4326, evt.coordinate 直接就是经纬度
    var lon = evt.coordinate[0].toFixed(6);
    var lat = evt.coordinate[1].toFixed(6);
    lonEl.textContent = lon;
    latEl.textContent = lat;
  });

  // ★ GetFeatureInfo 弹窗 Overlay (复用 .wms-popup 样式)
  const popupEl = document.createElement('div');
  popupEl.className = 'wms-popup';
  popupEl.style.display = 'none';
  document.getElementById('wmsStage').appendChild(popupEl);
  _popupOverlay = new ol.Overlay({
    element: popupEl,
    autoPan: { animation: { duration: 250 } },
  });
  _map.addOverlay(_popupOverlay);
  _bindMapViewStateSync();

  // ★ 默认加载成都市边界线 + name 标注 (cd_shp:chengdushi); chengduqu 已禁用
  setTimeout(function() {
    var strokeColor = _cssVar('--map-boundary', '#2c6fbd');
    var sldChengdushi = _makeBoundarySLD('cd_shp:chengdushi', strokeColor);
    // chengduqu 已禁用: 改为按需从图层面板手动加载
    // var sldChengduqu = _makeBoundarySLD('cd_shp:chengduqu', strokeColor);
    // showWmsInPanel('chengduqu', 'cd_shp', null, null, '成都区', false, { sldBody: sldChengduqu, forceFit: true, persistent: true });
    showWmsInPanel('chengdushi', 'cd_shp', null, null, '成都市', false, { sldBody: sldChengdushi, forceFit: false, persistent: true });
  }, 300);
}

// ★ 清空所有业务图层 (保留底图/控件/Overlay)
function _clearMapLayers() {
  // 入参:
  //   无显式入参, 直接操作当前地图单例上的业务图层集合。
  // 方法:
  //   仅移除业务图层与业务图例, 不销毁地图实例与当前视图对象;
  //   这样“清空内容”和“重置坐标系”分离, 后续可按需要单独恢复或重置视图。
  // 出参:
  //   无返回值; 成功时地图上仅保留控件与 Overlay。
  if (!_map) return;
  // ★ 使用注册表遍历 (替代旧标签过滤), 跳过持久层 (跨对话保留)
  var keys = Object.keys(_layerRegistry);
  keys.forEach(function(key) {
    var entry = _layerRegistry[key];
    // ★ persistent 层保留在 map 和注册表中, 跨对话不销毁
    if (entry && !entry.config.persistent) {
      _map.removeLayer(entry.layer);
      delete _layerRegistry[key];
    }
  });
  // 清理图例浮层 (图例始终清理, 不区分持久/非持久)
  var overlays = document.querySelectorAll('.seg-legend-overlay');
  overlays.forEach(function(el) { el.remove(); });
}

// ★ 按 workspace:layerName 反注册 (供 layer-panel.js 显隐切换调用)
//   入参: workspace (string), layerName (string)
//   方法: 从注册表查找 key=workspace:layerName, 移除地图图层并清理注册表条目
//   出参: 无
function _unregisterWmsLayer(workspace, layerName) {
  var key = (workspace || '') + ':' + layerName;
  var entry = _layerRegistry[key];
  if (entry && _map) {
    _map.removeLayer(entry.layer);
    _unregisterLayer(key);
  }
}

// ★ 异步查影像地理元数据 (复用 /api/v1/image/meta 端点, 缓存避免重复请求)
function _fetchImageMeta(imagePath) {
  return new Promise(function(resolve) {
    if (!imagePath) { resolve(null); return; }
    if (_imageMetaCache[imagePath]) { resolve(_imageMetaCache[imagePath]); return; }
    fetch('/api/v1/image/meta?path=' + encodeURIComponent(imagePath))
      .then(function(r) { return r.json(); })
      .then(function(meta) {
        _imageMetaCache[imagePath] = meta;
        resolve(meta);
      })
      .catch(function() { resolve(null); });
  });
}

// ★ 把图片加载到地图容器 (SamSeg 结果 PNG/TIF, 或上传原图)
// 签名兼容旧调用 (url, caption, legend, record, imagePath, artifactPath), 新增 overlay 参数
// overlay=true → 该图层设为半透明叠加层 (掩膜, 替代旧 overlay 混色图)
function showImageInPanel(url, caption, legend, record, imagePath, artifactPath, overlay, viewOptions) {
  // 入参:
  //   url/caption/...: 影像图层基础信息; viewOptions.forceFit=true 表示本次加载后需要主动定位。
  // 方法:
  //   将影像作为独立业务图层叠加到地图上, 默认不抢占已有视图;
  //   只有首次加载或调用方明确要求定位时, 才会把视图 fit 到该影像范围。
  // 出参:
  //   返回 Promise<{loaded,error?}>; loaded=true 表示影像图层已成功加入并完成加载。
  if (!_map) { initWmsMap(); }
  if (!_map) return Promise.resolve({ loaded: false, error: '地图未初始化' });

  onWmsImageLoaded();  // 隐藏空提示

  return new Promise(function(resolve) {
    var settled = false;
    function done(result) {
      if (settled) return; settled = true; resolve(result);
    }
    var timeout = setTimeout(function() { done({ loaded: false, error: '图片加载超时 (15s)' }); }, 15000);

    // ★ 优先用 imagePath 查地理元数据定位; 无 imagePath 时用图片自身 (mask 同 input 坐标)
    var metaPath = imagePath || null;
    _fetchImageMeta(metaPath).then(function(meta) {
      var extent, sourceProj;

      if (meta && meta.has_crs && meta.bbox && meta.bbox.length === 4 && meta.width && meta.height) {
        // ★ 有 CRS: meta.bbox 是【源 CRS 坐标】(可能是 4326 经纬度, 也可能是 3857 投影米)
        //   判定: bbox 值 > 180 → 投影坐标 (Web Mercator 3857); 否则经纬度 4326
        var b = meta.bbox;
        var isProjected = Math.abs(b[0]) > 180 || Math.abs(b[2]) > 180;
        sourceProj = isProjected ? 'EPSG:3857' : 'EPSG:4326';
        // ★ ImageStatic 的 imageExtent 必须用【源 CRS 坐标】(配合 projection 参数),
        //   OL 内部会把它投影到 view.projection。不能手动 transformExtent (会导致范围错乱)。
        extent = b;
      } else {
        // ★ 系统只接受带坐标影像: 无 CRS 不再虚拟铺画布, 直接报错拒绝渲染。
        //   (旧逻辑会按 extent=[0,0,W,H] 伪 4326 铺图, 位置是假的, 已移除)
        clearTimeout(timeout);
        var detail = (meta && meta.msg) ? ' (' + meta.msg + ')' : '';
        done({ loaded: false, error: '该影像无地理坐标(CRS)，无法在地图中定位显示。请使用带坐标的 GeoTIFF/COG 影像。' + detail });
        return;
      }

      var source = new ol.source.ImageStatic({
        url: _appendPreviewParam(url),
        imageExtent: extent,          // ★ 源 CRS 坐标 (ImageStatic 据此 + projection 渲染)
        projection: sourceProj,       // ★ 源 CRS (OL 内部把图层投影到 view.projection)
      });

      var layer = new ol.layer.Image({
        source: source,
        opacity: overlay ? 0.5 : 1.0,   // ★ overlay=true (掩膜) 半透明叠加; 否则不透明底图
        zIndex: 5,                        // ★ 底图在最下层 (WMS10 > 底图5, 矢量描边15 > WMS10)
        tag: _LAYER_TAG,                 // ★ 标记为业务图层 (清空时识别)
      });
      layer.set('caption', caption);

      // ★ 注册到图层注册表
      var imgKey = 'img_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
      _registerLayer(imgKey, layer, {
        type: 'image',
        url: url,
        caption: caption,
        overlay: !!overlay,
        addedAt: Date.now(),
      });

      _map.addLayer(layer);

      // ★ 关键: 必须先把 view fit 到图层范围, 否则图层在视图外 OL 不触发图片加载
      //   (ImageStatic 懒加载)。fit 的 extent 必须是 view projection 坐标,
      //   所以先把源 CRS extent 转成 view projection。
      var viewProj = _map.getView().getProjection();
      var extentInViewProj = ol.proj.transformExtent(extent, sourceProj, viewProj);
      _fitMapToExtent(extentInViewProj, !!(viewOptions && viewOptions.forceFit));

      // ★ 图层加载事件 → resolve 真实状态
      source.on('imageloadend', function() {
        clearTimeout(timeout);
        done({ loaded: true });
      });
      source.on('imageloaderror', function() {
        clearTimeout(timeout);
        done({ loaded: false, error: '图层加载失败: ' + url });
      });
    }).catch(function(e) {
      clearTimeout(timeout);
      done({ loaded: false, error: '元数据查询失败: ' + (e && e.message) });
    });

    // ★ 记录到对话 state (供切换对话恢复)。用唯一 key 标记
    if (record !== false) {
      const cur = cs();
      if (cur) {
        const imageKey = 'img_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
        cur.wmsImages.push({
          type: 'image', key: imageKey,
          url: url, caption: caption, legend: legend,
          imagePath: imagePath, artifactPath: artifactPath, overlay: overlay,
        });
        _persistConvWms(activeConvId);
      }
    }
  });
}

// ★ 矢量 GeoJSON 图斑叠加
// 兼容两种入参:
//   - geojsonUrl (string): 远程 .geojson URL → OL 异步加载
//   - geojsonObj (object): 已解析的 GeoJSON 对象 (shp 转换后) → 直接注入 features
function _addVectorLayer(geojsonUrlOrObj, caption) {
  if (!_map) return null;
  var source;
  if (typeof geojsonUrlOrObj === 'string') {
    // URL 形态 (.geojson 文件)
    source = new ol.source.Vector({
      url: geojsonUrlOrObj,
      format: new ol.format.GeoJSON(),
    });
  } else if (geojsonUrlOrObj && typeof geojsonUrlOrObj === 'object') {
    // GeoJSON 对象形态 (shp 转换结果)
    var features = new ol.format.GeoJSON().readFeatures(geojsonUrlOrObj, {
      dataProjection: 'EPSG:4326',
      featureProjection: _map.getView().getProjection(),
    });
    source = new ol.source.Vector({ features: features });
  } else {
    return null;
  }
  // ★ 统一边界线风格: 浅蓝色描边 + 透明填充 + name 字段标注 (与 _makeBoundarySLD 视觉一致)
  var vecStrokeColor = _cssVar('--map-vector-stroke', '#2C6FBD');
  var layer = new ol.layer.Vector({
    source: source,
    style: function(feature) {
      return new ol.style.Style({
        fill: new ol.style.Fill({ color: 'rgba(0,0,0,0)' }),
        stroke: new ol.style.Stroke({ color: vecStrokeColor, width: 2 }),
        text: new ol.style.Text({
          text: feature.get('name') || '',
          font: '12px Microsoft YaHei, sans-serif',
          fill: new ol.style.Fill({ color: vecStrokeColor }),
          stroke: new ol.style.Stroke({ color: '#ffffff', width: 2 }),
        }),
      });
    },
    opacity: 0.8,
    zIndex: 15,
    tag: _LAYER_TAG,
  });
  layer.set('caption', caption || '矢量图斑');

  // ★ 注册到图层注册表
  var vecKey = 'vec_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
  _registerLayer(vecKey, layer, {
    type: 'vector',
    caption: caption || '矢量图斑',
    addedAt: Date.now(),
  });

  _map.addLayer(layer);
  return layer;
}

// ★ 统一矢量文件加载入口 (支持 .geojson 和 .shp)
//   - .geojson: 直接走 _addVectorLayer (URL 形态)
//   - .shp: 先调 /api/v1/vector/shp-to-geojson 转换 → 再 _addVectorLayer (对象形态)
// 返回 Promise (供 thinking.js finishAction 闭环)
function showVectorFile(urlOrPath, caption, record, viewOptions) {
  // 入参:
  //   urlOrPath: GeoJSON 或 Shapefile 路径; viewOptions.forceFit=true 时本次加载后定位到该矢量范围。
  // 方法:
  //   统一加载本地矢量数据, 默认只叠加不改动当前视图;
  //   只有首次进入地图或显式要求定位时才自动 fit。
  // 出参:
  //   返回 Promise<{loaded,error?,url}>; loaded=true 表示矢量已成功加入地图。
  if (!_map) { initWmsMap(); }
  if (!_map) return Promise.resolve({ loaded: false, error: '地图未初始化' });

  onWmsImageLoaded();
  var cap = caption || '矢量图层';
  var lower = (urlOrPath || '').toLowerCase();

  // ★ 记录到对话 state (供切换对话恢复)
  function _recordVector(rec) {
    if (rec !== false) {
      var cur = cs();
      if (cur) {
        var key = 'vec_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
        cur.wmsImages.push({
          type: 'vector', key: key,
          url: urlOrPath, caption: cap,
        });
        _persistConvWms(activeConvId);
      }
    }
  }

  // ★ fit 到矢量 bounds (加载完成后)
  function _fitToVector(layer) {
    try {
      var extent = layer.getSource().getExtent();
      if (extent && !ol.extent.isEmpty(extent)) {
        _fitMapToExtent(extent.slice(), !!(viewOptions && viewOptions.forceFit));
      }
    } catch (e) { /* 异步加载未完成, 忽略 */ }
  }

  if (lower.endsWith('.shp') || lower.indexOf('_shp') >= 0) {
    // ★ Shapefile: 走后端 shp-to-geojson 转换
    return fetch('/api/v1/vector/shp-to-geojson?shp_path=' + encodeURIComponent(urlOrPath))
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (!data.success) {
          return { loaded: false, error: data.msg || 'Shapefile 转换失败', url: urlOrPath };
        }
        var geojson = typeof data.geojson === 'string' ? JSON.parse(data.geojson) : data.geojson;
        var layer = _addVectorLayer(geojson, cap + ' (' + data.feature_count + '要素)');
        if (layer) {
          _fitToVector(layer);
          _recordVector(record);
          return { loaded: true, feature_count: data.feature_count, crs: data.crs, sampled: data.sampled, url: urlOrPath };
        }
        return { loaded: false, error: '矢量图层创建失败', url: urlOrPath };
      })
      .catch(function(e) {
        return { loaded: false, error: 'Shapefile 请求失败: ' + (e.message || e), url: urlOrPath };
      });
  }

  // ★ GeoJSON: 直接加载
  var layer = _addVectorLayer(urlOrPath, cap);
  if (layer) {
    // URL 形态异步加载, 延迟 fit (source changefeature 事件触发后)
    layer.getSource().on('featuresloadend', function() { _fitToVector(layer); });
    layer.getSource().on('featuresloaderror', function() { /* 加载失败静默 */ });
    _recordVector(record);
    return Promise.resolve({ loaded: true, url: urlOrPath });
  }
  return Promise.resolve({ loaded: false, error: '矢量图层创建失败', url: urlOrPath });
}

// ★ 图例渲染为地图右上角浮层 (DOM, 非 OL 图层)
function _renderLegendOverlay(legend, title) {
  var stage = document.getElementById('wmsStage');
  if (!stage || !legend || !legend.length) return;
  // 移除旧图例 (同会话只保留最新)
  var old = stage.querySelector('.seg-legend-overlay');
  if (old) old.remove();
  var wrap = document.createElement('div');
  wrap.className = 'seg-legend-overlay';
  var titleEl = document.createElement('div');
  titleEl.className = 'legend-title';
  titleEl.textContent = title || '图例';
  wrap.appendChild(titleEl);
  legend.forEach(function(it) {
    var chip = document.createElement('span');
    chip.style.cssText = 'display:inline-flex;align-items:center;gap:4px;margin-right:10px;font-size:var(--fs-sm);font-family:var(--mono);color:var(--text-primary);';
    var sw = document.createElement('span');
    sw.style.cssText = 'display:inline-block;width:10px;height:10px;border-radius:2px;background:' + it.hex + ';border:1px solid rgba(0,0,0,0.15);';
    chip.appendChild(sw);
    chip.appendChild(document.createTextNode(it.name || ''));
    wrap.appendChild(chip);
  });
  // seg-legend 换行布局
  wrap.style.display = 'flex';
  wrap.style.flexWrap = 'wrap';
  wrap.style.gap = '6px 12px';
  stage.appendChild(wrap);
}

// ==================== WMS 图层加载 (GeoServer) ====================

// ★ WMS 代理 URL: 不再直连 GeoServer (硬编码 localhost:8080 远程访问不可达 + CORS)。
//   前端所有 WMS GetMap 请求经后端 /api/v1/geoserver/wms 代理转发,
//   地址随服务端 settings.geoserver_url, 统一认证, 绕过浏览器 CORS。
//   (与 feature-info 代理同源, 修复"WMS 加载超时 (10s)")
var _WMS_PROXY_URL = '/api/v1/geoserver/wms';

// ★ 把可能直连 GeoServer 的旧 wmsUrl 归一化到代理 URL
//   - 缺省 / 明显指向 GeoServer (含 /geoserver/wms 或 localhost:8080) → 走代理
//   - 调用方显式传入其他 URL (如自定义服务端点) → 保留原值
function _resolveWmsUrl(wmsUrl) {
  if (!wmsUrl) return _WMS_PROXY_URL;
  var u = wmsUrl;
  // 直连 GeoServer 的旧默认值 (含协议头或裸名都覆盖), 统一改走代理
  if (/\/geoserver\/wms/i.test(u) || /localhost:8080/i.test(u) || u.indexOf('127.0.0.1:8080') >= 0) {
    return _WMS_PROXY_URL;
  }
  return u;
}

// 构造 GeoServer WMS GetMap URL
function buildWmsUrl(layerName, workspace, bbox, wmsUrl) {
  const base = _resolveWmsUrl(wmsUrl);
  const ws = workspace || '';
  const layersParam = ws ? (ws + ':' + layerName) : layerName;
  const params = new URLSearchParams({
    service: 'WMS', version: '1.1.1', request: 'GetMap',
    layers: layersParam,
    styles: '',
    format: 'image/png', transparent: 'false',
    width: '512', height: '512',
    srs: 'EPSG:4326',
    bbox: (bbox && bbox.length === 4) ? bbox.join(',') : '-180,-90,180,90',
  });
  return base + '?' + params.toString();
}

// ★ WMS 图层加载到地图 (统一走 TileWMS source + 透明瓦片叠加)
//   v2.7 重构: 删除"矢量→WFS Vector"分叉。同一图层之前会因"默认加载传 SLD 走 TileWMS、
//   面板定位未传 SLD 走 WFS"分裂成两份地图图层, 各自画一次 name 字段 → 文字重影。
//   现在统一只走 TileWMS, 并加两道判定: ① 同 key 去重 (已存在只 fit 不重加);
//   ② 矢量且未显式传 SLD 时自动注入边界线 SLD (与默认加载的 name 标注视觉一致)。
//   WFS/Vector 路径仍保留, 仅由 renderImage (AI 分析结果的变化面/边缘线, 无 name 字段) 使用。
function showWmsInPanel(layerName, workspace, bbox, wmsUrl, caption, record, viewOptions) {
  // 入参:
  //   layerName/workspace/...: WMS 图层定位信息; viewOptions.forceFit=true 表示显示后需定位到该图层;
  //   viewOptions.sldBody: 调用方显式指定的 SLD (如默认加载的边界线样式), 未传时矢量图层自动注入边界线 SLD。
  // 方法:
  //   将 GeoServer 图层以透明瓦片叠加到当前地图, 支持多图层同时显示互不遮挡;
  //   仅在显式定位或首次加载场景下执行 fit, 避免切换 Tab/恢复会话时坐标系被重置。
  // 出参:
  //   返回 Promise<{loaded,error?,layer_name,workspace,deduped?}>; loaded=true 表示瓦片已加载或图层已存在;
  //   deduped=true 表示本次因同 key 图层已存在而跳过叠加 (仅 fit 视图)。
  if (!_map) { initWmsMap(); }
  if (!_map) return Promise.resolve({ loaded: false, error: '地图未初始化' });

  onWmsImageLoaded();

  const ws = workspace || '';
  const key = ws + ':' + layerName;
  const forceFit = !!(viewOptions && viewOptions.forceFit);

  // ★ 判定① 同 key 去重: 图层已存在 (如默认加载的 cd_shp:chengduqu) 时, 不再重复叠加。
  //   为什么必须去重: 默认加载的边界图层带 name 标注, 若面板"定位/显示"再叠一份,
  //   两个同 key 图层各画一次 name → 文字重影。去重后同 key 全局只保留一个图层实例。
  //   行为: 已存在 → 仅按 bbox + forceFit 移动视图, 同步面板勾选, 直接返回。
  if (_findLayerByKey(key)) {
    if (bbox && bbox.length === 4) {
      var extDedup = ol.proj.transformExtent(bbox, 'EPSG:4326', _map.getView().getProjection());
      _fitMapToExtent(extDedup, forceFit);
    }
    if (typeof syncLayerPanelCheckboxes === 'function') syncLayerPanelCheckboxes();
    return Promise.resolve({ loaded: true, deduped: true, layer_name: layerName, workspace: ws });
  }

  // ★ 判定② SLD 注入策略 (统一 TileWMS 路径下保证矢量 name 标注一致):
  //   - 调用方显式传 sldBody (默认加载边界线场景): 直接用, 不再查类型 (快速路径)。
  //   - 未传 sldBody: 异步查类型 → 矢量自动注入边界线 SLD (边界描边 + name 标注,
  //     与默认加载视觉一致, 避免 GeoServer 灰色默认样式无标注); 栅格/unknown 不注入
  //     (栅格套矢量 SLD 会导致 GeoServer 渲染失败)。
  function _proceed(finalSldBody) {
    var mergedOpts = viewOptions || {};
    if (finalSldBody) mergedOpts = Object.assign({}, mergedOpts, { sldBody: finalSldBody });
    return _showTileWmsFallback(layerName, ws, bbox, wmsUrl, caption, record, mergedOpts);
  }

  if (viewOptions && viewOptions.sldBody) {
    return _proceed(viewOptions.sldBody);
  }

  return _resolveLayerType(ws, layerName).then(function(ltype) {
    var autoSld = null;
    if (ltype === 'vector') {
      autoSld = _makeBoundarySLD(ws + ':' + layerName, _cssVar('--map-vector-stroke', '#2C6FBD'));
    }
    return _proceed(autoSld);
  });
}

// ★ TileWMS 加载 (唯一地图叠加路径; 矢量/栅格统一经此函数, 由 showWmsInPanel 注入 SLD 决定样式)
//   sldBody 来源: 调用方显式传入 (默认加载边界线) 或 showWmsInPanel 按图层类型自动注入 (矢量边界线 SLD)。
function _showTileWmsFallback(layerName, ws, bbox, wmsUrl, caption, record, viewOptions) {
  const extent = (bbox && bbox.length === 4)
    ? ol.proj.transformExtent(bbox, 'EPSG:4326', _map.getView().getProjection())
    : [-180, -90, 180, 90];

  // ★ 栅格图层使用 TileWMS: 瓦片化加载, 支持多级缩放
  // ★ viewOptions.sldBody 可选: 自定义 SLD 样式 (如仅显示边界线、透明填充)
  var wmsParams = {
    'LAYERS': ws ? (ws + ':' + layerName) : layerName,
    'FORMAT': 'image/png',
    'TILED': true,
    'TRANSPARENT': true,
    'VERSION': '1.1.1',
    'SRS': 'EPSG:4326',
  };
  if (viewOptions && viewOptions.sldBody) {
    wmsParams['SLD_BODY'] = viewOptions.sldBody;
    wmsParams['STYLES'] = '';  // ★ 清空默认样式, 确保 SLD_BODY 生效
  }
  const source = new ol.source.TileWMS({
    url: _resolveWmsUrl(wmsUrl),
    params: wmsParams,
    serverType: 'geoserver',
    transition: 0,
  });

  const layer = new ol.layer.Tile({
    source: source,
    visible: true,
    opacity: 0.8,
    zIndex: 10,
  });
  layer.set('tag', _LAYER_TAG);
  layer.set('caption', caption || layerName);
  layer.set('layerName', layerName);
  layer.set('workspace', ws);
  // ★ 存 WMS 请求参数 (GetFeatureInfo 反算像素 i/j 用)
  layer.set('wmsBbox', (bbox && bbox.length === 4) ? bbox : [-180, -90, 180, 90]);
  layer.set('wmsWidth', 512);
  layer.set('wmsHeight', 512);

  // ★ 注册到图层注册表: key = workspace:layerName
  _registerLayer(ws + ':' + layerName, layer, {
    type: 'wms',
    workspace: ws,
    layerName: layerName,
    bbox: bbox,
    addedAt: Date.now(),
    persistent: !!(viewOptions && viewOptions.persistent),  // ★ 持久层跨对话保留
  });

  _map.addLayer(layer);

  // ★ 绑定 GetFeatureInfo 点击 (WMS 图层才支持)
  _bindFeatureInfoIfNeeded();

  // ★ 同步图层管理面板勾选状态 (图层添加后, 面板对应行应勾选)
  if (typeof syncLayerPanelCheckboxes === 'function') syncLayerPanelCheckboxes();

  // ★ 记录到对话 state
  if (record !== false) {
    const cur = cs();
    if (cur) {
      const imageKey = 'wms_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
      cur.wmsImages.push({
        type: 'wms', key: imageKey,
        layerName: layerName, workspace: workspace || '',
        bbox: bbox, wmsUrl: wmsUrl, caption: caption || layerName,
      });
      _persistConvWms(activeConvId);
    }
  }

  // ★ view fit 到 bbox + 等加载完成 resolve
  // ★ 仅当有真实 bbox 时才 fit 视图; 无 bbox (全景兜底 [-180,-90,180,90]) 不强制修改视图,
  //   避免把用户视图拉到全球范围导致已有图层不可见。
  if (bbox && bbox.length === 4) {
    _fitMapToExtent(extent, !!(viewOptions && viewOptions.forceFit));
  }

  return new Promise(function(resolve) {
    var settled = false;
    function done(r) { if (settled) return; settled = true; resolve(r); }
    // ★ 超时 10s→30s: 走后端代理后链路变长, GeoServer 首瓦片渲染也较慢, 10s 过激易误报。
    //   (原 10s 是直连 localhost 的假设; 代理场景下应留足渲染时间)
    var endCount = 0, errCount = 0;
    var timeout = setTimeout(function() {
      var msg = (errCount > 0 && endCount === 0)
        ? 'WMS 加载失败: ' + errCount + ' 个瓦片均出错 (代理/GeoServer 返回错误)'
        : 'WMS 加载超时 (30s)';
      done({ loaded: false, error: msg, layer_name: layerName, workspace: ws });
    }, 30000);
    // ★ TileWMS 逐瓦片加载; 首个瓦片成功即视为图层已加载
    //   - tileloadend (首个成功): 图层已加载, 清超时 resolve true。
    //   - tileloaderror: 单瓦片失败很常见 (边缘瓦片、GeoServer 瞬时排队), 不当作整层失败;
    //     但若所有瓦片都失败 (endCount=0), 超时分支据此给出准确错误信息,
    //     并在首个出错瓦片后延迟一帧提前结束, 避免干等满 30s。
    source.on('tileloadend', function() { endCount++; clearTimeout(timeout); done({ loaded: true, layer_name: layerName, workspace: ws }); });
    source.on('tileloaderror', function() {
      errCount++;
      if (endCount === 0) {
        clearTimeout(timeout);
        // 延迟一帧, 给可能稍晚到达的成功事件一个机会
        setTimeout(function() {
          done({ loaded: endCount > 0, error: endCount > 0 ? null : 'WMS 加载失败: 瓦片请求出错 (检查 GeoServer 服务状态)', layer_name: layerName, workspace: ws });
        }, 800);
      }
    });
  });
}

// ★ WFS 矢量图层加载 (GeoServer WFS GetFeature → GeoJSON → VectorSource)
//   入参: 与 showWmsInPanel 一致; 内部构造 WFS URL, 委托 _addVectorLayer 加载。
//   参考: E:\1代码\系统\Agent-RSCD\Frontend\src\composables\useGeoServer.ts createWFSVectorLayer
function showWfsInPanel(layerName, workspace, bbox, wmsUrl, caption, record, viewOptions) {
  if (!_map) { initWmsMap(); }
  if (!_map) return Promise.resolve({ loaded: false, error: '地图未初始化' });

  onWmsImageLoaded();

  const ws = workspace || '';
  const fullName = ws ? (ws + ':' + layerName) : layerName;

  // 构造 WFS GetFeature URL (输出 GeoJSON, EPSG:4326)
  // ★ 走后端 /api/v1/geoserver/wms 代理 (后端按 service 参数转发到 GeoServer WMS/WFS,
  //   避免前端直连 localhost:8080 的地址/CORS 问题, 与 showWmsInPanel 一致)。
  const wfsBase = _resolveWmsUrl(wmsUrl);
  const params = new URLSearchParams({
    service: 'WFS',
    request: 'GetFeature',
    version: '1.1.0',
    typeName: fullName,
    outputFormat: 'application/json',
    srsName: 'EPSG:4326',
  });
  const wfsUrl = wfsBase + '?' + params.toString();

  // ★ 复用 _addVectorLayer (支持 URL 形态 GeoJSON)
  const layer = _addVectorLayer(wfsUrl, caption || layerName);
  if (!layer) return Promise.resolve({ loaded: false, error: '矢量图层创建失败', layer_name: layerName, workspace: ws });

  layer.set('layerName', layerName);
  layer.set('workspace', ws);
  layer.set('wmsBbox', (bbox && bbox.length === 4) ? bbox : [-180, -90, 180, 90]);
  layer.set('wmsWidth', 512);
  layer.set('wmsHeight', 512);

  _registerLayer(ws + ':' + layerName, layer, {
    type: 'wfs',
    workspace: ws,
    layerName: layerName,
    bbox: bbox,
    addedAt: Date.now(),
    persistent: !!(viewOptions && viewOptions.persistent),
  });

  if (bbox && bbox.length === 4) {
    const extent = ol.proj.transformExtent(bbox, 'EPSG:4326', _map.getView().getProjection());
    _fitMapToExtent(extent, !!(viewOptions && viewOptions.forceFit));
  }

  if (typeof syncLayerPanelCheckboxes === 'function') syncLayerPanelCheckboxes();

  if (record !== false) {
    const cur = cs();
    if (cur) {
      const imageKey = 'wfs_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
      cur.wmsImages.push({
        type: 'wfs', key: imageKey,
        layerName: layerName, workspace: ws,
        bbox: bbox, caption: caption || layerName,
      });
      _persistConvWms(activeConvId);
    }
  }

  return new Promise(function(resolve) {
    var settled = false;
    function done(r) { if (settled) return; settled = true; resolve(r); }
    var timeout = setTimeout(function() { done({ loaded: false, error: 'WFS 加载超时 (10s)', layer_name: layerName, workspace: ws }); }, 10000);
    layer.getSource().on('featuresloadend', function() { clearTimeout(timeout); done({ loaded: true, layer_name: layerName, workspace: ws }); });
    layer.getSource().on('featuresloaderror', function() {
      clearTimeout(timeout);
      done({ loaded: false, error: 'WFS 加载失败', layer_name: layerName, workspace: ws });
    });
  });
}

// ★ 绑定 GetFeatureInfo 点击查询 (单次绑定, WMS 图层点击触发)
var _featureInfoBound = false;
function _bindFeatureInfoIfNeeded() {
  if (_featureInfoBound || !_map) return;
  _featureInfoBound = true;
  _map.on('singleclick', async function(evt) {
    // 收集所有可见 WMS 图层 (从上到下)
    var allLayers = [];
    _map.getLayers().forEach(function(layer) {
      if (layer.getVisible() && layer.get('layerName')) {
        allLayers.push(layer);
      }
    });
    if (!allLayers.length) return;

    // ★ 从最上层往下依次尝试, 第一个返回有效属性数据的图层作为显示结果
    //   边缘线图层 click 命中率低 (线太细), 自动回退到面图层获取属性
    for (var idx = allLayers.length - 1; idx >= 0; idx--) {
      var result = await _queryWmsFeatureInfo(allLayers[idx], evt.coordinate);
      if (result && result.hasFeatures) {
        _renderFeatureInfoPopup(result, evt.coordinate);
        return;
      }
    }
    // 所有图层都无数据, 用最上层显示空结果
    var topLayer = allLayers[allLayers.length - 1];
    var emptyResult = await _queryWmsFeatureInfo(topLayer, evt.coordinate);
    _renderFeatureInfoPopup(emptyResult || { layerName: topLayer.get('layerName'), workspace: '', i: 0, j: 0, hasFeatures: false }, evt.coordinate);
  });
}

// ★ GetFeatureInfo 纯查询 (不操作弹窗), 返回 {layerName, workspace, i, j, data, hasFeatures}
//   基于当前视图实际渲染分辨率构造精确的 GetFeatureInfo 请求 ——
//   以点击点为中心取 101×101 像素范围对应的地理 bbox, 点击映射到中心像素 (50,50),
//   确保查询分辨率与用户眼睛看到的瓦片分辨率一致, 避免存死 bbox/width/height 导致的偏移。
async function _queryWmsFeatureInfo(layer, coord) {
  const layerName = layer.get('layerName');
  const workspace = layer.get('workspace') || '';

  var lonLat = ol.proj.transform(coord, _map.getView().getProjection(), 'EPSG:4326');
  var lon = lonLat[0], lat = lonLat[1];

  // ★ 从当前视图计算每像素对应的经纬度跨度 (EPSG:4326)
  var mapSize = _map.getSize();
  var viewExtent = _map.getView().calculateExtent(mapSize);
  var viewExtent4326 = ol.proj.transformExtent(viewExtent, _map.getView().getProjection(), 'EPSG:4326');
  var degPerPixelLon = (viewExtent4326[2] - viewExtent4326[0]) / mapSize[0];
  var degPerPixelLat = (viewExtent4326[3] - viewExtent4326[1]) / mapSize[1];

  var queryW = 101, queryH = 101;
  var halfW = Math.floor(queryW / 2), halfH = Math.floor(queryH / 2);
  var bufferLon = halfW * degPerPixelLon;
  var bufferLat = halfH * degPerPixelLat;
  var queryBbox = [lon - bufferLon, lat - bufferLat, lon + bufferLon, lat + bufferLat];
  var i = halfW, j = halfH;

  try {
    const params = new URLSearchParams({
      layer_name: layerName,
      workspace: workspace,
      bbox: queryBbox.join(','),
      width: queryW,
      height: queryH,
      i: i,
      j: j,
    });
    const resp = await fetch('/api/v1/geoserver/feature-info?' + params.toString());
    const data = await resp.json();
    var hasFeatures = data.success && data.values && Object.keys(data.values).length > 0;
    return { layerName: layerName, workspace: workspace, i: i, j: j, data: data, hasFeatures: hasFeatures };
  } catch (err) {
    return { layerName: layerName, workspace: workspace, i: i, j: j, data: null, hasFeatures: false, error: err.message };
  }
}

// ★ GetFeatureInfo 弹窗渲染
function _renderFeatureInfoPopup(result, coord) {
  var layerName = result.layerName;
  var i = result.i, j = result.j;
  var data = result.data;
  var hasError = result.error;

  var popupEl = _popupOverlay.getElement();
  popupEl.innerHTML =
    '<div class="popup-header"><span class="popup-title">属性查看</span>'
    + '<button class="popup-close" type="button"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6L6 18M6 6l12 12"/></svg></button></div>'
    + '<div class="popup-coords">图层: ' + escapeHtml(layerName) + ' | 像素: (' + i + ', ' + j + ')</div>'
    + '<div class="popup-loading">查询中...</div>';
  popupEl.style.display = '';
  _popupOverlay.setPosition(coord);
  popupEl.querySelector('.popup-close').onclick = function(e) { e.stopPropagation(); closeWmsPopup(); };

  var bodyEl = popupEl.querySelector('.popup-loading');
  if (!bodyEl) return;

  if (hasError) {
    bodyEl.className = 'popup-error';
    bodyEl.textContent = '网络请求失败: ' + result.error;
    return;
  }

  if (!data) { return; }

  if (result.hasFeatures) {
    let tableRows = '';
    for (const [key, val] of Object.entries(data.values)) {
      let displayVal = val;
      if (typeof val === 'number') displayVal = Number.isInteger(val) ? val : val.toFixed(6);
      tableRows += '<tr><td>' + escapeHtml(key) + '</td><td>' + escapeHtml(String(displayVal)) + '</td></tr>';
    }
    var lonLatText = (data.lon !== undefined && data.lat !== undefined)
      ? ' | 坐标: ' + data.lon + ', ' + data.lat : '';
    popupEl.querySelector('.popup-coords').textContent =
      '图层: ' + layerName + ' | 像素: (' + i + ', ' + j + ')' + lonLatText;
    bodyEl.outerHTML = '<table class="popup-table">' + tableRows + '</table>';
  } else {
    bodyEl.outerHTML = '<div class="popup-coords" style="margin-top:4px">该位置无可读属性数据</div>'
      + (data.raw_response ? '<div style="font-size:var(--fs-xs);color:var(--text-muted);margin-top:4px;word-break:break-all;max-height:120px;overflow-y:auto">原始响应: ' + escapeHtml(data.raw_response.slice(0, 300)) + '</div>' : '');
  }
}

function closeWmsPopup() {
  if (_popupOverlay) {
    var el = _popupOverlay.getElement();
    if (el) el.style.display = 'none';
    _popupOverlay.setPosition(undefined);
  }
}

// ==================== 空提示 / 清空 ====================

function showWmsEmpty() {
  // ★ 清空所有业务图层 (保留控件/Overlay)。空提示文本已移除, 不再显示。
  _clearMapLayers();
}

function clearWmsPanel() {
  // 入参:
  //   无显式入参, 清理当前活跃对话关联的地图业务内容与历史视图快照。
  // 方法:
  //   同步清空图层快照与地图视图快照, 保证用户点击“清空”后不会在切换对话或刷新时又被恢复回来。
  // 出参:
  //   无返回值; 成功时当前地图业务内容与持久化状态一并清空。
  // ★ 同步清空当前对话的影像快照 (否则切换回来会恢复, 与"清空"语义矛盾)
  const cur = cs();
  if (cur) {
    cur.wmsImages = [];
    cur.mapView = null;
    _clearConvWms(activeConvId);
    if (typeof _clearConvMapView === 'function') _clearConvMapView(activeConvId);
  }
  showWmsEmpty();
}

// ★ 空提示已移除, 此函数保留为空操作 (多处调用方仍引用, 保持兼容)
function onWmsImageLoaded() {
  // 无操作 (原用于隐藏 wmsEmptyHint, 已废弃)
}

// ==================== 工具函数 ====================

// ★ 给图片 URL 追加 preview=1 参数 (触发后端 TIFF→PNG 转码)
function _appendPreviewParam(url) {
  if (!url) return url;
  if (/[?&]preview=/.test(url)) return url;
  return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'preview=1';
}

// ★ 构建结构化错误块: 简短提示 + 完整 URL + 一键复制按钮
function buildWmsErrorBlock(message, url) {
  var wrap = document.createElement('div');
  wrap.className = 'wms-error';
  var msg = document.createElement('span');
  msg.className = 'wms-error-msg';
  msg.textContent = message;
  wrap.appendChild(msg);
  if (url) {
    var urlEl = document.createElement('span');
    urlEl.className = 'wms-error-url';
    urlEl.textContent = 'URL: ' + url;
    wrap.appendChild(urlEl);
  }
  var copyBtn = document.createElement('button');
  copyBtn.className = 'wms-error-copy';
  copyBtn.type = 'button';
  copyBtn.title = '复制错误信息';
  copyBtn.setAttribute('aria-label', '复制错误信息');
  var copySVG = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  var checkSVG = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><polyline points="20 6 9 17 4 12"/></svg>';
  copyBtn.innerHTML = copySVG;
  copyBtn.onclick = function(e) {
    e.stopPropagation();
    var text = message + (url ? '\nURL: ' + url : '');
    var doneFn = function() {
      copyBtn.innerHTML = checkSVG;
      copyBtn.classList.add('copied');
      setTimeout(function() { copyBtn.innerHTML = copySVG; copyBtn.classList.remove('copied'); }, 2000);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(doneFn).catch(function() { _fallbackCopy(text); doneFn(); });
    } else { _fallbackCopy(text); doneFn(); }
  };
  wrap.appendChild(copyBtn);
  return wrap;
}

function _fallbackCopy(text) {
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } catch (e) {}
  document.body.removeChild(ta);
}

// ==================== 沙盒文件下载到宿主机指定目录 (v2.5) ====================
// ★ 卡片下载按钮 (⬇) 点击后调用。弹窗让用户输入目标宿主机目录绝对路径,
//   确认后 POST /api/v1/sandbox/download-to-host, 由宿主机后端把文件复制过去。
function openSandboxDownloadDialog(filePath, label) {
  if (!filePath) { alert('该图片无宿主机路径, 无法下载'); return; }
  var defaultDir = '';
  try {
    var norm = filePath.replace(/\//g, '\\');
    var idx = norm.lastIndexOf('\\');
    defaultDir = idx > 0 ? norm.substring(0, idx) : filePath;
  } catch (e) { defaultDir = filePath; }

  showPromptModal(
    '下载到宿主机目录（请输入目标文件夹绝对路径）\n文件: ' + (label || ''),
    defaultDir
  ).then(function(destDir) {
    if (destDir === null) return;
    if (!destDir || !destDir.trim()) { alert('目标目录不能为空'); return; }

    fetch('/api/v1/sandbox/download-to-host', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_path: filePath, dest_dir: destDir.trim() }),
    }).then(function(r) { return r.json(); }).then(function(res) {
      if (res && res.ok) {
        alert('已下载到: ' + res.dest_path + '\n大小: ' + res.size_bytes + ' 字节');
      } else {
        alert('下载失败: ' + (res && res.msg ? res.msg : '未知错误'));
      }
    }).catch(function(e) {
      alert('下载请求失败: ' + e.message);
    });
  });
}

// ==================== 右侧面板 ====================

function addResultCard(eventType, payload) {
  const list = document.getElementById('resultList');
  if (!list) return;  // 回传记录面板已移除
  const card = document.createElement('div'); card.className = 'result-card';
  const ts = new Date().toLocaleTimeString();
  card.innerHTML = `<div class="result-header"><span class="result-type">${eventType}</span><span style="color:var(--text-secondary);font-size:var(--fs-sm)">${ts}</span></div><div class="result-body">${escapeHtml(JSON.stringify(payload, null, 2))}</div>`;
  list.appendChild(card); list.scrollTop = list.scrollHeight;
}

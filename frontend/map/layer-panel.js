// ==================== 图层管理面板 (工作区 Tab: 图层管理) ====================
// ★ 功能区: 地图/影像 (map/) | GeoServer 图层管理面板
//
// 设计意图:
//   让用户直接通过 UI 管理 GeoServer 图层 (工作空间树 + 显隐/定位/删除/上传/属性),
//   而非每次都依赖 AI 自然语言驱动。数据来自后端 REST 端点 (/api/v1/geoserver/*),
//   不走 LLM, 加载快、即时响应。
//
// 复用关系 (不重写地图逻辑):
//   - 图层叠加: showWmsInPanel (wms-render.js, OL ImageWMS)
//   - 矢量叠加: showVectorFile (wms-render.js, OL Vector)
//   - 属性点查: _handleWmsFeatureInfo (wms-render.js, 已绑定 singleclick)
//   - 地图单例: _map (wms-render.js), 业务图层标记 _LAYER_TAG='business'
//
// Tab 状态联动对话切换: 当前 Tab (map/layer) 记入对话 state, 切对话时恢复
//   (与 wmsImages 持久化机制对齐, 见 state.js / conv-core.js)

// ★ 全局: 当前工作空间树缓存 (供 filterLayerTree 局部刷新用, 避免每次重拉)
let _layerTreeCache = null;        // {workspaces:[{name, layers_typed:[...]}], ...}
// ★ 左栏 Tab 状态: 'conv'(对话管理) | 'layer'(图层管理)
//   地图已固定在中间区域, 不再需要 map/layer 工作区切换。
let _sidebarActiveTab = 'conv';

// ★ 安全访问地图单例 (wms-render.js 的 _getMap; 若未加载返回 null)
function _lpMap() {
  return (typeof _getMap === 'function') ? _getMap() : null;
}

// ==================== 左栏 Tab 切换 (对话管理 / 图层管理) ====================

// 入参:
//   tab: 'conv'(对话管理) | 'layer'(图层管理), 表示左栏需要切换到的目标页签。
// 方法:
//   切换左栏两个 pane 的显示; 切到图层管理时自动拉取最新图层树。
// 出参:
//   无返回值; 成功时左栏页签与全局状态一致。
function switchSidebarTab(tab) {
  _sidebarActiveTab = tab;
  var paneConv = document.getElementById('sidebarPaneConv');
  var paneLayer = document.getElementById('sidebarPaneLayer');
  var tabConv = document.getElementById('sidebarTabConv');
  var tabLayer = document.getElementById('sidebarTabLayer');

  if (tab === 'layer') {
    if (paneConv) paneConv.style.display = 'none';
    if (paneLayer) paneLayer.style.display = '';
    if (tabConv) tabConv.classList.remove('active');
    if (tabLayer) tabLayer.classList.add('active');
    // 切到图层管理: 每次进入都重新从服务器拉取最新图层树 (不依赖本地缓存)
    //   为什么: 保证用户切回时看到 GeoServer 最新的工作空间/图层, 避免本地缓存过期导致的脏数据。
    refreshLayerPanel();
  } else {
    if (paneConv) paneConv.style.display = '';
    if (paneLayer) paneLayer.style.display = 'none';
    if (tabConv) tabConv.classList.add('active');
    if (tabLayer) tabLayer.classList.remove('active');
  }
}

// ★ 兼容保留: 地图现已固定显示在中间区域, 不再需要 map/layer 工作区切换。
//   旧调用方 (locateLayerFromPanel / uploadLayerFromPanel) 仍可能调用本函数,
//   这里仅确保地图容器触发尺寸重算, 避免隐藏后尺寸异常。
function switchWorkbenchTab(tab) {
  try { if (_lpMap()) setTimeout(function() { _lpMap().updateSize(); }, 50); } catch (e) {}
}

// ★ 对话切换时恢复 Tab (由 conv-core.js _restoreWmsPanelFromConv 调用)
//   地图固定显示后, 对话切换无需恢复工作区页签; 仅触发地图尺寸重算。
function _restoreWorkbenchTab(convId) {
  try { if (_lpMap()) setTimeout(function() { _lpMap().updateSize(); }, 50); } catch (e) {}
}

// ==================== 加载工作空间树 ====================

function refreshLayerPanel() {
  var tree = document.getElementById('layerTree');
  if (!tree) return;
  tree.innerHTML = '<div class="layer-tree-empty">加载中…</div>';

  fetch('/api/v1/geoserver/services')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.status !== 'success') {
        tree.innerHTML = '<div class="layer-tree-empty">加载失败: ' + escapeHtml(data.msg || 'GeoServer 不可用') + '</div>';
        _layerTreeCache = null;
        return;
      }
      _layerTreeCache = data;
      _renderLayerTree(data);
    })
    .catch(function(e) {
      tree.innerHTML = '<div class="layer-tree-empty">请求失败: ' + escapeHtml(e.message || String(e)) + '</div>';
      _layerTreeCache = null;
    });
}

// ★ 渲染工作空间树 (内部)
function _renderLayerTree(data) {
  var tree = document.getElementById('layerTree');
  if (!tree) return;
  // ★ 白名单过滤: 图层管理仅展示以 cd / samseg 开头的工作空间, 其余隐藏 (大小写不敏感)
  //   做什么: 渲染前剔除工作空间名不命中前缀规则(cd* / samseg*)的组。
  //   为什么: 本项目仅关心变化检测(cd*)与 samseg 工作空间, 全量展示其余工作空间对用户造成干扰。
  //   边界: 仅作用于面板树渲染; 不修改 _layerTreeCache 原始数据, 上传选空间/定位/折叠仍基于全量缓存。
  var workspaces = (data.workspaces || []).filter(function(ws) {
    var name = (ws.workspace || '').toLowerCase();
    return name.indexOf('cd') === 0 || name.indexOf('samseg') === 0;
  });
  if (workspaces.length === 0) {
    tree.innerHTML = '<div class="layer-tree-empty">GeoServer 中暂无工作空间/图层</div>';
    return;
  }
  var html = '';
  workspaces.forEach(function(ws) {
    var wsName = ws.workspace;
    // ★ 优先用 layers_typed (含 type), 兼容旧格式 layers (裸名数组)
    var layers = ws.layers_typed || (ws.layers || []).map(function(ln) {
      return { name: ln, full_name: wsName + ':' + ln, type: 'unknown' };
    });
    var collapsed = ws._collapsed ? 'collapsed' : '';
    html += '<div class="layer-ws-group ' + collapsed + '" data-ws="' + escapeHtml(wsName) + '">';
    html += '<div class="layer-ws-header" onclick="toggleWsGroup(this)">';
    html += '<span class="layer-ws-toggle">▼</span>';
    html += '<span class="layer-ws-name">📂 ' + escapeHtml(wsName) + '</span>';
    html += '<span class="layer-ws-count">' + layers.length + ' 个图层</span>';
    html += '</div>';
    html += '<div class="layer-ws-items">';
    if (layers.length === 0) {
      html += '<div class="layer-item" style="color:var(--text-muted)"><span class="layer-type-icon"></span><span class="layer-name">(空)</span></div>';
    } else {
      layers.forEach(function(lyr) {
        html += _renderLayerItem(lyr, wsName);
      });
    }
    html += '</div></div>';
  });
  tree.innerHTML = html;

  // ★ 渲染后应用当前搜索过滤
  applyLayerFilter();
  // ★ 同步各图层的显隐状态 (依据当前地图上已加载的图层)
  _syncLayerVisibilityFromMap();
}

// ★ 渲染单个图层行 (内部)
function _renderLayerItem(lyr, wsName) {
  var ltype = lyr.type || 'unknown';
  var fullName = lyr.full_name || (wsName + ':' + lyr.name);
  var iconChar = ltype === 'raster' ? '▦' : (ltype === 'vector' ? '◈' : '○');
  var iconClass = 'layer-type-icon ' + ltype;
  // ★ data 属性带上 full_name / ws / name / type, 供操作函数读取
  return ''
    + '<div class="layer-item" data-full="' + escapeHtml(fullName) + '" data-ws="' + escapeHtml(wsName) + '" data-name="' + escapeHtml(lyr.name) + '" data-type="' + escapeHtml(ltype) + '">'
    +   '<input type="checkbox" class="layer-vis-checkbox" title="显示/隐藏" onchange="toggleLayerVisibilityFromPanel(this)" />'
    +   '<span class="' + iconClass + '" title="' + escapeHtml(ltype) + '">' + iconChar + '</span>'
    +   '<span class="layer-name" title="点击定位并显示: ' + escapeHtml(fullName) + '" onclick="locateLayerFromPanel(\'' + escapeHtml(wsName) + '\',\'' + escapeHtml(lyr.name) + '\')">' + escapeHtml(lyr.name) + '</span>'
    +   '<span class="layer-meta-badge">' + escapeHtml(ltype) + '</span>'
    +   '<span class="layer-actions">'
    +     '<button class="layer-icon-btn" title="定位到图层范围" onclick="event.stopPropagation();locateLayerFromPanel(\'' + escapeHtml(wsName) + '\',\'' + escapeHtml(lyr.name) + '\')">🎯</button>'
    +     '<button class="layer-icon-btn danger" title="删除图层(不可恢复)" onclick="event.stopPropagation();deleteLayerFromPanel(\'' + escapeHtml(wsName) + '\',\'' + escapeHtml(lyr.name) + '\')">🗑</button>'
    +   '</span>'
    + '</div>';
}

// ★ 折叠/展开工作空间组
function toggleWsGroup(headerEl) {
  var group = headerEl.closest('.layer-ws-group');
  if (!group) return;
  group.classList.toggle('collapsed');
  // 记录折叠状态到缓存 (刷新时保持)
  var wsName = group.getAttribute('data-ws');
  if (_layerTreeCache && _layerTreeCache.workspaces) {
    _layerTreeCache.workspaces.forEach(function(ws) {
      if (ws.workspace === wsName) ws._collapsed = group.classList.contains('collapsed');
    });
  }
}

// ★ 从工作空间树缓存查图层类型 (vector/raster/unknown)
//   locateLayerFromPanel 等无 DOM 上下文的调用方据此决定是否套主题色 SLD。
//   入参: workspace(string), layerName(string)
//   方法: 遍历 _layerTreeCache.workspaces 命中同名图层, 返回其 type
//   出参: 'vector' | 'raster' | 'unknown'; 缓存缺失时返回 'unknown'
function _lookupLayerType(workspace, layerName) {
  if (!_layerTreeCache || !_layerTreeCache.workspaces) return 'unknown';
  for (var i = 0; i < _layerTreeCache.workspaces.length; i++) {
    var ws = _layerTreeCache.workspaces[i];
    if (ws.workspace !== workspace) continue;
    var layers = ws.layers_typed || (ws.layers || []).map(function(ln) {
      return { name: ln, type: 'unknown' };
    });
    for (var j = 0; j < layers.length; j++) {
      if (layers[j].name === layerName) return layers[j].type || 'unknown';
    }
  }
  return 'unknown';
}

// ★ 生成矢量图层主题色 SLD: 已迁移至 wms-render.js (全局兜底入口, 见 _makeVectorThemedSLD)

// ==================== 图层操作 (显隐 / 定位 / 删除) ====================

// ★ 显隐切换: 勾选 → 在地图叠加该图层; 取消 → 从地图移除
function toggleLayerVisibilityFromPanel(checkbox) {
  var item = checkbox.closest('.layer-item');
  if (!item) return;
  var ws = item.getAttribute('data-ws');
  var name = item.getAttribute('data-name');
  var fullName = item.getAttribute('data-full');
  var ltype = item.getAttribute('data-type');

  if (checkbox.checked) {
    // ★ 显示: 复用 showWmsInPanel (会自动 fit + 绑定 GetFeatureInfo)
    //   矢量图层若需本地叠加, 仍走 WMS (GeoServer 已发布, WMS 可出图)
    if (typeof showWmsInPanel === 'function') {
      // ★ 主题色 SLD 由 showWmsInPanel 内部全局兜底 (按图层类型自动判定, 无需调用方传)
      showWmsInPanel(name, ws, null, null, fullName + ' [图层管理]', false, { forceFit: false }).then(function(result) {
        if (!result.loaded) {
          checkbox.checked = false;
          alert('图层加载失败: ' + (result.error || '请检查 GeoServer/图层是否存在'));
        }
      });
    }
  } else {
    // ★ 隐藏: 从地图移除对应 OL 图层 (按 layerName + workspace 匹配)
    _removeMapLayerByName(name, ws);
  }
}

// ★ 从地图移除指定图层 (按 layerName/workspace 匹配, 同步反注册注册表)
function _removeMapLayerByName(layerName, workspace) {
  if (!_lpMap()) return;
  var toRemove = [];
  _lpMap().getLayers().forEach(function(layer) {
    if (layer.get('layerName') === layerName && (layer.get('workspace') || '') === (workspace || '')) {
      toRemove.push(layer);
    }
  });
  toRemove.forEach(function(layer) { _lpMap().removeLayer(layer); });
  // ★ 同步从注册表反注册 (wms-render.js)
  if (typeof _unregisterWmsLayer === 'function') {
    _unregisterWmsLayer(workspace || '', layerName);
  }
}

// ★ 定位到图层范围 (并确保该图层已显示)
//   流程: fetch bbox → 用精确 bbox 调 showWmsInPanel(加载+定位一步到位)→ 同步勾选状态。
//   v2.7: showWmsInPanel 内部统一走 TileWMS 并做了同 key 去重 + 矢量自动注入边界线 SLD,
//   因此对默认已加载的边界图层 (cd_shp:chengduqu) 定位时, 只会 fit 视图、不重复叠加,
//   不会与默认加载的 name 标注产生文字重影。
function locateLayerFromPanel(workspace, layerName) {
  var full = workspace + ':' + layerName;
  // 切到地图 Tab (让用户看到定位结果)
  switchWorkbenchTab('map');

  fetch('/api/v1/geoserver/layers/' + encodeURIComponent(workspace) + '/' + encodeURIComponent(layerName) + '/bbox')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var bbox4326 = null;
      if (data.status === 'success' && data.bbox) {
        var b = data.bbox;
        bbox4326 = [b.minx, b.miny, b.maxx, b.maxy];
      } else {
        console.warn('[图层面板] 获取范围失败, 用全局范围兜底:', data.msg);
      }

      // ★ 显式加载该图层到地图 (带精确 bbox → showWmsInPanel 会据此 fit 定位)
      //   重复定位同名图层时, showWmsInPanel 内部会去重 (只 fit 视图不重加), 无需调用方处理。
      if (typeof showWmsInPanel !== 'function') {
        console.warn('[图层面板] showWmsInPanel 不可用, 无法加载图层');
        return;
      }
      // ★ 主题色 SLD 由 showWmsInPanel 内部全局兜底 (按图层类型自动判定, 无需调用方传)
      showWmsInPanel(layerName, workspace, bbox4326, null, full + ' [定位]', false, { forceFit: true }).then(function(result) {
        if (!result.loaded) {
          console.warn('[图层面板] 图层加载失败:', result.error);
          alert('图层加载失败: ' + (result.error || '请检查 GeoServer/图层是否存在'));
          return;
        }
        // 加载成功后同步面板勾选状态
        _setLayerCheckbox(workspace, layerName, true);
      });
    })
    .catch(function(e) {
      console.warn('[图层面板] 定位请求失败:', e);
      alert('定位请求失败: ' + (e.message || e));
    });
}

// ★ 设置某图层面板行的勾选状态 (按 workspace+name 精确匹配, 容错 data-full 转义差异)
function _setLayerCheckbox(workspace, layerName, checked) {
  var rows = document.querySelectorAll('.layer-item');
  for (var i = 0; i < rows.length; i++) {
    var ws = rows[i].getAttribute('data-ws');
    var nm = rows[i].getAttribute('data-name');
    if (ws === workspace && nm === layerName) {
      var cb = rows[i].querySelector('.layer-vis-checkbox');
      if (cb) cb.checked = checked;
      return;
    }
  }
}

// ★ 删除图层 (二次确认, 破坏性操作)
function deleteLayerFromPanel(workspace, layerName) {
  var full = workspace + ':' + layerName;
  if (!confirm('确定删除图层 "' + full + '" 吗?\n\n该操作会从 GeoServer 永久移除该图层(含数据存储), 不可恢复, 可能影响引用它的其他数据。')) return;

  fetch('/api/v1/geoserver/layers/' + encodeURIComponent(workspace) + '/' + encodeURIComponent(layerName), {
    method: 'DELETE',
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.status === 'success') {
        // 先从地图移除 (若已显示)
        _removeMapLayerByName(layerName, workspace);
        // 刷新树
        refreshLayerPanel();
        alert('已删除: ' + full);
      } else {
        alert('删除失败: ' + (data.msg || '未知错误'));
      }
    })
    .catch(function(e) { alert('删除请求失败: ' + (e.message || e)); });
}

// ==================== 上传发布图层 ====================

// ★ 弹出"选择工作空间"模态: 现有工作空间下拉 / 新建输入, 返回选定的 workspace 名 (取消返回 null)
//   复用 .app-modal-overlay/.app-modal 样式 (与 msg-ui.js 的 _showAppModal 风格一致)。
function _promptWorkspace(kindLabel) {
  return new Promise(function(resolve) {
    // 从缓存或新拉取现有工作空间列表 (优先用缓存 _layerTreeCache)
    var existing = [];
    if (_layerTreeCache && _layerTreeCache.workspaces) {
      _layerTreeCache.workspaces.forEach(function(ws) { existing.push(ws.workspace); });
    }

    var overlay = document.createElement('div');
    overlay.className = 'app-modal-overlay show';
    var modal = document.createElement('div');
    modal.className = 'app-modal';
    var html = '';
    html += '<div class="app-modal-title">上传' + escapeHtml(kindLabel) + ' — 选择目标工作空间</div>';
    html += '<div class="app-modal-body" style="margin-bottom:10px;">图层将发布到所选 GeoServer 工作空间</div>';

    var radioExisting = 'ws_radio_existing';
    var radioNew = 'ws_radio_new';
    var selectId = 'ws_select_' + Date.now();
    var inputId = 'ws_input_' + Date.now();

    // 单选: 使用现有 / 新建
    html += '<div style="margin-bottom:6px;"><label style="cursor:pointer;font-size:var(--fs-md);display:flex;align-items:center;gap:6px;">'
      + '<input type="radio" name="ws_mode" id="' + radioExisting + '" checked style="accent-color:#2C6FBD;"> 使用现有工作空间'
      + '</label></div>';
    if (existing.length === 0) {
      html += '<div style="margin:0 0 10px 22px;color:var(--text-muted);font-size:var(--fs-sm);">(暂无工作空间, 请选择"新建")</div>';
    } else {
      html += '<select id="' + selectId + '" class="app-modal-input" style="margin:0 0 10px 22px;width:calc(100% - 22px);box-sizing:border-box;">';
      existing.forEach(function(ws) { html += '<option value="' + escapeHtml(ws) + '">' + escapeHtml(ws) + '</option>'; });
      html += '</select>';
    }

    html += '<div style="margin-bottom:6px;"><label style="cursor:pointer;font-size:var(--fs-md);display:flex;align-items:center;gap:6px;">'
      + '<input type="radio" name="ws_mode" id="' + radioNew + '" style="accent-color:#2C6FBD;"> 新建工作空间'
      + '</label></div>';
    html += '<input type="text" id="' + inputId + '" class="app-modal-input" placeholder="输入新工作空间名 (英文/数字)" disabled style="margin:0 0 10px 22px;width:calc(100% - 22px);box-sizing:border-box;" />';

    html += '<div class="app-modal-actions">';
    html += '<button class="app-modal-btn cancel">取消</button>';
    html += '<button class="app-modal-btn ok">确定</button>';
    html += '</div>';
    modal.innerHTML = html;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    var selectEl = modal.querySelector('#' + selectId);
    var inputEl = modal.querySelector('#' + inputId);
    var radioEx = modal.querySelector('#' + radioExisting);
    var radioNw = modal.querySelector('#' + radioNew);
    var okBtn = modal.querySelector('.app-modal-btn.ok');
    var cancelBtn = modal.querySelector('.app-modal-btn.cancel');

    function syncMode() {
      var useNew = radioNw.checked;
      if (selectEl) selectEl.disabled = useNew;
      if (inputEl) {
        inputEl.disabled = !useNew;
        if (useNew) setTimeout(function() { inputEl.focus(); }, 0);
      }
    }
    radioEx.onclick = syncMode;
    radioNw.onclick = syncMode;
    syncMode();

    var settled = false;
    function close(result) {
      if (settled) return;
      settled = true;
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      resolve(result);
    }
    okBtn.onclick = function() {
      var ws = '';
      if (radioNw.checked) {
        ws = (inputEl.value || '').trim();
        if (!ws) { alert('请输入新工作空间名称'); return; }
        // 工作空间名规范: 英文/数字/下划线
        if (!/^[A-Za-z0-9_]+$/.test(ws)) { alert('工作空间名只能包含英文、数字、下划线'); return; }
      } else if (selectEl) {
        ws = selectEl.value || '';
        if (!ws) { alert('请选择一个现有工作空间, 或选择"新建"'); return; }
      } else {
        // 无现有工作空间又没选新建
        alert('请选择"新建工作空间"并输入名称'); return;
      }
      close(ws);
    };
    cancelBtn.onclick = function() { close(null); };
    modal.addEventListener('keydown', function(e) {
      e.stopPropagation();
      if (e.key === 'Enter') { e.preventDefault(); okBtn.click(); }
      else if (e.key === 'Escape') { e.preventDefault(); cancelBtn.click(); }
    });
    overlay.addEventListener('click', function(e) { if (e.target === overlay) close(null); });
    setTimeout(function() { if (selectEl) selectEl.focus(); }, 0);
  });
}

// (已废弃) 旧的"输入本地 shapefile 路径"方案 (_promptLocalPath) 已移除。
//   原因: shapefile 是多文件组合, 输入本地磁盘路径依赖后端能访问用户磁盘, docker 部署下不可行。
//   现改为浏览器多选全部组件 → /files/upload-shapefile 同 stem 同目录落盘 → 发布。
//   见 uploadLayerFromPanel('vector') 分支。

// ★ 从面板上传文件并发布为 GeoServer 图层
//   kind: 'raster' (影像 GeoTIFF/COG) | 'vector' (矢量 SHP)
//   流程: 点击 → 弹"选择工作空间" → 矢量输本地 .shp 路径 / 栅格选文件上传 → 发布到选定工作空间
async function uploadLayerFromPanel(kind) {
  var kindLabel = (kind === 'raster') ? '影像' : '矢量';

  // ★ Step 1: 先弹"选择工作空间"对话框 (Bug2 修复: 不再直接弹文件框)
  var workspace = await _promptWorkspace(kindLabel);
  if (!workspace) return;  // 用户取消

  // ★ Step 2: 获取待发布文件的服务器路径 (矢量与栅格走不同路径)
  //   矢量(shapefile): 浏览器多选全部组件 (.shp/.shx/.dbf/.prj/...) → 一次 POST 到
  //     /files/upload-shapefile, 后端保证同 stem 同目录落盘, 返回 .shp 服务器路径。
  //     为什么不再用本地路径输入: shapefile 是多文件组合, 浏览器单文件上传只传 .shp,
  //     服务器无同目录 .shx/.dbf → 后端报"缺少必要文件: xxx.shx"; 而输入本地路径依赖
  //     后端能访问用户磁盘 (docker 部署下不可行)。改为上传全部组件既正确又适配部署。
  //   栅格(GeoTIFF): 浏览器选单文件 → /files/upload 拿服务器磁盘路径 (栅格为单文件, 无多组件问题)
  var localPath;
  if (kind === 'vector') {
    // 弹文件框: 多选 shapefile 全部组件
    var shpFiles = await new Promise(function(resolve) {
      var input = document.createElement('input');
      input.type = 'file';
      input.multiple = true;
      input.accept = '.shp,.shx,.dbf,.prj,.cpg,.sbn,.sbx,.fbn,.fbx,.ain,.aih,.qix';
      input.onchange = function() {
        resolve(Array.from(input.files));
      };
      input.click();
    });
    if (!shpFiles || shpFiles.length === 0) return;  // 用户取消

    // 前端预校验: 必须含 .shp; 缺 .shx/.dbf 直接提示 (减少无效上传)
    var hasShp = shpFiles.some(function(f) { return /\.shp$/i.test(f.name); });
    if (!hasShp) {
      alert('请选中完整的 shapefile 组件集 (至少包含 .shp 文件), 例如同时选中 chengduqu.shp / .shx / .dbf / .prj。');
      return;
    }

    // 上传组件到服务器 (后端保证同 stem 同目录落盘)
    if (!activeConvId && (typeof newConversation === 'function')) newConversation();
    var vfd = new FormData();
    shpFiles.forEach(function(f) { vfd.append('files', f); });
    vfd.append('conversation_id', activeConvId || '');
    try {
      var vResp = await fetch('/api/v1/files/upload-shapefile', { method: 'POST', body: vfd });
      var vData = await vResp.json().catch(function() { return { detail: '服务器返回无效响应 (HTTP ' + vResp.status + ')' }; });
      if (vResp.status === 400 && vData.detail) throw new Error(vData.detail);
      if (vData.status !== 'success') throw new Error(vData.msg || vData.detail || '上传失败 (HTTP ' + vResp.status + ')');
      localPath = vData.shp_path;
      // 后端可能返回组件不全的 warnings (尽管 .shp 已落盘), 提示用户但继续发布
      if (vData.warnings && vData.warnings.length > 0) {
        console.warn('[Upload] shapefile 组件提示:', vData.warnings.join('; '));
      }
    } catch (e) {
      alert('矢量组件上传失败: ' + (e.message || e));
      return;
    }
  } else {
    var file = await new Promise(function(resolve) {
      var input = document.createElement('input');
      input.type = 'file';
      input.accept = '.tif,.tiff,.cog';
      input.onchange = function() {
        var files = Array.from(input.files);
        resolve(files.length > 0 ? files[0] : null);
      };
      // 注意: 多数浏览器无法监听"取消", onchange 不触发则 Promise 不 resolve (用户取消即不继续)
      input.click();
    });
    if (!file) return;

    // 栅格: 上传文件到服务器 (复用 /api/v1/files/upload 拿磁盘路径)
    if (!activeConvId && (typeof newConversation === 'function')) newConversation();
    var fd = new FormData();
    fd.append('files', file);
    fd.append('conversation_id', activeConvId || '');
    try {
      var upResp = await fetch('/api/v1/files/upload', { method: 'POST', body: fd });
      if (!upResp.ok) throw new Error('上传失败 HTTP ' + upResp.status);
      var upData = await upResp.json();
      var paths = upData.paths || [];
      if (paths.length === 0) throw new Error('上传返回空路径');
      localPath = paths[0];
    } catch (e) {
      alert('文件上传失败: ' + (e.message || e));
      return;
    }
  }

  // ★ Step 3: 发布到选定工作空间 (POST /api/v1/geoserver/layers)
  try {
    var pubResp = await fetch('/api/v1/geoserver/layers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        file_path: localPath,
        layer_name: '',
        workspace: workspace,
        layer_type: kind,
      }),
    });
    var pubData = await pubResp.json();
    if (pubData.status === 'success') {
      refreshLayerPanel();
      var pubWs = pubData.workspace || workspace;
      var pubLayer = pubData.layer_name || '';
      if (confirm('发布成功: ' + pubWs + ':' + pubLayer + '\n\n是否立即在地图上显示?')) {
        if (typeof showWmsInPanel === 'function') {
          // ★ 主题色 SLD 由 showWmsInPanel 内部全局兜底 (按图层类型自动判定, 无需调用方传)
          showWmsInPanel(pubLayer, pubWs, null, null, '新发布图层: ' + pubLayer, false, { forceFit: false });
          switchWorkbenchTab('map');
        }
      }
    } else {
      alert('发布失败: ' + (pubData.msg || '未知错误'));
    }
  } catch (e) {
    alert('发布请求失败: ' + (e.message || e));
  }
}

// ==================== 搜索过滤 ====================

function filterLayerTree() {
  applyLayerFilter();
}

// ★ 应用搜索过滤 (不重新请求, 仅隐藏不匹配项)
function applyLayerFilter() {
  var input = document.getElementById('layerSearchInput');
  if (!input) return;
  var kw = (input.value || '').trim().toLowerCase();
  var groups = document.querySelectorAll('.layer-ws-group');
  groups.forEach(function(group) {
    var wsName = group.getAttribute('data-ws') || '';
    var items = group.querySelectorAll('.layer-item');
    var visibleCount = 0;
    items.forEach(function(item) {
      var name = (item.getAttribute('data-name') || '').toLowerCase();
      var fullWs = wsName.toLowerCase();
      var match = !kw || name.indexOf(kw) >= 0 || fullWs.indexOf(kw) >= 0;
      item.style.display = match ? '' : 'none';
      if (match) visibleCount++;
    });
    // 工作空间组: 组名匹配或内有匹配项 → 显示; 否则隐藏
    var wsMatch = !kw || wsName.toLowerCase().indexOf(kw) >= 0;
    group.style.display = (wsMatch || visibleCount > 0) ? '' : 'none';
    // 组名匹配时展开 (避免折叠态看不到结果)
    if (wsMatch && kw) group.classList.remove('collapsed');
  });
}

// ==================== 地图 ↔ 面板 状态同步 ====================

// ★ 同步: 根据当前地图上已加载的 OL 图层, 勾选面板对应行
//   (AI 加载的图层 / 上次显示的图层, 切到面板时应反映为已勾选)
function _syncLayerVisibilityFromMap() {
  if (!_lpMap()) return;
  var loadedKeys = {};
  _lpMap().getLayers().forEach(function(layer) {
    var ln = layer.get('layerName');
    var ws = layer.get('workspace') || '';
    if (ln) loadedKeys[ws + '|' + ln] = true;
  });
  document.querySelectorAll('.layer-item').forEach(function(item) {
    var ws = item.getAttribute('data-ws') || '';
    var name = item.getAttribute('data-name') || '';
    var cb = item.querySelector('.layer-vis-checkbox');
    if (cb) cb.checked = !!loadedKeys[ws + '|' + name];
  });
}

// ★ 地图图层变化时刷新勾选 (供 wms-render.js 在 showWmsInPanel 后调用)
function syncLayerPanelCheckboxes() {
  if (_sidebarActiveTab === 'layer') _syncLayerVisibilityFromMap();
}

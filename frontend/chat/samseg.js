// ==================== SamSeg 遥感分析: 上传图片 → 自动发分割/变化检测指令 ====================
// 流程: <input type=file> 选图 → POST /api/v1/samseg/upload (multipart) → 拿到路径 → 发对话指令
// 把后端返回的上传绝对路径转成前端可访问的 URL (走 /api/v1/upload 端点)
// ★ v2.3: 后端返回形如 "E:/.../agent-files/samseg/send/{conv_id}/xxx.png", 取 samseg/send/ 之后的部分拼 URL
// ★ 功能区: AI 聊天 (chat/) | SamSeg 上传 + 对话动作 (newConversation/stopChat)

function _uploadPathToUrl(absPath) {
  if (!absPath) return null;
  const norm = absPath.replace(/\\/g, '/');
  const idx = norm.toLowerCase().indexOf('samseg/send/');
  if (idx < 0) return null;
  const rel = norm.slice(idx + 'samseg/send/'.length);
  // ★ URL-encode 每个路径段: 文件名中的空格 / 中文 / 特殊字符需编码, 否则浏览器 <img> 加载失败
  return '/api/v1/upload/' + rel.split('/').map(encodeURIComponent).join('/');
}

function _isPreviewableImage(file) {
  const type = (file && file.type ? file.type.toLowerCase() : '');
  if (type.startsWith('image/')) return true;
  const name = (file && file.name ? file.name.toLowerCase() : '');
  return /\.(png|jpg|jpeg|gif|bmp|webp|svg|tif|tiff)$/i.test(name);
}

// ★ 查询影像是否带 CRS (调后端 /api/v1/image/meta), 供上传后即时校验
//   复用 wms-render.js 的 _fetchImageMeta 缓存, 避免重复请求; 查询失败时保守放行 (不误伤)
async function _imageHasCrs(absPath) {
  if (!absPath) return true; // 无路径无法校验, 放行交由后端兜底
  // 优先复用全局缓存查询函数 (wms-render.js)
  if (typeof _fetchImageMeta === 'function') {
    try {
      const meta = await _fetchImageMeta(absPath);
      if (meta) return !!meta.has_crs;
    } catch (e) { /* 查询失败, 保守放行 */ }
    return true;
  }
  // 兜底: 直接 fetch
  try {
    const r = await fetch('/api/v1/image/meta?path=' + encodeURIComponent(absPath));
    const meta = await r.json();
    return !!meta.has_crs;
  } catch (e) {
    return true; // 网络异常不阻断上传 (后端硬拦截是最终防线)
  }
}

// ★ 返回 paths 中第一个无 CRS 影像 (用于一次性多图校验); 全部有坐标返回 null
async function _findFirstNoCrsImage(paths) {
  for (const p of paths) {
    const ok = await _imageHasCrs(p);
    if (!ok) return p;
  }
  return null;
}

function _buildUploadPrompt(files, paths) {
  if (!paths || paths.length === 0) return '';
  if (paths.length === 1) {
    const file = files[0];
    const name = file && file.name ? file.name : paths[0].split(/[\\/]/).pop();
    return `文件路径: ${paths[0]} 。文件名: ${name} 。`;
  }
  const parts = paths.map(function(p, i) {
    const file = files[i];
    const name = file && file.name ? file.name : p.split(/[\\/]/).pop();
    return `${i + 1}. ${name}: ${p}`;
  });
  return '文件路径如下：\n' + parts.join('\n') + '\n';
}

// ★ 输入区上传按钮: 上传通用资料后把路径填入输入框, 由用户自己写指令再发送
// 与 demo-bar 的 uploadAndAnalyze 区别: 不自动构造指令发送, 让用户自主编辑
async function uploadToInput() {
  const st = cs();
  if (st && st.isSending) { alert('当前对话正在处理中，请稍候'); return; }

  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.png,.jpg,.jpeg,.gif,.bmp,.webp,.svg,.tif,.tiff,.json,.geojson,.md,.markdown,.pdf,.txt,.csv,.tsv,.xlsx,.xls,.doc,.docx,.zip,.shp,.shx,.dbf,.prj,.cpg,.sbn,.sbx,.fbn,.fbx,.ain,.aih,.qix';
  input.multiple = true;
  input.onchange = async () => {
    const files = Array.from(input.files);
    if (files.length === 0) return;

    if (!activeConvId) newConversation();

    try {
      // Step 1: 按类型预分流 — shapefile 组件单独走 /files/upload-shapefile, 其余走 /files/upload。
      //   ★ shapefile (.shp/.shx/.dbf/.prj/...) 是多文件组合, 若和其他文件一起走 /files/upload,
      //     后端会按 conv_prefix_stem 各自命名, .shx/.dbf 与 .shp 不再同 stem → 发布报
      //     "缺少必要文件: xxx.shx"。故 shapefile 组件先剥离, 单独上传保证同 stem 同目录。
      const SHP_EXT_RE = /\.(shp|shx|dbf|prj|cpg|sbn|sbx|fbn|fbx|ain|aih|qix)$/i;
      const otherFiles = files.filter(f => !SHP_EXT_RE.test(f.name));
      const shpComponentFiles = files.filter(f => SHP_EXT_RE.test(f.name));

      // 前端预校验: 选了 shapefile 组件但缺 .shp 主文件 → 直接提示, 不上传
      if (shpComponentFiles.length > 0 && !shpComponentFiles.some(f => /\.shp$/i.test(f.name))) {
        alert('检测到 shapefile 组件 (.shx/.dbf/...) 但缺少 .shp 主文件。\n请把完整的 .shp / .shx / .dbf / .prj 一起选中再上传。');
        return;
      }

      // 1a) 非组件文件 → /files/upload
      const paths = [];
      if (otherFiles.length > 0) {
        const fd = new FormData();
        otherFiles.forEach(f => fd.append('files', f));
        fd.append('conversation_id', activeConvId || '');
        const resp = await fetch('/api/v1/files/upload', { method: 'POST', body: fd });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.detail || `上传失败 (HTTP ${resp.status})`);
        }
        const data = await resp.json();
        paths.push(...(data.paths || []));
      }

      // Step 2: 按 otherFiles 顺序映射 paths → 影像/AI 资料分流
      //   shapefile 组件的发布路径由 Step 2.5 单独获取。
      const WS = 'cd_upload';
      const gsFiles = [];        // 影像: { path, file, type:'raster' }
      const aiFiles = [];        // 其他: { path, file }

      for (let i = 0; i < otherFiles.length; i++) {
        const p = paths[i];
        const f = otherFiles[i];
        const name = (f && f.name ? f.name : p).toLowerCase();
        if (/\.(tif|tiff|cog)$/i.test(name)) {
          gsFiles.push({ path: p, file: f, type: 'raster' });
        } else {
          aiFiles.push({ path: p, file: f });
        }
      }

      // ★ Step 2.5: shapefile 组件 → 一次性上传到 /files/upload-shapefile
      //   后端按 .shp 的 stem 把同 stem 组件落同一目录, 返回 .shp 的服务器路径。
      const shpEntries = [];  // { shp_path, file(.shp 原始 File, 用于显示名) }
      if (shpComponentFiles.length > 0) {
        try {
          const vfd = new FormData();
          shpComponentFiles.forEach(function(f) { vfd.append('files', f); });
          vfd.append('conversation_id', activeConvId || '');
          const vResp = await fetch('/api/v1/files/upload-shapefile', { method: 'POST', body: vfd });
          const vData = await vResp.json().catch(function() { return { detail: '服务器返回无效响应 (HTTP ' + vResp.status + ')' }; });
          if (vResp.status === 400 && vData.detail) throw new Error(vData.detail);
          if (vData.status !== 'success') throw new Error(vData.msg || vData.detail || '上传失败 (HTTP ' + vResp.status + ')');
          if (vData.warnings && vData.warnings.length > 0) {
            console.warn('[Upload] shapefile 组件提示:', vData.warnings.join('; '));
          }
          if (vData.shp_path) {
            const shpFile = shpComponentFiles.find(function(f) { return /\.shp$/i.test(f.name); });
            shpEntries.push({ shp_path: vData.shp_path, file: shpFile });
          }
        } catch (e) {
          alert('矢量组件上传失败: ' + (e.message || e));
        }
      }

      // Step 3: 影像/SHP → 发布到 GeoServer cd_upload 工作空间, 然后加载到地图
      //   影像直接用 /files/upload 返回的 path; shapefile 用 /files/upload-shapefile 返回的 shp_path。
      //   统一封装为 publishAndLoad(shp_path/path, layer_type, displayName)
      async function publishAndLoad(filePath, layerType, displayName) {
        try {
          const pubResp = await fetch('/api/v1/geoserver/layers', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              file_path: filePath,
              layer_name: '',
              workspace: WS,
              layer_type: layerType,
            }),
          });
          const pubData = await pubResp.json();
          if (pubData.status !== 'success') {
            alert('发布失败 (' + (displayName || '') + '): ' + (pubData.msg || '未知错误'));
            return;
          }
          const pubWs = pubData.workspace || WS;
          const pubLayer = pubData.layer_name || '';
          const cap = '上传: ' + (displayName || pubLayer);
          // 刷新图层面板 (新图层出现在 cd_upload 树下)
          if (typeof refreshLayerPanel === 'function') refreshLayerPanel();
          // 获取图层 bbox → 加载到地图并定位
          try {
            const bboxResp = await fetch(
              '/api/v1/geoserver/layers/' + encodeURIComponent(pubWs) + '/' + encodeURIComponent(pubLayer) + '/bbox'
            );
            const bboxData = await bboxResp.json();
            var bbox4326 = null;
            if (bboxData.status === 'success' && bboxData.bbox) {
              var b = bboxData.bbox;
              bbox4326 = [b.minx, b.miny, b.maxx, b.maxy];
            }
            if (typeof showWmsInPanel === 'function') {
              showWmsInPanel(pubLayer, pubWs, bbox4326, null, cap + ' [WMS]', false, { forceFit: true });
            }
          } catch (e) {
            console.warn('[Upload] 图层加载到地图失败:', pubWs + ':' + pubLayer, e);
          }
        } catch (e) {
          alert('GeoServer 发布请求失败 (' + (displayName || '') + '): ' + (e.message || e));
        }
      }

      // 影像: 逐个发布
      for (const gs of gsFiles) {
        await publishAndLoad(gs.path, 'raster', gs.file ? gs.file.name : '');
      }
      // shapefile: 用组件上传返回的 .shp 路径发布
      for (const se of shpEntries) {
        await publishAndLoad(se.shp_path, 'vector', se.file ? se.file.name : '');
      }

      // Step 4: 非影像/SHP 文件 → 路径填入输入框, 由用户编辑指令后发送给 AI
      if (aiFiles.length > 0) {
        const inputBox = document.getElementById('inputBox');
        inputBox.value = _buildUploadPrompt(
          aiFiles.map(f => f.file),
          aiFiles.map(f => f.path)
        );
        inputBox.focus();
        inputBox.setSelectionRange(inputBox.value.length, inputBox.value.length);
      }
    } catch (e) {
      alert('文件上传失败: ' + e.message);
    }
  };
  input.click();
}

async function uploadAndAnalyze(task) {
  // task: 'segment' (单图分割) | 'change' (双图变化检测)
  const isChange = task === 'change';
  const maxFiles = isChange ? 2 : 1;

  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'image/*';
  input.multiple = isChange;
  input.onchange = async () => {
    const files = Array.from(input.files).slice(0, maxFiles);
    if (files.length === 0) return;
    if (isChange && files.length < 2) {
      alert('变化检测需要选择 2 张图片（前一时相和后一时相）');
      return;
    }

    // 提示用户正在上传
    const st = cs();
    if (st && st.isSending) { alert('当前对话正在处理中，请稍候'); return; }

    // ★ 修复: 先确保有活跃对话, 再 showImageInPanel (时序见 uploadToInput)
    if (!activeConvId) newConversation();

    const st2 = cs();
    if (st2) appendUserMessageTo(st2, '正在上传图片...');

    try {
      const fd = new FormData();
      files.forEach(f => fd.append('images', f));
      // ★ 带上会话 id, 后端按 {conv_id}_{原名} 命名
      fd.append('conversation_id', activeConvId || '');
      const resp = await fetch('/api/v1/samseg/upload', { method: 'POST', body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `上传失败 (HTTP ${resp.status})`);
      }
      const data = await resp.json();
      const paths = data.paths || [];

      // ★ 系统只接受带坐标影像: 上传后即时校验 CRS, 无坐标则拒绝分析 (与后端硬拦截一致)
      const badFile = await _findFirstNoCrsImage(paths);
      if (badFile) {
        alert('该影像无地理坐标(CRS)，系统仅接受带坐标的 GeoTIFF/COG 影像。');
        return;
      }

      // ★ 预览: 先发布到 GeoServer (samseg 工作空间), 再从 WMS 加载到地图
      //   分割/变化检测结果返回时后端会以 _original 后缀再次发布, 与预览不冲突
      const PREVIEW_WS = 'samseg';
      for (let i = 0; i < paths.length; i++) {
        const p = paths[i];
        const f = files[i];
        const cap = isChange
          ? '输入影像 ' + (i === 0 ? 'T1' : 'T2') + ': ' + (f ? f.name : '')
          : '输入影像: ' + (files[0] ? files[0].name : '');
        try {
          const pubResp = await fetch('/api/v1/geoserver/layers', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              file_path: p,
              layer_name: '',
              workspace: PREVIEW_WS,
              layer_type: 'raster',
            }),
          });
          const pubData = await pubResp.json();
          if (pubData.status !== 'success') {
            console.warn('[Preview] GeoServer 发布失败:', pubData.msg);
            continue;
          }
          const pubWs = pubData.workspace || PREVIEW_WS;
          const pubLayer = pubData.layer_name || '';
          const bboxResp = await fetch(
            '/api/v1/geoserver/layers/' + encodeURIComponent(pubWs) + '/' + encodeURIComponent(pubLayer) + '/bbox'
          );
          const bboxData = await bboxResp.json();
          var bbox4326 = null;
          if (bboxData.status === 'success' && bboxData.bbox) {
            var b = bboxData.bbox;
            bbox4326 = [b.minx, b.miny, b.maxx, b.maxy];
          }
          if (typeof showWmsInPanel === 'function') {
            showWmsInPanel(pubLayer, pubWs, bbox4326, null, cap + ' [WMS]', false, { forceFit: true });
          }
        } catch (e) {
          console.warn('[Preview] 预览加载失败:', p, e);
        }
      }

      // 构造对话指令 (LLM 据此调用 segment_image / detect_change 工具)
      let prompt;
      if (isChange) {
        prompt = `请对这两张图片做变化检测，对比哪里发生了地物变化。T1 图片路径: ${paths[0]}，T2 图片路径: ${paths[1]}`;
      } else {
        prompt = `请对这张图片做语义分割，识别其中的地物类别。图片路径: ${paths[0]}`;
      }
      // activeConvId 已在上面确保
      document.getElementById('inputBox').value = '';
      doSend(prompt);
    } catch (e) {
      alert('图片上传失败: ' + e.message);
    }
  };
  input.click();
}

// ★ 记忆清空: 破坏性操作, 先确认再发送纯意图 (让 AI 自己思考调用哪个清空工具)
function clearMemoryDemo() {
  if (!confirm('确定要清空全部长期记忆吗？\n\n这会让 AI 忘记所有从历史对话中积累的用户偏好和常用实体，操作不可恢复。')) return;
  // 只发送自然语言意图, AI 会按 CoT Step1-2 自主决定调用 clear_user_memory 并设置 confirm=true
  sendDemo('请帮我清空你记住的关于我的所有信息');
}

let _stopTimeoutId = null;

function stopChat() {
  if (!activeConvId) return;
  const st = cs();
  if (!st || !st.isSending) return;

  const stopBtn = document.getElementById('stopBtn');
  // ★ 仅用背景色 + 禁用表达"停止中"态, 不用 textContent (会抹掉按钮内的 SVG 图标)
  stopBtn.style.background = 'var(--accent-amber)';
  stopBtn.disabled = true;
  addEventLog('out', 'stop_chat', `conv=${activeConvId.slice(0, 12)}...`);

  // 发送停止指令 (指定 conversation_id)
  wsClient.sendStop(activeConvId);

  // 本地标记
  st._stopped = true;
  st._stopTimeoutId = setTimeout(() => {
    _markBubbleStopped();
    st.isSending = false;
    updateInputState();
      }, 2000);

  // ★ 恢复按钮默认态: 仅复位背景与可用性, 不动 textContent (保留 SVG)
  stopBtn.style.background = '';
  stopBtn.disabled = false;
  addEventLog('out', 'stop_chat', '✓ 已发送停止指令', activeConvId);
}

function _markBubbleStopped() {
  const st = cs();
  if (!st || !st.currentAssistantEl) return;
  const cursor = st.currentAssistantEl.querySelector('.thinking-block .cursor');
  if (cursor) cursor.remove();

  const bubble = st.currentAssistantEl.querySelector('.message-bubble');
  if (bubble) {
    const marker = document.createElement('div');
    marker.className = 'action-block';
    marker.style.cssText = 'border-left-color:var(--accent-red);color:var(--accent-red);margin-top:8px;';
    marker.textContent = '对话已停止';
    bubble.appendChild(marker);
  }
  st.currentAssistantEl = null;
  st.currentContent = '';
}

function newConversation() {
  const newId = crypto.randomUUID ? crypto.randomUUID() : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => { const r = Math.random()*16|0; return (c=='x'?r:(r&0x3|0x8)).toString(16); });

  document.querySelectorAll('.messages-conv').forEach(el => el.style.display = 'none');

  getConvState(newId).title = '(新对话)';
  if (typeof recordConversationEventLog === 'function') {
    recordConversationEventLog(newId, 'out', 'new_conversation', '创建新对话');
  }
  activeConvId = newId;
  wsClient.conversationId = newId;
  registerConvCallbacks(newId);

  // 显示新对话容器
  const st = convStates[newId];
  st.container.style.display = '';
  st._callbacksRegistered = true;

  // ★ 工作区随会话绑定: 新会话的 wmsImages 为空 → 清空面板并显示空提示,
  //   避免上一个会话的影像/结果图残留在工作区
  _restoreWmsPanelFromConv(newId);
  
  // ★ 更新本地缓存，使新对话出现在树中
  _convListCache.unshift({ id: newId, title: '(新对话)', project_id: activeProjectId || null, updated_at: new Date().toISOString(), message_count: 0, status: '' });
  
  // ★ 如果当前有选中的项目，异步通知后端将新对话归属到该项目
  if (activeProjectId) {
    const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
    const host = location.host || 'localhost:8020';
    fetch(`${protocol}//${host}/api/v1/conversations/${newId}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: activeProjectId }),
    }).catch(() => {});
  }
  
  renderConvTree();  // 刷新树形结构显示
  
  updateInputState();
  addEventLog('out', '新对话', newId.slice(0, 8) + '...');
  scrollBottom();
}

// ★ v2.5 消息分支: 从指定用户消息节点"重新生成"
// 流程: fork (隐藏旧分支) → 清空当前 UI → 用原 prompt 重新发对话
// 注意: fork 后 active_leaf_node = 分叉点, append_message 会自动挂到新分支
async function handleRegenerate(nodeId, originalPrompt) {
  const convId = activeConvId;
  if (!convId || !nodeId) return;

  // 弹确认 (避免误点)
  const ok = confirm('从这条消息重新生成?\n(当前分支之后的对话会隐藏, 可通过历史保留)');
  if (!ok) return;

  // Step 1: 调 fork 端点 (隐藏分叉点之后的旧消息)
  try {
    const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
    const host = location.host || 'localhost:8020';
    const res = await fetch(`${protocol}//${host}/api/v1/conversations/${convId}/fork`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parent_node_id: nodeId }),
    });
    const data = await res.json();
    if (data.status !== 'success') {
      alert('重新生成失败: ' + (data.msg || '未知错误'));
      return;
    }
    addEventLog('out', 'fork', `node=${nodeId.slice(0, 8)}...`);
  } catch (e) {
    alert('网络错误: ' + e.message);
    return;
  }

  // Step 2: 重新加载对话 UI (只显示 fork 点及之前的消息)
  await loadConvMessages(convId);

  // Step 3: 用原 prompt 重新发对话 (append_message 自动挂到新分支)
  // ★ 不重新 appendUserMessage (doSend 会自己加), 但要让用户看到是"重新生成"
  doSend(originalPrompt);
}

function doSend(prompt) {
  const st = cs();
  if (!st) return;
  if (st._stopTimeoutId) { clearTimeout(st._stopTimeoutId); st._stopTimeoutId = null; }
  st._stopped = false;
  st.isSending = true;
  st.title = prompt.slice(0, 20);
  updateInputState();
    addEventLog('out', 'chat_request', prompt);
  appendUserMessageTo(st, prompt);

  // ★ v2.4: 携带当前项目 ID (工作区激活项目 > 缓存记录)
  let pid = activeProjectId || null;
  if (activeConvId && !pid) {
    const c = _convListCache.find(x => x.id === activeConvId);
    pid = c ? (c.project_id || null) : null;
  }

  if (!wsClient || !wsClient.ws || wsClient.ws.readyState !== WebSocket.OPEN) {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = location.host || 'localhost:8020';
    wsClient.connect(`${protocol}//${host}/api/v1/agent/ws/chat`);
    setTimeout(() => {
      wsClient.sendChat({ prompt, conversation_id: activeConvId || null, project_id: pid });
    }, 500);
  } else {
    wsClient.sendChat({
      prompt,
      conversation_id: activeConvId || null,
      project_id: pid,
    });
  }
}

function updateInputState() {
  const st = cs();
  const isSending = st ? st.isSending : false;
  document.getElementById('sendBtn').disabled = isSending;
  document.getElementById('stopBtn').style.display = isSending ? 'flex' : 'none';
}

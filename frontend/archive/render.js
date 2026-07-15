// ==================== 多对话状态管理 ====================
// ★ convStates: 每个对话的独立状态 (key = conversation_id)
//   { isSending, _stopped, currentAssistantEl, currentContent,
//     thinkingBuffer, thinkingTimer, thinkingTokenCount, _stopTimeoutId,
//     title, messagesContainer (DOM element) }
let convStates = {};
let activeConvId = null;     // 当前活跃显示的对话 ID
let wsClient = null;
// ★ v2.4 工作区状态: 当前激活文件夹 → 项目关联
let activeProjectPath = null;
let activeProjectId = null;
let workspaceTreeCache = null;

// UUID 生成 (兼容旧浏览器)
function generateUUID() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
  });
}

// 获取 or 创建对话状态
function getConvState(convId) {
  if (!convStates[convId]) {
    const container = document.createElement('div');
    container.className = 'messages-conv';
    container.dataset.convId = convId;
    container.style.display = 'none';
    document.getElementById('messages').appendChild(container);

    convStates[convId] = {
      isSending: false,
      _stopped: false,
      currentAssistantEl: null,
      currentContent: '',
      thinkingBuffer: '',
      thinkingTimer: null,
      thinkingTokenCount: 0,
      _stopTimeoutId: null,
      title: '(新对话)',
      container: container,
      // ★ 一阶段一组 UI: 当前轮次配对容器 + 该组是否已配对工具
      //   状态机: thinking 到来时若已配对工具 → 开新组; 否则沿用当前组
      //           tool_call 到来时 → 追加到当前组并标记 hasTool=true
      currentIterationGroup: null,
      currentGroupHasTool: false,
      // ★ 每个对话独立维护的遥感影像列表: [{type, url, caption, legend, ...}, ...]
      //   type: 'image' (本地分析结果) | 'wms' (GeoServer 图层)
      //   切换对话时按此快照重建 wmsViewport, 实现影像随对话切换
      // ★ 同时持久化到 localStorage (key=nrms_wms_<convId>), 刷新页面后仍可恢复
      wmsImages: _loadConvWms(convId),
    };
  }
  return convStates[convId];
}

// ★ 影像清单 localStorage 持久化 (修复刷新后影像面板全空的 bug)
//   key: nrms_wms_<convId>, value: wmsImages 数组 (JSON)
function _loadConvWms(convId) {
  try {
    const raw = localStorage.getItem('nrms_wms_' + convId);
    return raw ? JSON.parse(raw) : [];
  } catch (e) { return []; }
}
function _persistConvWms(convId) {
  try {
    const st = convStates[convId];
    if (!st) return;
    localStorage.setItem('nrms_wms_' + convId, JSON.stringify(st.wmsImages || []));
  } catch (e) {}
}
function _clearConvWms(convId) {
  try { localStorage.removeItem('nrms_wms_' + convId); } catch (e) {}
}

// 当前活跃对话的状态引用 (便捷访问)
function cs() { return activeConvId ? convStates[activeConvId] : null; }

// ==================== Tab 栏管理 ====================


function switchToConv(convId) {
  if (convId === activeConvId) return;
  // ★ 切换前: 把当前预览面板的影像快照存到旧对话 state
  _snapshotWmsPanelToConv(activeConvId);
  // 隐藏旧对话
  if (activeConvId && convStates[activeConvId]) {
    convStates[activeConvId].container.style.display = 'none';
  }
  // 显示新对话
  const st = getConvState(convId);
  st.container.style.display = '';
  activeConvId = convId;
  wsClient.conversationId = convId;
    updateInputState();
  // ★ 切换后: 按新对话的快照重建预览面板
  _restoreWmsPanelFromConv(convId);
  // ★ 同步侧栏高亮
  document.querySelectorAll('.conv-item').forEach(el => el.classList.remove('active'));
  const sideItem = document.querySelector(`.conv-item[data-conv-id="${convId}"]`);
  if (sideItem) sideItem.classList.add('active');
  scrollBottom();
}

// ★ 把当前预览面板的影像快照存到指定对话的 state.wmsImages
//   不读 DOM, 而是信任"加载时已记录到 state"的原则;
//   这里只做兜底: 若 state.wmsImages 与 DOM 不同步, 以 state 为准 (DOM 是渲染产物)
function _snapshotWmsPanelToConv(convId) {
  // 实际的 wmsImages 在 showImageInPanel/showWmsInPanel 加载时已实时记录,
  // 此处无需额外操作。保留函数占位以便未来扩展 (如按 DOM 校验)。
}

// ★ 按指定对话的 state.wmsImages 重建预览面板
//   清空当前 viewport → 逐项重新渲染 (走 showImageInPanel/showWmsInPanel)
//   重建是异步的 (图片要重新 onload), 不阻塞对话切换
function _restoreWmsPanelFromConv(convId) {
  const st = convStates[convId];
  if (!st) return;
  const viewport = document.getElementById('wmsViewport');
  if (!viewport) return;
  // 清空当前内容 (来自上一个对话)
  viewport.innerHTML = '';
  closeWmsPopup();

  if (!st.wmsImages || st.wmsImages.length === 0) {
    // 该对话无影像 → 显示空提示
    showWmsEmpty();
    return;
  }

  // 逐项重建 (不传 record=false 的内部调用, 避免重复 push 回同一 state)
  st.wmsImages.forEach(function(item) {
    if (item.type === 'wms') {
      showWmsInPanel(item.layerName, item.workspace, item.bbox, item.wmsUrl, item.caption, false);
    } else {
      showImageInPanel(item.url, item.caption, item.legend, false);
    }
  });
}


function activateConversation(convId, title) {
  const st = getConvState(convId);
  if (title) st.title = title;

  // ★ 如果该对话还没有注册回调, 初始化它
  if (!st._callbacksRegistered) {
    st._callbacksRegistered = true;
    registerConvCallbacks(convId);
    loadConvMessages(convId);
  }

  switchToConv(convId);
}

// 为指定对话注册 WebSocket 回调
function registerConvCallbacks(convId) {
  const st = getConvState(convId);
  wsClient.registerConversation(convId, {
    onThinking: (content) => {
      if (activeConvId !== convId) return; // 只渲染活跃对话
      if (st._stopped) return;
      accumulateThinkingLog(content);
      appendThinking(content);
    },
    onToolCall: (toolCalls) => {
      if (activeConvId !== convId) return;
      if (st._stopped) return;
      flushThinkingLog();
      toolCalls.forEach(tc => {
        addEventLog('in', `tool_call: ${tc.name}`, JSON.stringify(tc.args).slice(0, 80));
        appendToolCall(tc.name, tc.args);
      });
    },
    onFrontendAction: ({ eventType, eventData, description, requestId }) => {
      if (activeConvId !== convId) return;
      if (st._stopped) return;
      flushThinkingLog();
      addEventLog('in', `frontend_action: ${eventType}`, JSON.stringify(eventData).slice(0, 120));
      executeFrontendAction(eventType, eventData, description, requestId);
    },
    onContent: (content) => {
      if (activeConvId !== convId) return;
      if (st._stopped) return;
      flushThinkingLog();
      appendContent(content);
    },
    onError: (content) => {
      if (activeConvId !== convId) return;
      flushThinkingLog();
      addEventLog('in', `error: ${content}`);
      appendError(content);
      finishMessage();
    },
    onDone: () => {
      st.isSending = false;
      st._stopped = false;
      if (activeConvId === convId) {
        flushThinkingLog();
        addEventLog('in', 'done');
        finishMessage();
      }
      updateInputState();
      loadConvList();
    },
    onChatStopped: (data) => {
      st.isSending = false;
      st._stopped = false;
      if (st._stopTimeoutId) { clearTimeout(st._stopTimeoutId); st._stopTimeoutId = null; }
      if (activeConvId === convId) {
        flushThinkingLog();
        _markBubbleStopped();
        addEventLog('in', 'chat_stopped', `conv=${(data.conversation_id || '').slice(0, 12)}...`);
        finishMessage();
      }
      updateInputState();
      loadConvList();
    },
    onBackgroundDone: (data) => {
      addEventLog('in', 'background_done', `conv=${(data.conversation_id || '').slice(0, 12)}...`);
      loadConvList();
    },
    onMemoryUpdated: (data) => {
      // ★ 后台记忆/摘要完成 → 右下角轻提示 (不阻塞对话)
      const items = data.items || [];
      if (!items.length) return;
      addEventLog('in', 'memory_updated', `${items.length} 条: ${items.map(i => i.key).join(',')}`);
      showMemoryToast(items, data.kind);
    },
  });
}

// 加载历史消息到对话容器
async function loadConvMessages(convId) {
  const st = getConvState(convId);
  try {
    const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
    const host = location.host || 'localhost:8020';
    const res = await fetch(`${protocol}//${host}/api/v1/conversations/${convId}/messages`);
    const data = await res.json();
    rebuildConvUI(convId, data.messages || []);
  } catch (e) {
    st.container.innerHTML = '<div class="conv-empty" style="padding:40px;text-align:center;color:var(--text-muted);">加载失败</div>';
  }
}

function rebuildConvUI(convId, messages) {
  const st = getConvState(convId);
  const container = st.container;
  container.innerHTML = '';
  st.currentAssistantEl = null;
  st.currentContent = '';

  for (const m of messages) {
    const msgDbId = m.id;
    if (m.role === 'human') {
      finishMessageForConv(convId);
      appendUserMessageTo(st, m.content, msgDbId);
    } else if (m.role === 'ai') {
      if (m.tool_calls && m.tool_calls.length) {
        ensureAssistantBubbleFor(st);
        // ★ 思考过程持久化: 带 tool_calls 的 AI 消息, content 就是思考过程, 在这里重建为 thinking 块
        if (m.content && m.content.trim()) {
          appendThinkingTo(st, m.content);
        }
        m.tool_calls.forEach(tc => appendToolCallTo(st, tc.name || tc.get('name', ''), tc.args || tc.get('args', {})));
      }
      if (m.content && m.content.trim()) {
        ensureAssistantBubbleFor(st);
        appendContentTo(st, m.content);
        finishMessageForConv(convId);
      }
    } else if (m.role === 'tool') {
      try {
        const parsed = JSON.parse(m.content);
        if (parsed.type === 'frontend_action' && parsed.instruction) {
          // 前端执行动作 (历史回放时不创建 UI, 由后续消息内容承载)
        }
      } catch (e) {}
    }
  }
  finishMessageForConv(convId);

  // ★ 修复刷新后影像面板全空: 若有持久化的影像清单, 重建到预览面板
  //   rebuildConvUI 本身只渲染 frontend_action 文字卡片, 不重载图片;
  //   这里补一步: 用 localStorage 里的 wmsImages 恢复 (若是当前活跃对话)
  if (st.wmsImages && st.wmsImages.length > 0 && convId === activeConvId) {
    _restoreWmsPanelFromConv(convId);
  }
}

function finishMessageForConv(convId) {
  const st = convStates[convId];
  if (!st) return;
  if (st._stopTimeoutId) { clearTimeout(st._stopTimeoutId); st._stopTimeoutId = null; }
  if (st.currentAssistantEl) {
    // ★ 移除所有 thinking 块的光标 (多轮配对后会有多个 cursor)
    st.currentAssistantEl.querySelectorAll('.thinking-block .cursor').forEach(c => c.remove());
  }
  st.currentAssistantEl = null;
  st.currentContent = '';
  // ★ 重置轮次组状态 (下一条消息从新组开始)
  st.currentIterationGroup = null;
  st.currentGroupHasTool = false;
}


// ==================== 右键菜单操作 ====================

let _contextMenuConvId = null;  // 右键目标对话 ID

function showConvContextMenu(event, convId) {
  event.preventDefault();
  event.stopPropagation();
  _contextMenuConvId = convId;
  const menu = document.getElementById('convContextMenu');
  if (!menu) return;
  // ★ 填充"移动到分组"子菜单 (基于 _groupsCache + 当前会话的 project_id)
  _populateMoveSubmenu(convId);
  // 定位菜单 (确保不超出视口)
  let x = event.clientX;
  let y = event.clientY;
  const mw = 180, mh = 120;
  if (x + mw > window.innerWidth) x = window.innerWidth - mw - 4;
  if (y + mh > window.innerHeight) y = window.innerHeight - mh - 4;
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  menu.style.display = 'block';
  // 高亮目标项
  document.querySelectorAll('.conv-item').forEach(el => el.style.outline = '');
  const target = event.currentTarget;
  if (target) target.style.outline = '1px solid var(--accent)';
}

// ★ 填充"移动到分组"子菜单: 未分组 / 各 project / 新建分组
function _populateMoveSubmenu(convId) {
  const submenu = document.getElementById('convMoveSubmenu');
  if (!submenu) return;
  const curConv = _convListCache.find(c => c.id === convId);
  const curPid = curConv ? curConv.project_id : null;
  const items = [];
  items.push(`<div class="submenu-item${!curPid ? ' current' : ''}" onclick="moveConvToGroup('${convId}', '')">未分类</div>`);
  _groupsCache.forEach(g => {
    const isCur = curPid === g.id ? ' current' : '';
    items.push(`<div class="submenu-item${isCur}" onclick="moveConvToGroup('${convId}', '${g.id}')">${escapeHtml(g.name)}</div>`);
  });
  items.push(`<div class="submenu-item create-new" onclick="createGroupAndMove('${convId}')">新建分组…</div>`);
  submenu.innerHTML = items.join('');
}

// ★ 移动会话到分组 (project_id='' 表示移到未分组)
async function moveConvToGroup(convId, projectId) {
  hideConvContextMenu();
  try {
    const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
    const host = location.host || 'localhost:8020';
    const res = await fetch(`${protocol}//${host}/api/v1/conversations/${convId}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId }),
    });
    const data = await res.json();
    if (data.status === 'success') {
      addEventLog('out', '移动会话', `conv=${convId.slice(0,8)} → ${projectId || '未分组'}`);
      // 同步更新本地缓存, 立即重渲 (不必等防抖)
      const c = _convListCache.find(x => x.id === convId);
      if (c) c.project_id = projectId || null;
      renderConvTree();
    } else {
      alert('移动失败: ' + (data.msg || '未知错误'));
    }
  } catch (e) { alert('移动请求失败: ' + e.message); }
}

// ★ 新建分组并立即把会话移入
async function createGroupAndMove(convId) {
  hideConvContextMenu();
  const name = await showPromptModal('请输入新项目名称：');
  if (!name || !name.trim()) return;
  try {
    const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
    const host = location.host || 'localhost:8020';
    const res = await fetch(`${protocol}//${host}/api/v1/projects?user_id=study_user`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.trim(), description: '' }),
    });
    const data = await res.json();
    if (data.status === 'success' && data.project_id) {
      await moveConvToGroup(convId, data.project_id);
    } else {
      alert('新建分组失败: ' + (data.msg || '未知错误'));
    }
  } catch (e) { alert('新建分组请求失败: ' + e.message); }
}

// ★ 右键"重命名": 弹 prompt 改标题 (走 PATCH /conversations/{id})
async function renameConvFromMenu() {
  const convId = _contextMenuConvId;
  hideConvContextMenu();
  if (!convId) return;
  const cur = _convListCache.find(c => c.id === convId);
  const oldTitle = cur ? (cur.title || '') : '';
  const name = await showPromptModal('请输入新的会话名称：', oldTitle);
  if (name === null) return;  // 取消
  if (!name.trim()) { alert('名称不能为空'); return; }
  try {
    const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
    const host = location.host || 'localhost:8020';
    const res = await fetch(`${protocol}//${host}/api/v1/conversations/${convId}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: name.trim() }),
    });
    const data = await res.json();
    if (data.status === 'success') {
      if (cur) cur.title = name.trim();
      const st = convStates[convId];
      if (st) st.title = name.trim();
      renderConvTree();
      addEventLog('out', '重命名会话', name.trim());
    } else {
      alert('重命名失败: ' + (data.msg || '未知错误'));
    }
  } catch (e) { alert('重命名请求失败: ' + e.message); }
}

function hideConvContextMenu() {
  const menu = document.getElementById('convContextMenu');
  if (menu) menu.style.display = 'none';
  _contextMenuConvId = null;
  document.querySelectorAll('.conv-item').forEach(el => el.style.outline = '');
}

async function deleteConvFromMenu() {
  const convId = _contextMenuConvId;
  hideConvContextMenu();
  if (!convId) return;
  try {
    const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
    const host = location.host || 'localhost:8020';
    const res = await fetch(`${protocol}//${host}/api/v1/conversations/${convId}`, {
      method: 'DELETE',
    });
    const data = await res.json();
    if (data.status === 'success') {
      addEventLog('out', '删除对话', `conv=${convId.slice(0,12)}...`);
      // 如果该对话已打开, 清理其状态
      if (convStates[convId]) {
        wsClient.unregisterConversation(convId);
        if (convStates[convId].container) convStates[convId].container.remove();
        delete convStates[convId];
        if (activeConvId === convId) {
          activeConvId = null;
          wsClient.conversationId = null;
          // ★ 删的是当前会话 → 工作区面板随之清空 (避免残留已删会话的影像)
          const viewport = document.getElementById('wmsViewport');
          if (viewport) { viewport.innerHTML = ''; closeWmsPopup(); showWmsEmpty(); }
        }
      }
      _clearConvWms(convId);  // ★ 同步清理该对话的影像 localStorage
      loadConvList();
    } else {
      alert('删除失败: ' + (data.msg || '未知错误'));
    }
  } catch (e) {
    alert('删除请求失败: ' + e.message);
  }
}

// ★ 分组右键菜单状态 & 函数
let _contextGroupId = null;
let _contextGroupName = '';

function showGroupContextMenu(event, groupId, groupName) {
  event.preventDefault();
  event.stopPropagation();
  _contextGroupId = groupId;
  _contextGroupName = groupName;
  const menu = document.getElementById('groupContextMenu');
  if (!menu) return;
  // 定位菜单 (确保不超出视口)
  let x = event.clientX;
  let y = event.clientY;
  const mw = 160, mh = 80;
  if (x + mw > window.innerWidth) x = window.innerWidth - mw - 4;
  if (y + mh > window.innerHeight) y = window.innerHeight - mh - 4;
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  menu.style.display = 'block';
  // 高亮目标分组头部
  document.querySelectorAll('.conv-group-header').forEach(el => el.style.outline = '');
  const target = event.currentTarget;
  if (target) target.style.outline = '1px solid var(--accent)';
}

function hideGroupContextMenu() {
  const menu = document.getElementById('groupContextMenu');
  if (menu) menu.style.display = 'none';
  _contextGroupId = null;
  _contextGroupName = '';
  document.querySelectorAll('.conv-group-header').forEach(el => el.style.outline = '');
}

function renameGroupFromMenu() {
  const gid = _contextGroupId;
  const gname = _contextGroupName;
  hideGroupContextMenu();
  if (!gid) return;
  renameGroup(gid, gname);
}

async function deleteGroupFromMenu() {
  const gid = _contextGroupId;
  const gname = _contextGroupName;
  hideGroupContextMenu();
  if (!gid) return;
  await deleteGroup(gid, gname);
}

// ★ 统一关闭菜单 helper: 关闭会话菜单 + 分组菜单 + 清除高亮
function _hideAllContextMenus() {
  hideConvContextMenu();
  hideGroupContextMenu();
}

// 点击空白处或按 Esc / scroll 关闭所有菜单
document.addEventListener('click', (e) => {
  const convMenu = document.getElementById('convContextMenu');
  const groupMenu = document.getElementById('groupContextMenu');
  if (convMenu && convMenu.style.display === 'block' && !convMenu.contains(e.target)) {
    hideConvContextMenu();
  }
  if (groupMenu && groupMenu.style.display === 'block' && !groupMenu.contains(e.target)) {
    hideGroupContextMenu();
  }
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') _hideAllContextMenus();
});
document.addEventListener('scroll', _hideAllContextMenus, true);

// ==================== 会话侧栏 ====================

// ★ 将对话直接插入到侧栏 DOM (不通过后端请求)
function addConvToSidebar(convId, title, updatedAt, msgCount) {
  const container = document.getElementById('convList');
  // 移除空状态提示
  const empty = container.querySelector('.conv-empty');
  if (empty) empty.remove();
  
  const time = (updatedAt || '').slice(5, 16).replace('T', ' ');
  const isActive = convId === activeConvId ? ' active' : '';
  const item = document.createElement('div');
  item.className = 'conv-item' + isActive;
  item.dataset.convId = convId;
  item.onclick = () => openConversation(convId);
  item.ondblclick = () => openConversation(convId);
  item.oncontextmenu = (e) => showConvContextMenu(e, convId);
  item.innerHTML = `<div class="conv-item-title">${escapeHtml(title)}</div>
    <div class="conv-item-meta"><span>${time}</span><span class="msg-count">${msgCount}</span></div>`;
  
  // 插入到最顶部
  container.insertBefore(item, container.firstChild);
  return item;
}

// ★ 全局缓存: 分组列表 + 会话列表 (供右键子菜单和搜索复用)
let _groupsCache = [];      // [{id, name, ...}]
let _convListCache = [];    // [{id, project_id, title, ...}]

// ★ loadConvList 防抖: 多个 done/open/background_done 触发时合并刷新
//   loadConvList() 默认带防抖 (300ms); loadConvListImmediate() 立即执行
let _loadConvListTimer = null;
function loadConvList() {
  if (_loadConvListTimer) clearTimeout(_loadConvListTimer);
  _loadConvListTimer = setTimeout(loadConvListImmediate, 300);
}
async function loadConvListImmediate() {
  if (_loadConvListTimer) { clearTimeout(_loadConvListTimer); _loadConvListTimer = null; }
  try {
    const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
    const host = location.host || 'localhost:8020';
    // ★ v2.4: 按 activeProjectId 过滤会话, 同时拉分组列表供右键菜单
    const convUrl = activeProjectId
      ? `${protocol}//${host}/api/v1/conversations?user_id=study_user&project_id=${activeProjectId}`
      : `${protocol}//${host}/api/v1/conversations?user_id=study_user`;
    const [convRes, groupRes] = await Promise.all([
      fetch(convUrl).then(r => r.json()),
      fetch(`${protocol}//${host}/api/v1/projects?user_id=study_user`).then(r => r.json()),
    ]);
    _convListCache = convRes.conversations || [];
    _groupsCache = groupRes.projects || [];
    renderConvTree();
  } catch (e) {
    document.getElementById('convList').innerHTML = '<div class="conv-empty">加载失败</div>';
  }
}

// ★ 渲染分组折叠树
//   结构: 未分组 (project_id=null 的会话) + 各 project 分组 (含其会话)
//   折叠状态存 localStorage nrms_group_collapsed (Set of project_id 或 'ungrouped')
function _loadGroupCollapsed() {
  try { return new Set(JSON.parse(localStorage.getItem('nrms_group_collapsed') || '[]')); }
  catch (e) { return new Set(); }
}
function _saveGroupCollapsed(set) {
  try { localStorage.setItem('nrms_group_collapsed', JSON.stringify([...set])); } catch (e) {}
}

function renderConvTree() {
  const container = document.getElementById('convList');
  const search = (document.getElementById('convSearchInput') || {}).value || '';
  const convs = _convListCache.filter(c => {
    if (!search) return true;
    const t = (c.title || '').toLowerCase();
    return t.indexOf(search.toLowerCase()) >= 0 || c.id.indexOf(search) >= 0;
  });

  if (!convs.length) {
    container.innerHTML = '<div class="conv-empty">' + (search ? '无匹配会话' : (activeProjectId ? '暂无会话' : '请选择一个项目文件夹')) + '</div>';
    return;
  }

  container.innerHTML = convs.map(c => _renderConvItemHtml(c)).join('');
}

// ★ 折叠保留 (兼容简化格式, 不再按分组折叠)
function toggleGroup(groupId) { /* 简化列表无折叠, 保留占位 */ }
function filterConvList() { renderConvTree(); }

// 单个会话项 HTML (从原 loadConvList 抽出, 供树渲染复用)
function _renderConvItemHtml(c) {
  const title = escapeHtml(c.title || '(无标题)');
  const time = (c.updated_at || '').slice(5, 16).replace('T', ' ');
  const isActive = c.id === activeConvId ? ' active' : '';
  const status = c.status || '';
  let statusBadge = '';
  if (status === 'background') statusBadge = '<span class="conv-status background">进行中</span>';
  else if (status === 'stopped') statusBadge = '<span class="conv-status stopped">已停止</span>';
  else if (status === 'completed' && c.message_count > 0) statusBadge = '<span class="conv-status completed">已完成</span>';
  return `<div class="conv-item${isActive}" onclick="openConversation('${c.id}')" ondblclick="openConversation('${c.id}')" oncontextmenu="showConvContextMenu(event, '${c.id}')" data-conv-id="${c.id}">
    <div class="conv-item-title">${title}${statusBadge}</div>
    <div class="conv-item-meta"><span>${time}</span><span class="msg-count">${c.message_count}</span></div>
  </div>`;
}

// 折叠/展开分组
function toggleGroup(groupId) {
  const set = _loadGroupCollapsed();
  if (set.has(groupId)) set.delete(groupId); else set.add(groupId);
  _saveGroupCollapsed(set);
  renderConvTree();
}

// 搜索框实时过滤
function filterConvList() { renderConvTree(); }

// ★ 侧栏"新建分组"按钮
async function createGroupFromSidebar() {
  const name = await showPromptModal('请输入新项目名称：');
  if (!name || !name.trim()) return;
  try {
    const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
    const host = location.host || 'localhost:8020';
    const res = await fetch(`${protocol}//${host}/api/v1/projects?user_id=study_user`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.trim(), description: '' }),
    });
    const data = await res.json();
    if (data.status === 'success') {
      addEventLog('out', '新建分组', name.trim());
      loadConvListImmediate();  // ★ 立即刷新 (不走防抖), 否则用户感觉点击没反应
    } else {
      alert('新建分组失败: ' + (data.msg || '未知错误'));
    }
  } catch (e) { alert('新建分组请求失败: ' + e.message); }
}

// ★ 重命名分组 (走 PATCH /projects/{id}, 复用 showPromptModal 避免原生 prompt)
async function renameGroup(groupId, oldName) {
  if (groupId === 'ungrouped') { alert('"未分组"是系统默认分类，不可重命名'); return; }
  const name = await showPromptModal('请输入新的分组名称：', oldName);
  if (name === null) return;  // 取消
  if (!name.trim()) { alert('名称不能为空'); return; }
  if (name.trim() === oldName) return;  // 未改动
  try {
    const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
    const host = location.host || 'localhost:8020';
    const res = await fetch(`${protocol}//${host}/api/v1/projects/${groupId}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.trim() }),
    });
    const data = await res.json();
    if (data.status === 'success') {
      // ★ 同步更新本地缓存并立即重渲 (不必等防抖刷新)
      const g = _groupsCache.find(x => x.id === groupId);
      if (g) g.name = name.trim();
      renderConvTree();
      addEventLog('out', '重命名分组', name.trim());
    } else {
      alert('重命名分组失败: ' + (data.msg || '未知错误'));
    }
  } catch (e) { alert('重命名分组请求失败: ' + e.message); }
}

// ★ 删除项目 (会话的 project_id 会被后端 SET NULL, 自动回未分组)
async function deleteGroup(groupId, groupName) {
  if (groupId === 'ungrouped') { alert('"未分组"是系统默认分类，不可删除'); return; }
  if (!confirm(`确定删除项目「${groupName}」吗？\n分组内的会话不会被删除，会移回"未分组"。`)) return;
  try {
    const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
    const host = location.host || 'localhost:8020';
    const res = await fetch(`${protocol}//${host}/api/v1/projects/${groupId}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.status === 'success') {
      addEventLog('out', '删除项目', groupName);
      loadConvListImmediate();  // ★ 立即刷新
    } else {
      alert('删除项目失败: ' + (data.msg || '未知错误'));
    }
  } catch (e) { alert('删除项目请求失败: ' + e.message); }
}

async function openConversation(convId) {
  // ★ 不再阻塞 — 直接打开/切换到该对话的 Tab
  try {
    const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
    const host = location.host || 'localhost:8020';
    const res = await fetch(`${protocol}//${host}/api/v1/conversations/${convId}/messages`);
    const data = await res.json();
    const title = (data.messages && data.messages.length > 0 && data.messages[0].content)
      ? data.messages[0].content.slice(0, 20) : '(无标题)';

    activateConversation(convId, title);
    // ★ 高亮侧栏对应项
    document.querySelectorAll('.conv-item').forEach(el => el.classList.remove('active'));
    const sideItem = document.querySelector(`.conv-item[data-conv-id="${convId}"]`);
    if (sideItem) sideItem.classList.add('active');
    loadConvList();  // ★ 刷新侧栏确保列表最新
    addEventLog('in', `打开会话 ${convId.slice(0, 8)}...`, `${(data.messages || []).length} 条消息`);
  } catch (e) {
    alert('加载会话失败: ' + e.message);
  }
}

// ==================== 初始化 ====================

function initWsClient() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = location.host || 'localhost:8020';
  const wsUrl = `${protocol}//${host}/api/v1/agent/ws/chat`;

  wsClient = new WsChatClient({
    onThinking: (content) => { /* 由 conversationCallbacks 处理 */ },
    onToolCall: (toolCalls) => { /* 由 conversationCallbacks 处理 */ },
    onFrontendAction: (data) => { /* 由 conversationCallbacks 处理 */ },
    onContent: (content) => { /* 由 conversationCallbacks 处理 */ },
    onError: (content) => { /* 由 conversationCallbacks 处理 */ },
    onDone: () => { /* 由 conversationCallbacks 处理 */ },
    onStatusChange: (status) => { updateStatus(status); },
    onSessionStart: (id) => {
      // ★ 后端确认会话 ID (前端已提前创建, 此处做同步确认)
      if (activeConvId && activeConvId !== id) {
        // 如果后端返回的 ID 与前端不同, 更新为后端 ID
        const oldSt = convStates[activeConvId];
        if (oldSt) {
          convStates[id] = oldSt;
          oldSt.container.dataset.convId = id;
          delete convStates[activeConvId];
          // 更新侧栏 DOM 中的 data-conv-id
          const sideItem = document.querySelector(`.conv-item[data-conv-id="${activeConvId}"]`);
          if (sideItem) sideItem.dataset.convId = id;
        }
        activeConvId = id;
        wsClient.conversationId = id;
      }
      addEventLog('in', 'session_started', id);
      loadConvList();  // ★ 刷新侧栏以后端数据为准
    },
    onChatStopped: (data) => { /* 由 conversationCallbacks 处理 */ },
    onBackgroundDone: (data) => {
      // ★ 后台对话有消息到达时, 更新 Tab 通知
      const convId = data.conversation_id;
      if (convId && data.message_type !== 'pending_message') {
                loadConvList();
      }
      if (convId && data.message_type === 'pending_message') {
        // 后台对话消息被缓冲, 提示用户
        addEventLog('in', `后台消息 (${convId.slice(0, 8)}...)`, data.message_type);
      }
    },
    onActiveTasksList: (convs) => {
      addEventLog('in', 'active_tasks', convs.join(', ') || '(空)');
    },
  });

  wsClient.connect(wsUrl);
}

// ==================== 发送消息 ====================

function sendMessage() {
  const input = document.getElementById('inputBox');
  const prompt = input.value.trim();
  if (!prompt) return;

  if (!activeConvId) {
    // 没有活跃对话, 先创建再发送
    newConversation();
    doSend(prompt);
    input.value = '';
    return;
  }

  const st = cs();
  if (st && st.isSending) return;
  doSend(prompt);
  input.value = '';
}

function sendDemo(prompt) {
  if (!activeConvId) {
    newConversation();
    doSend(prompt);
    return;
  }
  const st = cs();
  if (st && st.isSending) return;
  document.getElementById('inputBox').value = '';
  doSend(prompt);
}

// ==================== SamSeg 遥感分析: 上传图片 → 自动发分割/变化检测指令 ====================
// 流程: <input type=file> 选图 → POST /api/v1/samseg/upload (multipart) → 拿到路径 → 发对话指令
// 把后端返回的上传绝对路径转成前端可访问的 URL (走 /api/v1/upload 端点)
// ★ v2.3: 后端返回形如 "E:/.../agent-files/samseg/send/{conv_id}/xxx.png", 取 samseg/send/ 之后的部分拼 URL
function _uploadPathToUrl(absPath) {
  if (!absPath) return null;
  const norm = absPath.replace(/\\/g, '/');
  const idx = norm.toLowerCase().indexOf('samseg/send/');
  if (idx < 0) return null;
  const rel = norm.slice(idx + 'samseg/send/'.length);
  return '/api/v1/upload/' + rel;
}

// ★ 输入区上传按钮: 上传图片后把路径填入输入框, 由用户自己写指令再发送
// 与 demo-bar 的 uploadAndAnalyze 区别: 不自动构造指令发送, 让用户自主编辑
async function uploadToInput() {
  const st = cs();
  if (st && st.isSending) { alert('当前对话正在处理中，请稍候'); return; }

  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'image/*';
  input.multiple = true;  // 允许多选 (变化检测需 2 张, 用户可自由选)
  input.onchange = async () => {
    const files = Array.from(input.files);
    if (files.length === 0) return;

    // ★ 修复: 先确保有活跃对话 (否则 cs() 为 null, showImageInPanel 里 wmsImages.push 被守卫跳过 → 切对话丢输入图)
    if (!activeConvId) newConversation();

    try {
      const fd = new FormData();
      files.forEach(f => fd.append('images', f));
      // ★ 带上会话 id, 后端按 {conv_id}_{原名} 命名, 让文件与"会话"关联
      fd.append('conversation_id', activeConvId || '');
      const resp = await fetch('/api/v1/samseg/upload', { method: 'POST', body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `上传失败 (HTTP ${resp.status})`);
      }
      const data = await resp.json();
      const paths = data.paths || [];

      // 立即把原图加载到右侧预览窗口 (此时 cs() 一定非 null)
      paths.forEach(function(p, i) {
        const url = _uploadPathToUrl(p);
        if (!url) return;
        const cap = files.length > 1
          ? '输入影像 ' + (i === 0 ? 'T1' : 'T2') + ': ' + (files[i] ? files[i].name : '')
          : '输入影像: ' + (files[0] ? files[0].name : '');
        renderImage(null, { image_url: url, caption: cap });
      });

      // ★ 不自动发送: 把路径填到输入框, 让用户自己写指令 (如"分割这张图"/"检测…的变化")
      const inputBox = document.getElementById('inputBox');
      if (paths.length === 1) {
        inputBox.value = `图片路径: ${paths[0]} 。`;
      } else {
        // 多张图按 T1/T2 标注, 便于变化检测
        const labels = paths.map((p, i) => `T${i + 1}=${p}`).join('，');
        inputBox.value = `图片路径 (${labels}) 。`;
      }
      inputBox.focus();
      // 光标移到末尾, 方便用户继续输入指令
      inputBox.setSelectionRange(inputBox.value.length, inputBox.value.length);
    } catch (e) {
      alert('图片上传失败: ' + e.message);
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

      // ★ 上传成功后立即把原图加载到右侧预览窗口 (不等分割完成)
      // 此时 activeConvId 一定非 null, 输入图会被记录到对话 state.wmsImages
      paths.forEach(function(p, i) {
        const url = _uploadPathToUrl(p);
        if (!url) return;
        const cap = isChange
          ? '输入影像 ' + (i === 0 ? 'T1' : 'T2') + ': ' + (files[i] ? files[i].name : '')
          : '输入影像: ' + (files[0] ? files[0].name : '');
        renderImage(null, { image_url: url, caption: cap });
      });

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
  addEventLog('out', 'stop_chat', '✓ 已发送停止指令');
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
  // ★ 立即生成 UUID, 创建状态, 添加到侧栏
  const newId = crypto.randomUUID ? crypto.randomUUID() : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => { const r = Math.random()*16|0; return (c==='x'?r:(r&0x3|0x8)).toString(16); });
  
  // 隐藏之前活跃对话的容器
  document.querySelectorAll('.messages-conv').forEach(el => el.style.display = 'none');
  
  // 创建新对话状态并切换
  getConvState(newId).title = '(新对话)';
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
  
  // 立即添加到侧栏 DOM (不等后端)
  addConvToSidebar(newId, '(新对话)', new Date().toISOString(), 0);
  
  updateInputState();
  addEventLog('out', '新对话', newId.slice(0, 8) + '...');
  scrollBottom();
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

  // ★ 携带当前会话所属分组 project_id: 沙盒工作区/记忆等按 (分组, 会话) 隔离依赖它。
  //   新建会话尚未落库 → _convListCache 无记录 → 使用 activeProjectId (工作区激活项目)。
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

// ==================== Thinking 日志聚合 ====================

function accumulateThinkingLog(content) {
  const st = cs();
  if (!st) return;
  st.thinkingBuffer += content;
  st.thinkingTokenCount++;
  if (st.thinkingTimer) clearTimeout(st.thinkingTimer);
  st.thinkingTimer = setTimeout(() => flushThinkingLog(), 300);
}

function flushThinkingLog() {
  const st = cs();
  if (!st) return;
  if (st.thinkingTimer) { clearTimeout(st.thinkingTimer); st.thinkingTimer = null; }
  if (!st.thinkingBuffer) return;
  const preview = st.thinkingBuffer.length > 80 ? st.thinkingBuffer.slice(0, 80) + '...' : st.thinkingBuffer;
  addEventLog('in', `thinking (${st.thinkingTokenCount} tokens)`, preview);
  st.thinkingBuffer = '';
  st.thinkingTokenCount = 0;
}

// ==================== 前端实际执行 frontend_action ====================

function executeFrontendAction(eventType, eventData, description, requestId) {
  // ★ 前端操作执行策略:
  //   - render_image (SamSeg 分割/变化检测结果): 必须实际渲染到工作区面板 (showImageInPanel)
  //   - 其余视觉渲染类 (表格/图表/面板): 静默, 由 AI 文字回复承载
  //   - 地图控制类 / 下载: 实际执行 (控制地图、触发下载)
  const st = cs();
  if (!st) return;

  // 纯视觉渲染类 (不含 render_image) → 不渲染, 直接返回成功 (AI 会在消息中描述结果)
  //   ★ render_image 不能放这里: SamSeg 结果靠它进入工作区面板, 跳过会导致结果永远不显示
  const skipVisual = ['render_table', 'render_chart', 'ui_panel_control'].includes(eventType);
  if (skipVisual) {
    if (requestId) {
      wsClient.sendToolResult({
        request_id: requestId,
        status: 'success',
        data: eventData,
      });
      addEventLog('out', 'tool_result: success (skip visual)', eventType);
    }
    return;
  }

  function finishAction(resultPayload) {
    var ok = resultPayload && (resultPayload.status === 'success' || resultPayload.loaded || resultPayload.visible);
    if (requestId) {
      wsClient.sendToolResult({
        request_id: requestId,
        status: ok ? 'success' : 'error',
        data: resultPayload,
      });
      var resultJson = JSON.stringify(resultPayload, null, 2);
      addEventLog('out', 'tool_result: ' + (ok ? 'success' : 'error'), resultJson.slice(0, 200));
      addResultCard(eventType, resultPayload);
    }
  }

  setTimeout(function() {
    var resultOrPromise;
    switch (eventType) {
      case 'render_image': resultOrPromise = renderImage(null, eventData); break;
      case 'layer_control':
      case 'layer_group_control': resultOrPromise = renderLayerControl(null, eventData); break;
      case 'layer_display':
      case 'layer_locate': resultOrPromise = renderLayerDisplay(null, eventData); break;
      case 'download': resultOrPromise = renderDownload(null, eventData); break;
      default:
        resultOrPromise = { action: eventType, status: 'rendered', data: eventData };
    }

    if (resultOrPromise && typeof resultOrPromise.then === 'function') {
      resultOrPromise.then(function(payload) {
        finishAction(payload);
      }).catch(function(err) {
        finishAction({ status: 'error', error: err.message || String(err) });
      });
    } else {
      finishAction(resultOrPromise || {});
    }
  }, 300);
}

// ==================== Markdown 解析 + UI组件 + 代码高亮 ====================

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

// ==================== 渲染器 (使用活跃对话的状态) ====================

// ★ canvas 绘图从 CSS 变量读取色值, 保证主题切换一致 (避免硬编码深色)
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
  // ★ 浅色友好的饱和度
  const colors = ['#4d6bfe', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899'];
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
    // ★ 浅色: 表头浅灰底, 行交替白/浅灰
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
        // ★ 状态列着色: 异常红/警告黄/正常深灰
        ctx.fillStyle = c===3&&rowData[c]==='异常' ? _cssVar('--accent-red','#ef4444')
                      : c===3&&rowData[c]==='警告' ? _cssVar('--accent-amber','#f59e0b')
                      : _cssVar('--text-primary', '#1a1a1a');
        ctx.fillText(rowData[c], pad+c*cellW+cellW/2, pad+(r+1.5)*cellH);
      }
    }
  });
  return { panel_name, action, status: 'success', rows, cols };
}

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

function renderLayerDisplay(container, data) { /* ... 不变 ... */
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

function renderDownload(container, data) { /* ... 不变 ... */
  const { layer_name, file_type, download_url, file_size } = data;
  const el = document.createElement('div');
  if (download_url) {
    var sizeText = file_size ? ' (' + Math.round(file_size / 1024) + 'KB)' : '';
    el.innerHTML = `<div style="color:var(--accent-green);">下载完成</div><div style="margin-top:4px;color:var(--text-primary);font-size:var(--fs-md);">文件: ${escapeHtml(layer_name)}.${file_type||'tif'}${sizeText}</div>`;
    container.appendChild(el);
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
  container.appendChild(el);
  return { layer_name, file_type, status: 'downloading' };
}

function renderTable(container, data) { /* ... 不变 ... */
  const { table_name, headers, rows } = data;
  if (!rows || rows.length === 0) { const empty = buildEmptyRichTable(table_name); container.appendChild(empty); return { table_name, rows: 0, status: 'empty' }; }
  const richTable = buildRichTable(table_name, headers, rows); container.appendChild(richTable);
  return { table_name, rows: rows.length, cols: headers.length, status: 'success' };
}

// ★ SamSeg 分割/变化检测结果图展示 (后端返回本地 PNG URL, 非 WMS)
// 输入原图由 uploadAndAnalyze 在上传成功后立即显示到预览面板, 这里只负责加载结果图。
// (历史回放 rebuildConvUI 不会调本函数 —— 它只显示 frontend_action 静态卡片, 不重载图片)
function renderImage(container, data) {
  const { image_url, caption, legend } = data;
  // ★ container 可能为 null (由 executeFrontendAction 调用, 结果图直接进工作区面板,
  //   不在气泡内嵌卡片); 只在显式传入容器时才追加内联提示
  if (container) {
    const el = document.createElement('div');
    el.innerHTML = `<div style="color:var(--accent-green);">分析结果已生成</div>`
      + `<div style="margin-top:4px;font-size:var(--fs-md);color:var(--text-secondary);">${escapeHtml(caption || '遥感分析结果')}</div>`;
    container.appendChild(el);
  }

  // ★ 只加载结果图 + 图注 (legend 在 showImageInPanel 内构建 DOM 时嵌入, 无时序问题)
  return showImageInPanel(image_url, caption || '遥感分析结果', legend).then(function(result) {
    return {
      image_url: image_url,
      status: result.loaded ? 'success' : 'error',
      loaded: result.loaded,
      error: result.error || null,
    };
  });
}

// ==================== WMS 遥感影像预览面板 ====================
// 通过 GeoServer WMS GetMap 请求获取真实遥感影像, 显示在右侧面板。
// 不引入任何地图库 —— WMS GetMap 返回的就是 PNG, 用 <img> 直连即可。

// 构造 GeoServer WMS GetMap URL
function buildWmsUrl(layerName, workspace, bbox, wmsUrl) {
  const base = wmsUrl || 'http://localhost:8080/geoserver/wms';
  const ws = workspace || '';
  // workspace 为空时不拼前缀, 让 GeoServer 在默认工作空间解析图层
  const layersParam = ws ? (ws + ':' + layerName) : layerName;
  const params = new URLSearchParams({
    service: 'WMS', version: '1.1.1', request: 'GetMap',
    layers: layersParam,
    styles: '',
    format: 'image/png', transparent: 'false',
    width: '512', height: '512',
    srs: 'EPSG:4326',
    // bbox 顺序 (WMS 1.1.1, EPSG:4326): minLng,minLat,maxLng,maxLat
    bbox: (bbox && bbox.length === 4) ? bbox.join(',') : '-180,-90,180,90',
  });
  return base + '?' + params.toString();
}

// 把 WMS 影像加载到右侧面板 (发射即忘, <img> 异步加载, 不阻塞 resultPayload 回传)
// record (可选, 默认 true): 是否记录到当前对话 state.wmsImages (用于对话切换恢复)。
function showWmsInPanel(layerName, workspace, bbox, wmsUrl, caption, record) {
  const viewport = document.getElementById('wmsViewport');
  if (!viewport) return Promise.resolve({ loaded: false, error: 'viewport missing' });

  // ★ 首次加载影像时隐藏空提示、显示缩放控件
  onWmsImageLoaded();

  const item = document.createElement('div');
  item.className = 'wms-item';
  const cap = caption || layerName;
  const wsLabel = workspace ? ' · ' + workspace : '';
  const w = 512, h = 512;
  const bboxStr = (bbox && bbox.length === 4) ? bbox.join(',') : '-180,-90,180,90';
  item.dataset.layerName = layerName;
  item.dataset.workspace = workspace || '';
  item.dataset.bbox = bboxStr;
  item.dataset.width = w;
  item.dataset.height = h;
  item.dataset.wmsUrl = wmsUrl || 'http://localhost:8080/geoserver/wms';
  item.innerHTML =
    '<img alt="' + escapeHtml(layerName) + '" title="点击查看该位置属性数据" />' +
    '<div class="wms-caption">' +
      '<span>' + escapeHtml(cap) + escapeHtml(wsLabel) + '</span>' +
      '<span class="wms-remove" title="移除该影像">✕</span>' +
    '</div>';
  viewport.appendChild(item);

  const img = item.querySelector('img');
  const removeBtn = item.querySelector('.wms-remove');
  // ★ 记录到当前对话 state (供切换对话时恢复)。用唯一 key 标记, 便于删除时定位
  const imageKey = 'wms_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
  item.dataset.imageKey = imageKey;
  if (record !== false) {
    const cur = cs();
    if (cur) {
      cur.wmsImages.push({
        type: 'wms', key: imageKey,
        layerName: layerName, workspace: workspace || '',
        bbox: bbox, wmsUrl: wmsUrl, caption: cap,
      });
      _persistConvWms(activeConvId);  // ★ 同步持久化到 localStorage
    }
  }
  removeBtn.onclick = function() {
    item.remove(); closeWmsPopup();
    // ★ 同步从当前对话 state 删除
    const cur = cs();
    if (cur && cur.wmsImages) {
      cur.wmsImages = cur.wmsImages.filter(function(x) { return x.key !== imageKey; });
      _persistConvWms(activeConvId);
    }
    if (!viewport.children.length) showWmsEmpty();
  };

  const ws = workspace || '';
  const url = buildWmsUrl(layerName, workspace, bbox, wmsUrl);

  // ★ 返回 Promise, 在图片加载完成/失败时 resolve 真实状态
  return new Promise(function(resolve) {
    var settled = false;
    var timeout = setTimeout(function() {
      if (settled) return;
      settled = true;
      resolve({ loaded: false, error: '图片加载超时 (10s)', layer_name: layerName, workspace: ws });
    }, 10000);

    img.onerror = function() {
      if (settled) return; settled = true; clearTimeout(timeout);
      img.style.display = 'none';
      var err = document.createElement('div');
      err.className = 'wms-error';
      err.textContent = '影像加载失败：' + layerName + '（请检查 GeoServer 是否启动、图层 ' + ws + ':' + layerName + ' 是否存在）';
      item.insertBefore(err, item.querySelector('.wms-caption'));
      resolve({ loaded: false, error: 'WMS 加载失败', layer_name: layerName, workspace: ws });
    };

    img.onload = function() {
      if (settled) return; settled = true; clearTimeout(timeout);
      img.classList.add('clickable');
      fitWmsToWindow();
      resolve({ loaded: true, layer_name: layerName, workspace: ws });
    };

    img.onclick = function(e) {
      e.stopPropagation();
      handleWmsImageClick(e, img, item);
    };

    img.src = url;
  });
}

// ★ 展示本地生成的图片 (SamSeg 分割/变化检测结果 PNG) 到右侧影像面板
// 与 showWmsInPanel 的区别: 不走 WMS URL 拼接 (buildWmsUrl), 不支持 GetFeatureInfo 点击查询,
// 直接 img.src = 本地 URL。视觉样式 (wms-item/caption/缩放/移除) 完全复用, 保持一致体验。
// legend (可选): [{name, hex}, ...] 颜色→类别图注, 在构建 DOM 时直接嵌入图片下方,
//                避免依赖 :last-child 等位置查询导致的时序竞态问题。
// record (可选, 默认 true): 是否记录到当前对话 state.wmsImages (用于对话切换恢复)。
//                切换对话时内部重建调用传 false, 避免重复记录。
function showImageInPanel(url, caption, legend, record) {
  const viewport = document.getElementById('wmsViewport');
  if (!viewport) return Promise.resolve({ loaded: false, error: 'viewport missing' });

  onWmsImageLoaded();

  const item = document.createElement('div');
  item.className = 'wms-item';
  const cap = caption || '分析结果';
  item.innerHTML =
    '<img alt="' + escapeHtml(cap) + '" />' +
    '<div class="wms-caption">' +
      '<span>' + escapeHtml(cap) + '</span>' +
      '<span class="wms-remove" title="移除该图片">✕</span>' +
    '</div>';

  // ★ 颜色→类别图注: 构建时直接嵌入 (在 caption 之前, 即图片下方), 与 item 原子绑定
  if (legend && legend.length) {
    const wrap = document.createElement('div');
    wrap.className = 'seg-legend';
    wrap.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px 12px;padding:8px 10px;'
      + 'font-size:var(--fs-sm);font-family:var(--mono);color:var(--text-secondary);'
      + 'border-top:1px solid var(--border);background:var(--bg-inset);';
    legend.forEach(function(it) {
      const chip = document.createElement('span');
      chip.style.cssText = 'display:inline-flex;align-items:center;gap:4px;';
      const sw = document.createElement('span');
      sw.style.cssText = 'display:inline-block;width:10px;height:10px;border-radius:2px;'
        + 'background:' + it.hex + ';border:1px solid rgba(255,255,255,0.2);';
      chip.appendChild(sw);
      chip.appendChild(document.createTextNode(it.name || ''));
      wrap.appendChild(chip);
    });
    item.appendChild(wrap);
  }

  // ★ 确保 caption 始终在最后 (legend 在 img 和 caption 之间)
  const capEl = item.querySelector('.wms-caption');
  if (capEl) item.appendChild(capEl);

  viewport.appendChild(item);

  const img = item.querySelector('img');
  const removeBtn = item.querySelector('.wms-remove');
  // ★ 记录到当前对话 state (供切换对话时恢复)。用唯一 key 标记, 便于删除时定位
  const imageKey = 'img_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
  item.dataset.imageKey = imageKey;
  if (record !== false) {
    const cur = cs();
    if (cur) {
      cur.wmsImages.push({ type: 'image', key: imageKey, url: url, caption: cap, legend: legend });
      _persistConvWms(activeConvId);  // ★ 同步持久化到 localStorage
    }
  }
  removeBtn.onclick = function() {
    item.remove(); closeWmsPopup();
    // ★ 同步从当前对话 state 删除
    const cur = cs();
    if (cur && cur.wmsImages) {
      cur.wmsImages = cur.wmsImages.filter(function(x) { return x.key !== imageKey; });
      _persistConvWms(activeConvId);
    }
    if (!viewport.children.length) showWmsEmpty();
  };

  return new Promise(function(resolve) {
    var settled = false;
    var timeout = setTimeout(function() {
      if (settled) return;
      settled = true;
      resolve({ loaded: false, error: '图片加载超时 (10s)' });
    }, 10000);

    // ★ 兜底: 若预览 URL 仍加载失败 (后端 / 网络问题), 试不带参数的原 URL
    var retriedWithPreview = false;

    img.onerror = function() {
      if (settled) return;
      if (!retriedWithPreview) {
        retriedWithPreview = true;
        img.src = url;  // 回退到原 URL (可能浏览器原生支持, 如 PNG)
        return;
      }
      settled = true; clearTimeout(timeout);
      img.style.display = 'none';
      var err = document.createElement('div');
      err.className = 'wms-error';
      err.textContent = '图片加载失败：' + cap + '（URL: ' + url + '）';
      item.insertBefore(err, item.querySelector('.wms-caption'));
      resolve({ loaded: false, error: '图片加载失败' });
    };

    img.onload = function() {
      if (settled) return; settled = true; clearTimeout(timeout);
      // ★ 记录图片真实尺寸 (用于缩放变换计算), 不强制 style.width 让 CSS 控制布局
      var nw = img.naturalWidth, nh = img.naturalHeight;
      item.dataset.width = nw;
      item.dataset.height = nh;
      // ★ 延迟一帧确保浏览器布局完成后再 fit
      requestAnimationFrame(function() {
        fitWmsToWindow();
      });
      resolve({ loaded: true });
    };

    // ★ 统一预处理: 所有图片 URL 主动加 preview=1 (PNG/JPG 零开销直读, TIF 转 PNG)
    //    避免依赖 onerror 重试, 浏览器一次就能正常显示
    img.src = _appendPreviewParam(url);
  });
}

// ★ 给图片 URL 追加 preview=1 参数 (触发后端 TIFF→PNG 转码)
//   已带 query → 追加 &preview=1; 无 query → 加 ?preview=1
//   已含 preview=1 → 原样返回 (避免重复)
function _appendPreviewParam(url) {
  if (!url) return url;
  if (/[?&]preview=/.test(url)) return url;
  return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'preview=1';
}

// ★ 处理 WMS 影像点击 → GetFeatureInfo 查询
async function handleWmsImageClick(event, img, item) {
  const rect = img.getBoundingClientRect();
  const offsetX = event.clientX - rect.left;
  const offsetY = event.clientY - rect.top;

  // 将渲染坐标映射到 WMS 请求坐标 (512x512)
  const wmsW = parseInt(item.dataset.width) || 512;
  const wmsH = parseInt(item.dataset.height) || 512;
  const i = Math.round((offsetX / rect.width) * wmsW);
  const j = Math.round((offsetY / rect.height) * wmsH);

  // 先关闭旧弹出框
  closeWmsPopup();

  // 创建弹出框
  const popup = document.createElement('div');
  popup.className = 'wms-popup';
  popup.innerHTML =
    '<div class="popup-header">' +
      '<span class="popup-title">属性查看</span>' +
      '<button class="popup-close" type="button">' +
        '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
          '<path d="M18 6L6 18M6 6l12 12" />' +
        '</svg>' +
      '</button>' +
    '</div>' +
    '<div class="popup-coords">图层: ' + escapeHtml(item.dataset.layerName) +
      ' | 像素: (' + i + ', ' + j + ')</div>' +
    '<div class="popup-loading">查询中...</div>';
  document.body.appendChild(popup);

  // 定位弹出框（优先在点击位置右侧）
  positionPopup(popup, event.clientX, event.clientY);

  // 关闭按钮
  popup.querySelector('.popup-close').onclick = function(e) {
    e.stopPropagation(); closeWmsPopup();
  };

  // 创建遮罩（点击空白关闭）
  const mask = document.createElement('div');
  mask.className = 'wms-popup-mask';
  mask.onclick = function() { closeWmsPopup(); };
  document.body.appendChild(mask);
  popup._mask = mask;

  // 调用后端 GetFeatureInfo 代理
  try {
    const params = new URLSearchParams({
      layer_name: item.dataset.layerName,
      workspace: item.dataset.workspace,
      bbox: item.dataset.bbox,
      width: item.dataset.width,
      height: item.dataset.height,
      i: i,
      j: j,
    });
    const resp = await fetch('/api/v1/geoserver/feature-info?' + params.toString());
    const data = await resp.json();

    const bodyEl = popup.querySelector('.popup-loading') || popup.querySelector('.popup-error');
    if (!bodyEl) return;

    if (data.success && data.values && Object.keys(data.values).length > 0) {
      // 构建属性表
      let tableRows = '';
      for (const [key, val] of Object.entries(data.values)) {
        let displayVal = val;
        if (typeof val === 'number') displayVal = Number.isInteger(val) ? val : val.toFixed(6);
        tableRows += '<tr><td>' + escapeHtml(key) + '</td><td>' + escapeHtml(String(displayVal)) + '</td></tr>';
      }
      const lonLat = (data.lon !== undefined && data.lat !== undefined)
        ? ' | 坐标: ' + data.lon + ', ' + data.lat : '';
      popup.querySelector('.popup-coords').textContent =
        '图层: ' + escapeHtml(item.dataset.layerName) +
        ' | 像素: (' + i + ', ' + j + ')' + lonLat;
      bodyEl.outerHTML = '<table class="popup-table">' + tableRows + '</table>';
    } else if (data.success) {
      // 响应成功但无结构化数据
      bodyEl.outerHTML = '<div class="popup-coords" style="margin-top:4px">该位置无可读属性数据</div>'
        + (data.raw_response ? '<div style="font-size:var(--fs-xs);color:var(--text-muted);margin-top:4px;word-break:break-all;max-height:120px;overflow-y:auto">原始响应: ' + escapeHtml(data.raw_response.slice(0, 300)) + '</div>' : '');
    } else {
      bodyEl.className = 'popup-error';
      bodyEl.textContent = '查询失败: ' + (data.msg || '未知错误');
    }

    // 内容更新后重新定位
    positionPopup(popup, event.clientX, event.clientY);

  } catch (err) {
    const bodyEl = popup.querySelector('.popup-loading');
    if (bodyEl) { bodyEl.className = 'popup-error'; bodyEl.textContent = '网络请求失败: ' + err.message; }
  }
}

// 定位弹出框（避免溢出屏幕）
function positionPopup(popup, clientX, clientY) {
  const gap = 12;
  let left = clientX + gap;
  let top = clientY - 20;

  // 防止右侧溢出
  if (left + popup.offsetWidth > window.innerWidth - 10) {
    left = clientX - popup.offsetWidth - gap;
  }
  // 防止左侧溢出
  if (left < 10) left = 10;
  // 防止底部溢出
  if (top + popup.offsetHeight > window.innerHeight - 10) {
    top = window.innerHeight - popup.offsetHeight - 10;
  }
  // 防止顶部溢出
  if (top < 10) top = 10;

  popup.style.left = left + 'px';
  popup.style.top = top + 'px';
}

// 关闭 WMS 弹出框
function closeWmsPopup() {
  const popup = document.querySelector('.wms-popup');
  if (popup) {
    if (popup._mask) popup._mask.remove();
    popup.remove();
  }
  // 清理孤儿遮罩
  document.querySelectorAll('.wms-popup-mask').forEach(function(m) { m.remove(); });
}

function showWmsEmpty() {
  const viewport = document.getElementById('wmsViewport');
  const hint = document.getElementById('wmsEmptyHint');
  const controls = document.getElementById('wmsZoomControls');
  if (viewport) viewport.innerHTML = '';
  if (hint) hint.style.display = '';
  if (controls) controls.style.display = 'none';
  resetWmsZoom();
}

function clearWmsPanel() {
  // ★ 同步清空当前对话的影像快照 (否则切换回来会恢复, 与"清空"语义矛盾)
  const cur = cs();
  if (cur) {
    cur.wmsImages = [];
    _clearConvWms(activeConvId);  // ★ 同步清空 localStorage
  }
  showWmsEmpty();
}

// ==================== WMS 视口缩放与拖拽 ====================
let wmsZoom = { scale: 1, x: 0, y: 0, dragging: false, lastX: 0, lastY: 0 };

function initWmsZoom() {
  const stage = document.getElementById('wmsStage');
  if (!stage || stage._zoomInited) return;
  stage._zoomInited = true;

  // 鼠标滚轮缩放（围绕光标位置）
  stage.addEventListener('wheel', function(e) {
    e.preventDefault();
    const rect = stage.getBoundingClientRect();
    const mx = e.clientX - rect.left;   // 光标在 stage 内的 X
    const my = e.clientY - rect.top;    // 光标在 stage 内的 Y

    const oldScale = wmsZoom.scale;
    const newScale = Math.min(10, Math.max(0.1, oldScale * (e.deltaY > 0 ? 0.9 : 1.1)));

    // 围绕光标缩放：新偏移 = 光标位置 - (光标位置 - 旧偏移) * (新比例 / 旧比例)
    wmsZoom.x = mx - (mx - wmsZoom.x) * (newScale / oldScale);
    wmsZoom.y = my - (my - wmsZoom.y) * (newScale / oldScale);
    wmsZoom.scale = newScale;

    applyWmsTransform();
    updateWmsZoomUI();
  }, { passive: false });

  // 鼠标拖拽平移
  stage.addEventListener('mousedown', function(e) {
    if (e.target.closest('.wms-zoom-controls') || e.target.closest('.wms-popup')) return;
    wmsZoom.dragging = true;
    wmsZoom.lastX = e.clientX;
    wmsZoom.lastY = e.clientY;
    stage.classList.add('grabbing');
    stage.classList.remove('grab');
    e.preventDefault();
  });

  window.addEventListener('mousemove', function(e) {
    if (!wmsZoom.dragging) return;
    const dx = e.clientX - wmsZoom.lastX;
    const dy = e.clientY - wmsZoom.lastY;
    wmsZoom.x += dx;
    wmsZoom.y += dy;
    wmsZoom.lastX = e.clientX;
    wmsZoom.lastY = e.clientY;
    applyWmsTransform();
  });

  window.addEventListener('mouseup', function() {
    if (!wmsZoom.dragging) return;
    wmsZoom.dragging = false;
    const stage = document.getElementById('wmsStage');
    if (stage) { stage.classList.remove('grabbing'); stage.classList.add('grab'); }
  });

  // 缩放按钮
  document.getElementById('wmsZoomIn').onclick = function() {
    wmsZoom.scale = Math.min(10, wmsZoom.scale * 1.25);
    applyWmsTransform();
    updateWmsZoomUI();
  };
  document.getElementById('wmsZoomOut').onclick = function() {
    wmsZoom.scale = Math.max(0.1, wmsZoom.scale * 0.8);
    applyWmsTransform();
    updateWmsZoomUI();
  };
  document.getElementById('wmsZoomFit').onclick = function() { fitWmsToWindow(); };

  // 初始状态
  stage.classList.add('grab');
}

function applyWmsTransform() {
  const vp = document.getElementById('wmsViewport');
  if (!vp) return;
  vp.style.transform = 'translate(' + wmsZoom.x + 'px, ' + wmsZoom.y + 'px) scale(' + wmsZoom.scale + ')';
}

function updateWmsZoomUI() {
  const el = document.getElementById('wmsZoomLevel');
  if (el) el.textContent = Math.round(wmsZoom.scale * 100) + '%';
}

function resetWmsZoom() {
  wmsZoom.scale = 1; wmsZoom.x = 0; wmsZoom.y = 0;
  applyWmsTransform();
  updateWmsZoomUI();
  const hint = document.getElementById('wmsEmptyHint');
  const controls = document.getElementById('wmsZoomControls');
  if (hint) hint.style.display = '';
  if (controls) controls.style.display = 'none';
}

function fitWmsToWindow() {
  const stage = document.getElementById('wmsStage');
  const vp = document.getElementById('wmsViewport');
  if (!stage || !vp) return;
  const sw = stage.clientWidth, sh = stage.clientHeight;
  const vw = vp.scrollWidth, vh = vp.scrollHeight;
  if (vw === 0 || vh === 0) return;
  const scaleFit = Math.min(sw / vw, sh / vh, 1) * 0.9;
  wmsZoom.scale = scaleFit;
  wmsZoom.x = (sw - vw * scaleFit) / 2;
  wmsZoom.y = (sh - vh * scaleFit) / 2;
  applyWmsTransform();
  updateWmsZoomUI();
}

// ★ 显示影像时自动初始化缩放、显示控件
function onWmsImageLoaded() {
  const hint = document.getElementById('wmsEmptyHint');
  const controls = document.getElementById('wmsZoomControls');
  if (hint) hint.style.display = 'none';
  if (controls) controls.style.display = '';
  initWmsZoom();
}

// ==================== 右侧面板 ====================

function addResultCard(eventType, payload) { /* ... 不变 ... */
  const list = document.getElementById('resultList');
  if (!list) return;  // 回传记录面板已移除
  const card = document.createElement('div'); card.className = 'result-card';
  const ts = new Date().toLocaleTimeString();
  card.innerHTML = `<div class="result-header"><span class="result-type">${eventType}</span><span style="color:var(--text-secondary);font-size:var(--fs-sm)">${ts}</span></div><div class="result-body">${escapeHtml(JSON.stringify(payload, null, 2))}</div>`;
  list.appendChild(card); list.scrollTop = list.scrollHeight;
}

// ==================== UI 操作 (使用活跃对话状态) ====================

function updateStatus(status) { /* ... 不变 ... */
  const dot = document.getElementById('statusDot'), text = document.getElementById('statusText');
  if (!dot || !text) return; // 状态指示器已移除
  dot.className = 'status-dot ' + status;
  const labels = { connected: '已连接', disconnected: '未连接', connecting: '连接中...', error: '连接失败' };
  text.textContent = labels[status] || status;
}

function addEventLog(dir, msg, detail) { /* ... 不变 ... */
  const log = document.getElementById('eventLog');
  const item = document.createElement('div'); item.className = 'event-item ' + (dir === 'in' ? 'in' : 'out');
  let html = `${escapeHtml(msg)}`;
  if (detail) html += `<div class="detail">${escapeHtml(detail)}</div>`;
  item.innerHTML = html; log.appendChild(item); log.scrollTop = log.scrollHeight;
}

// ==================== 消息渲染 (作用于活跃对话状态) ====================

function appendUserMessage(text, msgDbId) {
  const st = cs(); if (!st) return;
  appendUserMessageTo(st, text, msgDbId);
}

function appendUserMessageTo(st, text, msgDbId) {
  const el = document.createElement('div'); el.className = 'message user';
  if (msgDbId) el.dataset.msgId = msgDbId;
  const bubble = document.createElement('div'); bubble.className = 'message-bubble'; bubble.textContent = text; el.appendChild(bubble);
  st.container.appendChild(el); scrollBottom();
  _hideWelcomeArea();
}

function appendThinking(content) {
  const st = cs(); if (!st) return;
  ensureAssistantBubbleFor(st);
  // ★ 一阶段一组状态机: 当前组已配对工具 → 这是新一轮思考, 开新组
  if (st.currentGroupHasTool || !st.currentIterationGroup) {
    ensureIterationGroup(st);
  }
  // 在当前组内找/建 thinking 块 (同轮流式追加)
  let block = st.currentIterationGroup.querySelector('.thinking-block');
  if (!block) {
    block = document.createElement('div'); block.className = 'thinking-block';
    const label = document.createElement('div'); label.className = 'label'; label.textContent = 'thinking';
    const contentEl = document.createElement('span'); contentEl.className = 'thinking-content';
    const cursor = document.createElement('span'); cursor.className = 'cursor';
    block.appendChild(label); block.appendChild(contentEl); block.appendChild(cursor);
    st.currentIterationGroup.appendChild(block);
  }
  block.querySelector('.thinking-content').textContent += content;
  scrollBottom();
}

// ★ 重建模式: 将一段完整的 thinking 内容追加到指定对话状态 (rebuildConvUI 用)
function appendThinkingTo(st, content) {
  ensureAssistantBubbleFor(st);
  ensureIterationGroup(st);
  const block = document.createElement('div'); block.className = 'thinking-block';
  const label = document.createElement('div'); label.className = 'label'; label.textContent = 'thinking';
  const contentEl = document.createElement('span'); contentEl.className = 'thinking-content';
  contentEl.textContent = content;
  block.appendChild(label); block.appendChild(contentEl);
  st.currentIterationGroup.appendChild(block);
  // 标记该组已含工具, 下一轮 thinking 会开新组
  st.currentGroupHasTool = false;
}

// ★ 确保存在"当前轮次配对容器", 并重置该组的工具配对标记
function ensureIterationGroup(st) {
  const bubble = st.currentAssistantEl.querySelector('.message-bubble');
  // 若存在未配对工具的旧组且为空, 直接复用 (避免空组)
  const group = document.createElement('div'); group.className = 'iteration-group';
  bubble.appendChild(group);
  st.currentIterationGroup = group;
  st.currentGroupHasTool = false;
}

function appendToolCall(name, args) { const st = cs(); if (!st) return; appendToolCallTo(st, name, args); }
function appendToolCallTo(st, name, args) {
  ensureAssistantBubbleFor(st);
  // ★ 一阶段一组: 工具调用追加到当前轮次组, 标记该组已配对工具
  //   (下一轮 thinking 到来时据此开新组)
  if (!st.currentIterationGroup) {
    ensureIterationGroup(st); // 兜底: 无思考直接调工具的极端情况
  }
  // ★ Codex 风格: 单色行 ▌ tool_name(args), 无彩色背景块
  const block = document.createElement('div'); block.className = 'tool-block';
  const argStr = JSON.stringify(args);
  const argDisplay = argStr.length > 120 ? argStr.slice(0, 120) + '…' : argStr;
  block.textContent = `${name}(${argDisplay})`;
  st.currentIterationGroup.appendChild(block);
  st.currentGroupHasTool = true;
  scrollBottom();
}

function appendContent(content) { const st = cs(); if (!st) return; appendContentTo(st, content); }
function appendContentTo(st, content) {
  ensureAssistantBubbleFor(st);
  st.currentContent += content;
  let textEl = st.currentAssistantEl.querySelector('.message-bubble .content-text');
  if (!textEl) {
    textEl = document.createElement('div'); textEl.className = 'content-text';
    st.currentAssistantEl.querySelector('.message-bubble').appendChild(textEl);
  }
  textEl.innerHTML = md.render(st.currentContent);
  enhanceMarkdownTables(textEl);
  textEl.querySelectorAll('pre code').forEach(block => { if (!block.classList.contains('language-none')) { Prism.highlightElement(block); } });
  scrollBottom();
}

function appendError(content) {
  const st = cs(); if (!st) return;
  ensureAssistantBubbleFor(st);
  // ★ Codex 风格: 红色边框错误行
  const block = document.createElement('div'); block.className = 'action-block';
  block.style.borderLeftColor = 'var(--accent-red)'; block.style.color = 'var(--accent-red)';
  block.textContent = `error: ${content}`;
  st.currentAssistantEl.querySelector('.message-bubble').appendChild(block); scrollBottom();
}

function ensureAssistantBubble() { const st = cs(); if (st) ensureAssistantBubbleFor(st); }
function ensureAssistantBubbleFor(st) {
  if (!st.currentAssistantEl) {
    const el = document.createElement('div'); el.className = 'message ai';
    el.innerHTML = '<div class="message-avatar"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><line x1="8" y1="16" x2="8" y2="16.01"/><line x1="16" y1="16" x2="16" y2="16.01"/></svg></div><div class="message-bubble"></div>';
    st.container.appendChild(el); st.currentAssistantEl = el; st.currentContent = '';
    // ★ 关闭欢迎区
    _hideWelcomeArea();
    // ★ 新气泡重置轮次组状态 (上一条消息的组不延续到新消息)
    st.currentIterationGroup = null;
    st.currentGroupHasTool = false;
  }
}

function createBlock(className, label) {
  const block = document.createElement('div'); block.className = className;
  const labelEl = document.createElement('div'); labelEl.className = 'label'; labelEl.textContent = label; block.appendChild(labelEl);
  return block;
}

function finishMessage() {
  const st = cs(); if (!st) return;
  finishMessageForConv(activeConvId);
  st.isSending = false;
  updateInputState();
  }

function scrollBottom() {
  const st = cs();
  if (st && st.container) {
    st.container.scrollTop = st.container.scrollHeight;
  } else {
    const el = document.getElementById('messages');
    if (el) el.scrollTop = el.scrollHeight;
  }
}

function escapeHtml(str) { return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ==================== 自定义 Modal (替代被禁用的原生 prompt) ====================
// ★ 嵌入式浏览器/WebView 可能禁用 window.prompt, 调用即抛
//   "prompt() is not supported"。用自建模态框替代, 保证交互在任何环境下可用。
// showPromptModal(title, defaultValue='') -> Promise<string|null>  (null = 用户取消)
function showPromptModal(title, defaultValue) {
  return _showAppModal({
    title: title || '',
    input: true,
    defaultValue: defaultValue || '',
    okText: '确定',
    cancelText: '取消',
  }).then(function(r) { return r ? r.inputValue : null; });
}

// 通用模态框 (内部): Promise — input=true 返回 {inputValue}, 否则返回 true; 取消返回 null
function _showAppModal(opts) {
  return new Promise(function(resolve) {
    var overlay = document.createElement('div');
    overlay.className = 'app-modal-overlay show';
    var modal = document.createElement('div');
    modal.className = 'app-modal';
    var html = '';
    html += '<div class="app-modal-title">' + escapeHtml(opts.title) + '</div>';
    if (opts.body) html += '<div class="app-modal-body">' + escapeHtml(opts.body) + '</div>';
    var inputId = 'appModalInput_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
    if (opts.input) {
      html += '<div class="app-modal-input-wrap"><input type="text" id="' + inputId + '" class="app-modal-input" /></div>';
    }
    html += '<div class="app-modal-actions">';
    html += '<button class="app-modal-btn cancel">' + escapeHtml(opts.cancelText || '取消') + '</button>';
    html += '<button class="app-modal-btn ok">' + escapeHtml(opts.okText || '确定') + '</button>';
    html += '</div>';
    modal.innerHTML = html;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    var inputEl = opts.input ? modal.querySelector('#' + inputId) : null;
    var okBtn = modal.querySelector('.app-modal-btn.ok');
    var cancelBtn = modal.querySelector('.app-modal-btn.cancel');
    if (inputEl) {
      inputEl.value = opts.defaultValue || '';
      // 异步聚焦 + 全选, 兼容刚插入 DOM 的情况
      setTimeout(function() { inputEl.focus(); inputEl.select(); }, 0);
    } else {
      setTimeout(function() { okBtn.focus(); }, 0);
    }
    var settled = false;
    function close(result) {
      if (settled) return;
      settled = true;
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      resolve(result);
    }
    okBtn.onclick = function() {
      close(opts.input ? { inputValue: inputEl.value } : true);
    };
    cancelBtn.onclick = function() { close(null); };
    // 键盘: Enter 确认, Esc 取消
    modal.addEventListener('keydown', function(e) {
      e.stopPropagation();
      if (e.key === 'Enter') { e.preventDefault(); okBtn.click(); }
      else if (e.key === 'Escape') { e.preventDefault(); cancelBtn.click(); }
    });
    // 点击遮罩区域 = 取消
    overlay.addEventListener('click', function(e) {
      if (e.target === overlay) close(null);
    });
  });
}

// ==================== 拖拽分隔条: 区域间调整尺寸 (localStorage 持久化) ====================
// 布局 key: nrms_layout = {sidebarW, chatW, bottomH}
// 约束: sidebar [180,420], chat [320,720], bottom [80,400]
const LAYOUT_KEY = 'nrms_layout';
const LAYOUT_LIMITS = { sidebar: { min: 180, max: 420 }, chat: { min: 320, max: 720 }, bottom: { min: 80, max: 400 } };
const LAYOUT_DEFAULTS = { sidebarW: 240, chatW: 400, bottomH: 150 };

function _loadLayout() {
  try { return Object.assign({}, LAYOUT_DEFAULTS, JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}')); }
  catch (e) { return Object.assign({}, LAYOUT_DEFAULTS); }
}

function _saveLayout(patch) {
  try {
    const cur = _loadLayout();
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(Object.assign({}, cur, patch)));
  } catch (e) {}
}

function _clamp(v, min, max) { return Math.min(max, Math.max(min, v)); }

function applyLayout(layout) {
  const sidebar = document.getElementById('convSidebar');
  const chat = document.getElementById('chatArea');
  const bottom = document.getElementById('bottomBar');
  if (sidebar) sidebar.style.width = layout.sidebarW + 'px';
  if (chat) chat.style.width = layout.chatW + 'px';
  if (bottom) bottom.style.height = layout.bottomH + 'px';
}

function initResizers() {
  const resizers = document.querySelectorAll('.resizer');
  resizers.forEach(function(resizer) {
    let dragging = false;
    resizer.addEventListener('mousedown', function(e) {
      e.preventDefault();
      dragging = true;
      resizer.classList.add('dragging');
      document.body.classList.add('resizing');
      if (resizer.classList.contains('resizer-h')) document.body.classList.add('resizing-h');
      const target = resizer.dataset.target;
      const startX = e.clientX, startY = e.clientY;
      const layout = _loadLayout();
      const startSidebarW = layout.sidebarW, startChatW = layout.chatW, startBottomH = layout.bottomH;
      // 拖动时的总宽度约束 (侧栏+聊天不能超过窗口)
      const winW = window.innerWidth;

      function onMove(ev) {
        if (!dragging) return;
        if (target === 'sidebar') {
          const newW = _clamp(startSidebarW + (ev.clientX - startX), LAYOUT_LIMITS.sidebar.min, LAYOUT_LIMITS.sidebar.max);
          // 约束: 不能把中间影像挤到 < 300
          if (winW - newW - startChatW < 300) return;
          applyLayout(Object.assign(layout, { sidebarW: newW }));
        } else if (target === 'chat') {
          // chat 增宽 = 鼠标左移, 故用 startX - ev.clientX
          const newW = _clamp(startChatW + (startX - ev.clientX), LAYOUT_LIMITS.chat.min, LAYOUT_LIMITS.chat.max);
          if (winW - startSidebarW - newW < 300) return;
          applyLayout(Object.assign(layout, { chatW: newW }));
        } else if (target === 'bottom') {
          const newH = _clamp(startBottomH - (ev.clientY - startY), LAYOUT_LIMITS.bottom.min, LAYOUT_LIMITS.bottom.max);
          applyLayout(Object.assign(layout, { bottomH: newH }));
        }
      }
      function onUp() {
        if (!dragging) return;
        dragging = false;
        resizer.classList.remove('dragging');
        document.body.classList.remove('resizing', 'resizing-h');
        // 持久化当前实际尺寸
        const sidebar = document.getElementById('convSidebar');
        const chat = document.getElementById('chatArea');
        const bottom = document.getElementById('bottomBar');
        _saveLayout({
          sidebarW: sidebar ? sidebar.offsetWidth : LAYOUT_DEFAULTS.sidebarW,
          chatW: chat ? chat.offsetWidth : LAYOUT_DEFAULTS.chatW,
          bottomH: bottom ? bottom.offsetHeight : LAYOUT_DEFAULTS.bottomH,
        });
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  });
}

// 启动时应用持久化的布局
applyLayout(_loadLayout());

// ==================== 记忆更新轻提示 (toast) ====================
function showMemoryToast(items, kind) {
  let container = document.querySelector('.memory-toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'memory-toast-container';
    document.body.appendChild(container);
  }
  const titles = { pattern: '已记住你的常用操作', long_term: '已更新长期记忆', summary: '已总结本次对话' };
  const title = titles[kind] || '记忆已更新';
  const toast = document.createElement('div');
  toast.className = 'memory-toast';
  const itemHtml = items.map(it =>
    `<div class="toast-item"><b>${escapeHtml(it.key)}</b>: ${escapeHtml(it.value)}</div>`
  ).join('');
  toast.innerHTML = `<div class="toast-title">${title}</div><div class="toast-body">${itemHtml}</div>`;
  container.appendChild(toast);
  // 2.8s 后自动消失
  setTimeout(() => {
    toast.classList.add('dismissing');
    setTimeout(() => toast.remove(), 280);
  }, 2800);
}

// ==================== 折叠栏初始化 (demo-bar + 底部事件日志) ====================
function initCollapsibles() {
  // 底部事件日志: 点击标题切换 .collapsed
  const eventTitle = document.getElementById('eventLogTitle');
  if (eventTitle) {
    eventTitle.addEventListener('click', function() {
      const bar = document.getElementById('bottomBar');
      if (bar) bar.classList.toggle('collapsed');
    });
  }
}

// ==================== 欢迎区管理 ====================

function _hideWelcomeArea() {
  const wa = document.getElementById('welcomeArea');
  if (wa) wa.style.display = 'none';
}

function _showWelcomeArea() {
  const wa = document.getElementById('welcomeArea');
  if (wa) wa.style.display = '';
}

// ==================== 输入框增强 ====================

function initTextareaAutoResize() {
  const textarea = document.getElementById('inputBox');
  if (!textarea) return;
  textarea.addEventListener('input', function() {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 100) + 'px';
  });
  textarea.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
}

// ==================== 启动 ====================
initWsClient();
loadConvListImmediate();
initResizers();
initCollapsibles();
initTextareaAutoResize();

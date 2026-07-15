// ==================== 右键菜单操作 ====================

// ★ 功能区: 项目管理 (project/) | 侧栏会话/分组/项目树 + 右键 CRUD

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
  document.querySelectorAll('.conversation-card').forEach(el => el.style.outline = '');
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
  document.querySelectorAll('.conversation-card').forEach(el => el.style.outline = '');
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
      _clearConvTrace(convId);
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

// ★ 全局视图右键菜单 (全部对话 / 未分类)
let _allConvMenuVisible = false;

function showAllConvContextMenu(event) {
  event.preventDefault();
  event.stopPropagation();
  _allConvMenuVisible = true;
  const menu = document.getElementById('allConvContextMenu');
  if (!menu) return;
  let x = event.clientX;
  let y = event.clientY;
  const mw = 160, mh = 60;
  if (x + mw > window.innerWidth) x = window.innerWidth - mw - 4;
  if (y + mh > window.innerHeight) y = window.innerHeight - mh - 4;
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  menu.style.display = 'block';
  document.querySelectorAll('.conv-section-header').forEach(el => el.style.outline = '');
  const target = event.currentTarget;
  if (target) target.style.outline = '1px solid var(--accent)';
}

function hideAllConvContextMenu() {
  const menu = document.getElementById('allConvContextMenu');
  if (menu) menu.style.display = 'none';
  _allConvMenuVisible = false;
  document.querySelectorAll('.conv-section-header').forEach(el => el.style.outline = '');
}

function createGroupFromMenu() {
  hideAllConvContextMenu();
  createGroupFromSidebar();
}

// ★ 统一关闭菜单 helper: 关闭所有菜单 + 清除高亮
function _hideAllContextMenus() {
  hideConvContextMenu();
  hideGroupContextMenu();
  hideAllConvContextMenu();
}

// 点击空白处或按 Esc / scroll 关闭所有菜单
document.addEventListener('click', (e) => {
  const convMenu = document.getElementById('convContextMenu');
  const groupMenu = document.getElementById('groupContextMenu');
  const allMenu = document.getElementById('allConvContextMenu');
  if (convMenu && convMenu.style.display === 'block' && !convMenu.contains(e.target)) {
    hideConvContextMenu();
  }
  if (groupMenu && groupMenu.style.display === 'block' && !groupMenu.contains(e.target)) {
    hideGroupContextMenu();
  }
  if (allMenu && allMenu.style.display === 'block' && !allMenu.contains(e.target)) {
    hideAllConvContextMenu();
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
    // 并发拉取会话列表 + 分组列表
    // activeProjectId 仅用于“当前选中项目”和新会话归属，不再作为左侧面板过滤条件
    const [convRes, groupRes] = await Promise.all([
      fetch(`${protocol}//${host}/api/v1/conversations?user_id=study_user`).then(r => r.json()),
      fetch(`${protocol}//${host}/api/v1/projects?user_id=study_user`).then(r => r.json()),
    ]);
    _convListCache = convRes.conversations || [];
    _groupsCache = groupRes.projects || [];
    renderConvTree();
  } catch (e) {
    document.getElementById('convList').innerHTML = '<div class="conv-empty">加载失败</div>';
  }
}

function renderConvTree() {
  const container = document.getElementById('convList');
  const search = (document.getElementById('convSearchInput') || {}).value || '';
  const convs = _convListCache.filter(c => {
    if (!search) return true;
    const t = (c.title || '').toLowerCase();
    return t.indexOf(search.toLowerCase()) >= 0 || c.id.indexOf(search) >= 0;
  });
  const hasProjects = _groupsCache && _groupsCache.length > 0;
  if (!convs.length && !hasProjects) {
    container.innerHTML = '<div class="conv-empty">' + (search ? '无匹配结果' : '暂无会话') + '</div>';
    return;
  }
  const collapsed = _loadGroupCollapsed();
  let html = '';
  // 按项目分组
  const byProject = {};
  const ungrouped = [];
  convs.forEach(c => {
    if (c.project_id) {
      if (!byProject[c.project_id]) byProject[c.project_id] = [];
      byProject[c.project_id].push(c);
    } else {
      ungrouped.push(c);
    }
  });

  html += '<div class="conv-section-header" oncontextmenu="showAllConvContextMenu(event)">项目</div>';
  if (_groupsCache.length) {
    _groupsCache.forEach(g => {
      const convsInGroup = byProject[g.id] || [];
      const isCol = collapsed.has(g.id);
      const escName = g.name.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
      html += '<div class="conv-group">';
      html += '<div class="project-row' + (isCol ? ' collapsed' : '') + (activeProjectId === g.id ? ' active' : '') + '" ' +
        'onclick="toggleGroup(event,\'' + g.id + '\');switchProject(\'' + g.id + '\')" ' +
        'oncontextmenu="showGroupContextMenu(event,\'' + g.id + '\',\'' + escName + '\')">' +
        '<span class="project-row-toggle">▾</span>' +
        '<span class="project-row-title">' + escapeHtml(g.name) + '</span>' +
        '<span class="project-row-count">' + convsInGroup.length + '</span>' +
        '</div>';
      html += '<div class="conv-group-items' + (isCol ? ' collapsed' : '') + '">';
      html += convsInGroup.length ? convsInGroup.map(c => _renderConvItemHtml(c)).join('') : '<div class="conv-empty" style="padding:4px 0;font-size:var(--fs-xs)">暂无会话</div>';
      html += '</div></div>';
    });
  } else {
    html += '<div class="conv-group" style="padding:4px 10px"><div class="conv-empty" style="font-size:var(--fs-sm)">暂无项目，点击上方「+ 新建项目」创建</div></div>';
  }

  if (ungrouped.length) {
    html += '<div class="conv-section-header">对话</div>';
    html += ungrouped.map(c => _renderConvItemHtml(c)).join('');
  } else if (!_groupsCache.length) {
    html = convs.map(c => _renderConvItemHtml(c)).join('');
  }

  container.innerHTML = html;
}
// 单个会话项 HTML (从原 loadConvList 抽出, 供树渲染复用)
function _renderConvItemHtml(c) {
  const title = escapeHtml(c.title || '(无标题)');
  const time = (c.updated_at || '').slice(5, 16).replace('T', ' ');
  const isActive = c.id === activeConvId ? ' active' : '';
  const status = c.status || '';
  let statusClass = 'idle';
  if (status === 'completed' && c.message_count > 0) statusClass = 'success';
  else if (status === 'stopped' || status === 'error' || status === 'failed') statusClass = 'failed';
  else if (status === 'background' || status === 'running' || status === 'pending') statusClass = 'running';
  return ` <div class="conversation-card${isActive}" onclick="openConversation('${c.id}')" oncontextmenu="showConvContextMenu(event, '${c.id}')" data-conv-id="${c.id}">
    <span class="conversation-state ${statusClass}" aria-hidden="true"></span>
    <span class="conversation-card-title" title="${title}">${title}</span>
    <span class="conversation-card-time">${time}</span>
  </div>`;
}

// 折叠状态管理
function _loadGroupCollapsed() {
  try { return new Set(JSON.parse(localStorage.getItem('nrms_group_collapsed') || '[]')); }
  catch (e) { return new Set(); }
}
function _saveGroupCollapsed(set) {
  try { localStorage.setItem('nrms_group_collapsed', JSON.stringify([...set])); } catch (e) {}
}

// 折叠/展开分组
function toggleGroup(groupIdOrEvent, maybeGroupId) {
  let groupId = groupIdOrEvent;
  if (groupIdOrEvent && typeof groupIdOrEvent === 'object') {
    groupIdOrEvent.stopPropagation();
    groupId = maybeGroupId;
  }
  const set = _loadGroupCollapsed();
  if (set.has(groupId)) set.delete(groupId); else set.add(groupId);
  _saveGroupCollapsed(set);
  renderConvTree();
}

// 切换当前选中项目（仅影响高亮和新会话归属，不过滤左侧列表）
function switchProject(projectId) {
  if (activeProjectId === projectId) return;
  activeProjectId = projectId;
  try { localStorage.setItem('nrms_active_project', JSON.stringify({ id: projectId })); } catch (e) {}
  loadConvListImmediate();
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
      loadConvListImmediate();  // 立即刷新，留在当前视图
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

    if (typeof setConversationTraceSnapshot === 'function') {
      setConversationTraceSnapshot(convId, data.trace || null);
    }

    const convMeta = _convListCache.find(x => x.id === convId) || null;
    if (convMeta) {
      const projectId = convMeta.project_id || null;
      if (projectId) {
        activeProjectId = projectId;
        try { localStorage.setItem('nrms_active_project', JSON.stringify({ id: projectId })); } catch (e) {}
        const projectMeta = _groupsCache.find(x => x.id === projectId) || null;
        if (projectMeta && typeof setActiveFolder === 'function' && projectMeta.folder_path) {
          setActiveFolder(projectMeta.folder_path, projectMeta.id, projectMeta.name);
        } else {
          renderConvTree();
        }
      } else if (activeProjectId || activeProjectPath) {
        clearActiveProject();
      }
    }

    activateConversation(convId, title);
    // ★ 高亮侧栏对应项
    document.querySelectorAll('.conversation-card').forEach(el => el.classList.remove('active'));
    const sideItem = document.querySelector(`.conversation-card[data-conv-id="${convId}"]`);
    if (sideItem) sideItem.classList.add('active');
    loadConvList();  // ★ 刷新侧栏确保列表最新
    addEventLog('in', `打开会话 ${convId.slice(0, 8)}...`, `${(data.messages || []).length} 条消息`);
  } catch (e) {
    alert('加载会话失败: ' + e.message);
  }
}
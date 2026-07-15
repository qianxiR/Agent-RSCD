/**
 * 工作区文件浏览器 (v2.4)
 * - 左侧栏顶部: 工作区根目录配置 + 文件夹树
 * - 对话归属到当前激活的文件夹项目
 *
 * 全局变量 (定义在 core/state.js):
 *   - activeProjectPath: 当前激活文件夹完整路径
 *   - activeProjectId: 当前激活项目 project_id
 *   - workspaceTreeCache: 缓存文件树
 */

// ==================== 工作区根目录管理 ====================

// ★ 功能区: 项目管理 (project/) | 工作区文件浏览器 (文件夹树, 项目=文件夹)

function loadWorkspaceRoots() {
  try {
    const raw = localStorage.getItem('nrms_workspace_roots');
    return raw ? JSON.parse(raw) : ['E:\\0文档\\2资料\\Agent'];
  } catch (e) { return ['E:\\0文档\\2资料\\Agent']; }
}

function saveWorkspaceRoots(roots) {
  try { localStorage.setItem('nrms_workspace_roots', JSON.stringify(roots)); } catch (e) {}
}

// ==================== 工作区设置弹窗 ====================

function showWorkspaceSettings() {
  const roots = loadWorkspaceRoots();

  const overlay = document.createElement('div');
  overlay.className = 'app-modal-overlay show';
  const modal = document.createElement('div');
  modal.className = 'app-modal';
  modal.style.width = '420px';

  function renderRootList() {
    return roots.map((r, i) =>
      '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">' +
        '<span style="flex:1;font-size:var(--fs-sm);color:var(--text-primary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + (r || '').replace(/"/g, '&quot;') + '">' + escapeHtml(r) + '</span>' +
        '<span class="ws-root-del" data-idx="' + i + '" style="cursor:pointer;color:var(--accent-red);font-size:14px;line-height:1">✕</span>' +
      '</div>'
    ).join('') || '<div style="font-size:var(--fs-sm);color:var(--text-muted)">暂无工作区</div>';
  }

  modal.innerHTML =
    '<div class="app-modal-title">工作区设置</div>' +
    '<div class="app-modal-body">' +
      '<div style="margin-bottom:8px;font-size:var(--fs-sm);color:var(--text-secondary)">已配置的工作区根目录：</div>' +
      '<div id="wsRootList">' + renderRootList() + '</div>' +
      '<div style="margin-top:12px;border-top:1px solid var(--border);padding-top:10px">' +
        '<div style="font-size:var(--fs-sm);color:var(--text-secondary);margin-bottom:6px">添加工作区路径：</div>' +
        '<input id="wsNewRootInput" class="app-modal-input" placeholder="输入文件夹绝对路径，如 E:\\项目\\MyWork" style="width:100%;padding:8px;font-size:var(--fs-sm)" />' +
      '</div>' +
    '</div>' +
    '<div class="app-modal-actions">' +
      '<button class="app-modal-btn cancel">取消</button>' +
      '<button class="app-modal-btn ok">添加并关闭</button>' +
    '</div>';

  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  function closeModal() { overlay.remove(); }

  // 聚焦输入框
  setTimeout(() => { modal.querySelector('#wsNewRootInput').focus(); }, 50);

  modal.querySelector('.app-modal-btn.cancel').onclick = closeModal;
  modal.querySelector('.app-modal-btn.ok').onclick = () => {
    const newPath = modal.querySelector('#wsNewRootInput').value.trim();
    if (newPath && !roots.includes(newPath)) {
      roots.push(newPath);
      saveWorkspaceRoots(roots);
    }
    closeModal();
    renderWorkspaceTree();
  };

  // 事件委托：删除按钮
  modal.addEventListener('click', e => {
    if (e.target.classList.contains('ws-root-del')) {
      const idx = parseInt(e.target.dataset.idx);
      if (!isNaN(idx)) {
        roots.splice(idx, 1);
        saveWorkspaceRoots(roots);
        modal.querySelector('#wsRootList').innerHTML = renderRootList();
        renderWorkspaceTree();
      }
    }
  });

  // Enter 提交 / ESC 关闭
  modal.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeModal();
    if (e.key === 'Enter' && e.target.id === 'wsNewRootInput') {
      modal.querySelector('.app-modal-btn.ok').click();
    }
  });
  overlay.addEventListener('click', e => { if (e.target === overlay) closeModal(); });
}

function addWsRoot() {
  const container = document.querySelector('.app-modal-body');
  if (!container) return;
  const div = document.createElement('div');
  div.style.cssText = 'display:flex;align-items:center;gap:8px;margin-bottom:6px';
  const idx = document.querySelectorAll('.ws-root-input').length;
  div.innerHTML =
    '<input class="ws-root-input" value="" data-idx="' + idx + '" ' +
      'style="flex:1;padding:6px 8px;border:1px solid var(--border);border-radius:var(--r-sm);font-size:var(--fs-sm)" />' +
    '<span onclick="this.parentElement.remove()" style="cursor:pointer;color:var(--accent-red)">✕</span>';
  container.appendChild(div);
}

// ★ 此函数在 showWorkspaceSettings 的闭包中定义, 但通过 onclick 调用时需要全局可访问
//   解决方案: 把 roots 存到 window 临时变量, 或把删除逻辑改为事件委托。
//   这里用 window 临时变量存储索引列表。
let _wsTempRoots = [];
function removeWsRoot(idx) {
  const inputs = document.querySelectorAll('.ws-root-input');
  for (const inp of inputs) {
    if (parseInt(inp.dataset.idx) === idx) {
      inp.parentElement.remove();
      break;
    }
  }
}

// ==================== 工作区文件树渲染 ====================

async function renderWorkspaceTree() {
  const container = document.getElementById('workspaceTree');
  if (!container) return;

  const roots = loadWorkspaceRoots();
  if (!roots.length) {
    container.innerHTML = '<div class="conv-empty">请先配置工作区根目录</div>';
    return;
  }

  // 调用后端 API
  const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
  const host = location.host || 'localhost:8020';
  const rootsParam = roots.map(encodeURIComponent).join(',');
  let data;
  try {
    const res = await fetch(`${protocol}//${host}/api/v1/workspace/list?roots=${rootsParam}&depth=3`);
    data = await res.json();
  } catch (e) {
    container.innerHTML = '<div class="conv-empty">加载工作区失败</div>';
    return;
  }

  // 递归渲染树
  function renderNode(node, level) {
    const hasChildren = node.children && node.children.length > 0;
    const isActive = activeProjectPath === node.path;
    const indent = level * 16;
    const expandKey = 'nrms_tree_' + node.path;
    const expanded = localStorage.getItem(expandKey) === 'expanded';
    let html = '';

    // 文件夹行
    const activeClass = isActive ? ' active' : '';
    const arrow = hasChildren ? (expanded ? '▾' : '▸') : '&nbsp;';
    html += '<div class="ws-tree-item' + activeClass + '" style="padding-left:' + indent + 'px" ' +
      'data-path="' + escapeAttr(node.path) + '" data-name="' + escapeAttr(node.name) + '" ' +
      'title="' + escapeAttr(node.path) + '">' +
      '<span class="ws-tree-arrow" onclick="toggleTreeNode(event,\'' + escapeAttr(node.path) + '\')">' + arrow + '</span>' +
      '<span class="ws-tree-label" onclick="openFolderAsProject(\'' + escapeAttr(node.path) + '\',\'' + escapeAttr(node.name) + '\')">' +
      escapeHtml(node.name) + '</span>' +
    '</div>';

    // 子节点
    if (hasChildren) {
      const display = expanded ? '' : ' style="display:none"';
      html += '<div class="ws-tree-children" data-parent="' + escapeAttr(node.path) + '"' + display + '>';
      node.children.forEach(child => { html += renderNode(child, level + 1); });
      html += '</div>';
    }
    return html;
  }

  const html = [];

  // ★ "全部对话" 入口 — 点击后取消项目过滤，显示所有对话
  const allActive = !activeProjectPath ? ' active' : '';
  html.push('<div class="ws-tree-item ws-all-conv' + allActive + '" onclick="clearActiveProject()" title="显示所有对话（不按项目过滤）">');
  html.push('<span class="ws-tree-arrow">&nbsp;</span>');
  html.push('<span class="ws-tree-label">全部对话</span>');
  html.push('</div>');

  (data.roots || []).forEach(root => {
    if (root.error) return;
    html.push('<div class="ws-root-group">');
    html.push('<div class="ws-root-label">' + escapeHtml(root.name || root.path) + '</div>');
    (root.children || []).forEach(child => { html.push(renderNode(child, 1)); });
    if (!(root.children || []).length) {
      html.push('<div class="ws-empty">(空)</div>');
    }
    html.push('</div>');
  });

  container.innerHTML = html.join('');
}

// ★ HTML 属性逃逸 (防 XSS)
function escapeAttr(str) {
  return (str || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

// ★ 树节点展开/折叠（通过 DOM 遍历，避免 CSS 选择器路径转义问题）
function toggleTreeNode(event, path) {
  event.stopPropagation();
  // 从点击的箭头找到 .ws-tree-item 父元素，再找其下一个兄弟（.ws-tree-children）
  const arrow = event.target.closest('.ws-tree-arrow') || event.target;
  const treeItem = arrow.closest('.ws-tree-item');
  if (!treeItem) return;
  const childrenDiv = treeItem.nextElementSibling;
  if (!childrenDiv || !childrenDiv.classList.contains('ws-tree-children')) return;

  const isVisible = childrenDiv.style.display !== 'none';
  childrenDiv.style.display = isVisible ? 'none' : '';
  arrow.innerHTML = isVisible ? '▸' : '▾';

  // 持久化折叠状态
  const key = 'nrms_tree_' + path;
  localStorage.setItem(key, isVisible ? 'collapsed' : 'expanded');
}

// ==================== 打开文件夹为项目 ====================

// ★ 取消项目选择 → 显示全部对话
function clearActiveProject() {
  if (!activeProjectPath && !activeProjectId) return;
  activeProjectPath = null;
  activeProjectId = null;
  try {
    localStorage.removeItem('nrms_active_folder');
    localStorage.removeItem('nrms_active_project');
  } catch (e) {}

  const projName = document.getElementById('activeProjectName');
  if (projName) projName.textContent = '全部';

  renderWorkspaceTree();
  loadConvListImmediate();
}

async function openFolderAsProject(folderPath, folderName) {
  if (activeProjectPath === folderPath) return; // 已是激活状态

  // 创建/复用 project
  const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
  const host = location.host || 'localhost:8020';
  try {
    const res = await fetch(`${protocol}//${host}/api/v1/projects`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: folderName, folder_path: folderPath }),
    });
    const data = await res.json();
    if (data.status !== 'success') {
      alert('打开项目失败: ' + (data.msg || '未知错误'));
      return;
    }
    setActiveFolder(folderPath, data.project_id, folderName);
  } catch (e) {
    alert('打开项目失败: ' + e.message);
  }
}

function setActiveFolder(folderPath, projectId, folderName) {
  activeProjectPath = folderPath;
  activeProjectId = projectId;
  // 持久化
  try {
    localStorage.setItem('nrms_active_folder', JSON.stringify({ path: folderPath, id: projectId, name: folderName }));
    localStorage.setItem('nrms_active_project', JSON.stringify({ id: projectId }));
  } catch (e) {}

  // 更新侧栏标题
  const projName = document.getElementById('activeProjectName');
  if (projName) {
    projName.textContent = folderName || (folderPath ? folderPath.split(/[\\/]/).filter(Boolean).pop() : '');
  }

  // 刷新文件树高亮
  renderWorkspaceTree();

  // 加载该项目的对话列表
  loadConvListImmediate();
}

// ==================== 启动时恢复上一次激活的项目 ====================

function restoreActiveFolder() {
  try {
    const saved = JSON.parse(localStorage.getItem('nrms_active_folder') || 'null');
    if (saved && saved.path && saved.id) {
      setActiveFolder(saved.path, saved.id, saved.name);
    }
  } catch (e) {}
}

// ==================== 项目右键菜单 ====================

let _wsContextTarget = null; // {path, name, projectId}

function showProjectContextMenu(event, path, name) {
  event.preventDefault();
  event.stopPropagation();

  // 先获取 projectId（从 DOM 的 data-path 找到对应项目）
  _wsContextTarget = { path, name, projectId: null };
  // 如果这个路径就是当前激活项目
  if (activeProjectPath === path) {
    _wsContextTarget.projectId = activeProjectId;
  }

  const menu = document.getElementById('wsProjectContextMenu');
  if (!menu) return;
  menu.style.display = 'block';
  menu.style.left = event.clientX + 'px';
  menu.style.top = event.clientY + 'px';

  // 点击其他地方关闭
  setTimeout(() => {
    document.addEventListener('click', _hideProjectContextMenu, { once: true });
  }, 0);
}

function _hideProjectContextMenu() {
  const menu = document.getElementById('wsProjectContextMenu');
  if (menu) menu.style.display = 'none';
}

async function renameProjectFromMenu() {
  _hideProjectContextMenu();
  if (!_wsContextTarget) return;

  // 需要先确保有 projectId（如果不是当前激活项目，先 UPSERT）
  let projectId = _wsContextTarget.projectId;
  if (!projectId) {
    const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
    const host = location.host || 'localhost:8020';
    try {
      const res = await fetch(`${protocol}//${host}/api/v1/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: _wsContextTarget.name, folder_path: _wsContextTarget.path }),
      });
      const data = await res.json();
      if (data.status === 'success') projectId = data.project_id;
    } catch (e) {}
  }
  if (!projectId) { alert('无法定位该项目'); return; }

  // 弹出重命名输入框（复用 _showAppModal）
  const result = await _showAppModal({
    title: '重命名项目',
    input: true,
    defaultValue: _wsContextTarget.name,
    okText: '确定',
    cancelText: '取消',
  });
  if (!result || !result.inputValue || !result.inputValue.trim()) return;

  const newName = result.inputValue.trim();
  const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
  const host = location.host || 'localhost:8020';
  try {
    const res = await fetch(`${protocol}//${host}/api/v1/projects/${projectId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newName }),
    });
    const data = await res.json();
    if (data.status === 'success') {
      renderWorkspaceTree();
    }
  } catch (e) { alert('重命名失败: ' + e.message); }
}

async function deleteProjectFromMenu() {
  _hideProjectContextMenu();
  if (!_wsContextTarget) return;

  if (!confirm('确定删除项目 "' + _wsContextTarget.name + '"？\n（仅删除数据库记录，不影响本地文件夹）')) return;

  let projectId = _wsContextTarget.projectId;
  if (!projectId) {
    // 查找 project_id
    const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
    const host = location.host || 'localhost:8020';
    try {
      const res = await fetch(`${protocol}//${host}/api/v1/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: _wsContextTarget.name, folder_path: _wsContextTarget.path }),
      });
      const data = await res.json();
      if (data.status === 'success') projectId = data.project_id;
    } catch (e) {}
  }
  if (!projectId) { alert('无法定位该项目'); return; }

  const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
  const host = location.host || 'localhost:8020';
  try {
    const req = await fetch(`${protocol}//${host}/api/v1/projects/${projectId}`, { method: 'DELETE' });
    const data = await req.json();
    if (data.status === 'success') {
      // 如果删除的是当前激活项目，清除激活状态
      if (activeProjectId === projectId) {
        clearActiveProject();
      }
      renderWorkspaceTree();
      loadConvListImmediate();
    }
  } catch (e) { alert('删除失败: ' + e.message); }
}

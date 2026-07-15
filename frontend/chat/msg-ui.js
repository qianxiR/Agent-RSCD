// ==================== UI 操作 (使用活跃对话状态) ====================

// ★ 功能区: AI 聊天 (chat/) | 消息气泡渲染 + Modal + 布局拖拽 + Markdown 增强

function updateStatus(status) { /* ... 不变 ... */
  const dot = document.getElementById('statusDot'), text = document.getElementById('statusText');
  if (!dot || !text) return; // 状态指示器已移除
  dot.className = 'status-dot ' + status;
  const labels = { connected: '已连接', disconnected: '未连接', connecting: '连接中...', error: '连接失败' };
  text.textContent = labels[status] || status;
}

function _appendEventLogDom(log, dir, msg, detail) {
  const isThinking = dir === 'in' && typeof msg === 'string' && msg.indexOf('thinking (') === 0;
  if (isThinking) {
    const tokenMatch = /thinking \((\d+) tokens\)/.exec(msg || '');
    const tokenCount = tokenMatch ? parseInt(tokenMatch[1], 10) : 0;
    const last = log.lastElementChild;
    if (last && last.dataset && last.dataset.kind === 'thinking-summary') {
      const prevTokens = parseInt(last.dataset.tokenTotal || '0', 10) || 0;
      const prevCount = parseInt(last.dataset.segmentCount || '0', 10) || 0;
      const nextTokens = prevTokens + tokenCount;
      const nextCount = prevCount + 1;
      last.dataset.tokenTotal = String(nextTokens);
      last.dataset.segmentCount = String(nextCount);
      let html = 'thinking 汇总（' + nextCount + ' 段，' + nextTokens + ' tokens）';
      if (detail) {
        const prevDetail = last.dataset.fullDetail || '';
        const mergedDetail = prevDetail ? (prevDetail + '\n\n' + detail) : detail;
        last.dataset.fullDetail = mergedDetail;
        html += `<div class="detail">${escapeHtml(mergedDetail)}</div>`;
      }
      last.innerHTML = html;
      log.scrollTop = log.scrollHeight;
      return;
    }

    const item = document.createElement('div');
    item.className = 'event-item in';
    item.dataset.kind = 'thinking-summary';
    item.dataset.tokenTotal = String(tokenCount);
    item.dataset.segmentCount = '1';
    item.dataset.fullDetail = detail || '';
    let html = 'thinking 汇总（1 段，' + tokenCount + ' tokens）';
    if (detail) html += `<div class="detail">${escapeHtml(detail)}</div>`;
    item.innerHTML = html;
    log.appendChild(item);
    log.scrollTop = log.scrollHeight;
    return;
  }

  const item = document.createElement('div'); item.className = 'event-item ' + (dir === 'in' ? 'in' : 'out');
  let html = `${escapeHtml(msg)}`;
  if (detail) html += `<div class="detail">${escapeHtml(detail)}</div>`;
  item.innerHTML = html; log.appendChild(item); log.scrollTop = log.scrollHeight;
}

function addEventLog(dir, msg, detail, convId) { /* ... 不变 ... */
  const targetConvId = convId || activeConvId || (wsClient ? wsClient.conversationId : null);
  if (targetConvId && typeof recordConversationEventLog === 'function') {
    recordConversationEventLog(targetConvId, dir, msg, detail);
  }
  const log = document.getElementById('eventLog');
  if (!log) return;
  _appendEventLogDom(log, dir, msg, detail);
}

let _traceOverlayEl = null;
let _traceWindowConvId = null;

function _renderEventLogPanelForConv(convId) {
  const log = document.getElementById('eventLog');
  if (!log) return;
  log.innerHTML = '';
  if (!convId) return;
  const trace = typeof getConversationTraceSnapshot === 'function'
    ? getConversationTraceSnapshot(convId)
    : { eventLogs: [] };
  const items = trace.eventLogs || [];
  if (!items.length) return;

  items.forEach(function(item) {
    _appendEventLogDom(log, item.dir || 'in', item.msg || '', item.detail || '');
  });
}

function openConversationTraceWindow() {
  if (!activeConvId) {
    alert('当前没有活跃对话');
    return;
  }
  _traceWindowConvId = activeConvId;
  if (_traceOverlayEl && _traceOverlayEl.parentNode) _traceOverlayEl.parentNode.removeChild(_traceOverlayEl);

  const overlay = document.createElement('div');
  overlay.className = 'app-modal-overlay show trace-modal-overlay';
  overlay.innerHTML = '' +
    '<div class="app-modal trace-modal">' +
      '<div class="trace-modal-head">' +
        '<div>' +
          '<div class="app-modal-title">对话执行日志</div>' +
          '<div class="trace-modal-sub" id="traceModalSub"></div>' +
        '</div>' +
        '<button class="trace-modal-close" type="button" aria-label="关闭">×</button>' +
      '</div>' +
      '<div class="trace-modal-grid">' +
        '<section class="trace-section"><h3>Prompt 记录</h3><div class="trace-list" id="tracePromptList"></div></section>' +
        '<section class="trace-section"><h3>事件日志</h3><div class="trace-list" id="traceEventList"></div></section>' +
        '<section class="trace-section"><h3>WebSocket 记录</h3><div class="trace-list" id="traceWsList"></div></section>' +
      '</div>' +
    '</div>';
  document.body.appendChild(overlay);
  _traceOverlayEl = overlay;

  overlay.querySelector('.trace-modal-close').onclick = closeConversationTraceWindow;
  overlay.addEventListener('click', function(e) {
    if (e.target === overlay) closeConversationTraceWindow();
  });
  document.addEventListener('keydown', _handleTraceModalEscape);

  refreshConversationTraceWindow();
}

function closeConversationTraceWindow() {
  if (_traceOverlayEl && _traceOverlayEl.parentNode) {
    _traceOverlayEl.parentNode.removeChild(_traceOverlayEl);
  }
  _traceOverlayEl = null;
  document.removeEventListener('keydown', _handleTraceModalEscape);
}

function _handleTraceModalEscape(e) {
  if (e.key === 'Escape') closeConversationTraceWindow();
}

function refreshConversationTraceWindow() {
  if (!_traceOverlayEl || !_traceWindowConvId) return;
  const trace = typeof getConversationTraceSnapshot === 'function'
    ? getConversationTraceSnapshot(_traceWindowConvId)
    : { eventLogs: [], promptLogs: [], wsLogs: [] };
  const title = (convStates[_traceWindowConvId] && convStates[_traceWindowConvId].title) || _traceWindowConvId;

  const mergedEventLogs = [];
  let finalThinkingEvent = null;
  (trace.eventLogs || []).forEach(function(item) {
    const isThinking = item && typeof item.msg === 'string' && item.msg.indexOf('thinking (') === 0;
    if (!isThinking) {
      mergedEventLogs.push(item);
      return;
    }

    const tokenMatch = /thinking \((\d+) tokens\)/.exec(item.msg || '');
    const tokenCount = tokenMatch ? parseInt(tokenMatch[1], 10) : 0;
    if (!finalThinkingEvent) {
      finalThinkingEvent = {
        time: item.time,
        dir: item.dir,
        msg: '最终 thinking 汇总',
        detail: item.detail || '',
        _tokenTotal: tokenCount,
        _count: 1,
      };
      return;
    }

    finalThinkingEvent.time = item.time || finalThinkingEvent.time;
    finalThinkingEvent._tokenTotal += tokenCount;
    finalThinkingEvent._count += 1;
    if (item.detail) {
      finalThinkingEvent.detail += (finalThinkingEvent.detail ? '\n\n' : '') + item.detail;
    }
  });
  if (finalThinkingEvent) {
    finalThinkingEvent.msg = '最终 thinking 汇总（' + finalThinkingEvent._count + ' 段，' + finalThinkingEvent._tokenTotal + ' tokens）';
    delete finalThinkingEvent._tokenTotal;
    delete finalThinkingEvent._count;
    mergedEventLogs.unshift(finalThinkingEvent);
  }

  const mergedWsLogs = [];
  let finalWsThinking = null;

  (trace.wsLogs || []).forEach(function(item) {
    const isThinkingWs = item && item.direction === 'in' && item.kind === 'thinking';
    if (!isThinkingWs) {
      mergedWsLogs.push(item);
      return;
    }

    if (!finalWsThinking) {
      finalWsThinking = {
        time: item.time,
        direction: item.direction,
        kind: '最终 thinking 汇总',
        payload: '',
        _count: 1,
      };
      return;
    }

    finalWsThinking.time = item.time || finalWsThinking.time;
    finalWsThinking._count += 1;
  });
  if (finalWsThinking) {
    finalWsThinking.kind = '最终 thinking 汇总（' + finalWsThinking._count + ' 段）';
    finalWsThinking.payload = '已折叠 thinking token 流，不展示逐段内容。';
    delete finalWsThinking._count;
    mergedWsLogs.unshift(finalWsThinking);
  }

  function renderItems(items, kind) {
    if (!items || !items.length) return '<div class="trace-empty">暂无记录</div>';
    return items.map(function(item) {
      if (kind === 'event') {
        return '<div class="trace-item ' + item.dir + '">' +
          '<div class="trace-item-h"><span class="trace-tag">' + escapeHtml(item.dir.toUpperCase()) + '</span><span class="trace-time">' + escapeHtml(item.time) + '</span></div>' +
          '<div class="trace-main">' + escapeHtml(item.msg || '') + '</div>' +
          (item.detail ? '<pre class="trace-pre">' + escapeHtml(item.detail) + '</pre>' : '') +
        '</div>';
      }
      if (kind === 'prompt') {
        return '<div class="trace-item prompt">' +
          '<div class="trace-item-h"><span class="trace-tag">' + escapeHtml(item.phase || 'prompt') + '</span><span class="trace-time">' + escapeHtml(item.time) + '</span></div>' +
          '<pre class="trace-pre">' + escapeHtml(item.payload || '') + '</pre>' +
        '</div>';
      }
      return '<div class="trace-item ws ' + escapeHtml(item.direction || 'in') + '">' +
        '<div class="trace-item-h"><span class="trace-tag">' + escapeHtml((item.direction || 'in').toUpperCase()) + ' · ' + escapeHtml(item.kind || 'ws') + '</span><span class="trace-time">' + escapeHtml(item.time) + '</span></div>' +
        '<pre class="trace-pre">' + escapeHtml(item.payload || '') + '</pre>' +
      '</div>';
    }).join('');
  }

  const sub = _traceOverlayEl.querySelector('#traceModalSub');
  const eventList = _traceOverlayEl.querySelector('#traceEventList');
  const promptList = _traceOverlayEl.querySelector('#tracePromptList');
  const wsList = _traceOverlayEl.querySelector('#traceWsList');
  if (sub) sub.textContent = '会话：' + title + ' · ' + _traceWindowConvId;
  if (eventList) eventList.innerHTML = renderItems(mergedEventLogs, 'event');
  if (promptList) promptList.innerHTML = renderItems(trace.promptLogs, 'prompt');
  if (wsList) wsList.innerHTML = renderItems(mergedWsLogs, 'ws');
}

// ==================== 消息渲染 (作用于活跃对话状态) ====================

function appendUserMessage(text, msgDbId) {
  const st = cs(); if (!st) return;
  appendUserMessageTo(st, text, msgDbId);
}

function appendUserMessageTo(st, text, msgDbId, nodeId) {
  const el = document.createElement('div'); el.className = 'message user';
  if (msgDbId) el.dataset.msgId = msgDbId;
  if (nodeId) el.dataset.nodeId = nodeId;  // ★ v2.5 消息分支: 挂 node_id
  const bubble = document.createElement('div'); bubble.className = 'message-bubble'; bubble.textContent = text; el.appendChild(bubble);
  // ★ v2.5 消息分支: 从这条消息重新生成 (fork + 重新对话)
  if (nodeId) {
    const btn = document.createElement('button');
    btn.className = 'msg-action-btn regenerate-btn';
    btn.title = '从这里重新生成';
    btn.innerHTML = '↻';
    btn.onclick = function(e) {
      e.stopPropagation();
      if (typeof handleRegenerate === 'function') handleRegenerate(nodeId, text);
    };
    el.appendChild(btn);
  }
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
      const winW = window.innerWidth;

      function onMove(ev) {
        if (!dragging) return;
        if (target === 'sidebar') {
          const newW = _clamp(startSidebarW + (ev.clientX - startX), LAYOUT_LIMITS.sidebar.min, LAYOUT_LIMITS.sidebar.max);
          if (winW - newW - startChatW < 300) return;
          applyLayout(Object.assign(layout, { sidebarW: newW }));
        } else if (target === 'chat') {
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

// ==================== 折叠栏初始化 ====================
function initCollapsibles() {}

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
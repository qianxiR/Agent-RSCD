/**
 * Phase F: 任务日志监控面板 (#19)
 * ---------------------------------------------------------------------------
 * 对接后端:
 *   - GET /api/v1/tasks?conversation_id=...  列出 AI 任务
 *   - GET /api/v1/tasks/{id}                 任务详情
 *   - GET /api/v1/tasks/{id}/logs            任务执行日志
 *
 * 入口: openTaskMonitor()  (绑定到 ai-actions 按钮的 onclick)
 */
// ★ 功能区: 地图/影像 (map/) | 任务日志监控面板 (对接 /api/v1/tasks)

(function () {
  'use strict';

  /**
   * 打开任务监控面板 (Modal).
   */
  window.openTaskMonitor = function () {
    _ensureStyles();
    const mask = document.createElement('div');
    mask.className = 'task-monitor-mask';
    mask.innerHTML = `
      <div class="task-monitor-modal">
        <div class="task-monitor-header">
          <h3>📋 任务日志监控</h3>
          <div class="task-monitor-actions">
            <select id="taskFilterStatus">
              <option value="">全部状态</option>
              <option value="running">运行中</option>
              <option value="done">已完成</option>
              <option value="failed">失败</option>
              <option value="pending">待处理</option>
              <option value="cancelled">已取消</option>
            </select>
            <input type="text" id="taskFilterConv" placeholder="会话 ID (可选)" style="width:160px" />
            <button class="tm-btn" onclick="refreshTaskList()">刷新</button>
            <button class="tm-btn tm-close" onclick="closeTaskMonitor()">关闭</button>
          </div>
        </div>
        <div class="task-monitor-body">
          <div class="task-list-pane" id="taskListPane">
            <div class="task-list-loading">加载中...</div>
          </div>
          <div class="task-detail-pane" id="taskDetailPane">
            <div class="task-detail-empty">← 点击左侧任务查看详情</div>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(mask);
    mask.addEventListener('click', function (e) {
      if (e.target === mask) window.closeTaskMonitor();
    });
    // 默认按当前会话过滤
    const curConv = (typeof activeConvId !== 'undefined' && activeConvId) ? activeConvId : '';
    if (curConv) document.getElementById('taskFilterConv').value = curConv;
    setTimeout(refreshTaskList, 50);
  };

  window.closeTaskMonitor = function () {
    const m = document.querySelector('.task-monitor-mask');
    if (m) m.remove();
  };

  window.refreshTaskList = function () {
    const status = document.getElementById('taskFilterStatus').value;
    const conv = document.getElementById('taskFilterConv').value.trim();
    const pane = document.getElementById('taskListPane');
    if (!pane) return;
    pane.innerHTML = '<div class="task-list-loading">加载中...</div>';
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    if (conv) params.set('conversation_id', conv);
    params.set('limit', '50');
    fetch('/api/v1/tasks?' + params.toString())
      .then(r => r.json())
      .then(data => {
        if (data.status !== 'success') {
          pane.innerHTML = '<div class="task-list-empty">查询失败: ' + _esc(data.msg || '') + '</div>';
          return;
        }
        const tasks = data.tasks || [];
        if (!tasks.length) {
          pane.innerHTML = '<div class="task-list-empty">无任务记录</div>';
          return;
        }
        pane.innerHTML = tasks.map(t => _taskRowHtml(t)).join('');
        // 绑定点击
        pane.querySelectorAll('.task-row').forEach(row => {
          row.addEventListener('click', () => loadTaskDetail(row.dataset.id));
        });
      })
      .catch(e => {
        pane.innerHTML = '<div class="task-list-empty">查询异常: ' + _esc(e.message) + '</div>';
      });
  };

  window.loadTaskDetail = function (taskId) {
    const detail = document.getElementById('taskDetailPane');
    if (!detail) return;
    detail.innerHTML = '<div class="task-detail-loading">加载中...</div>';
    // 并发拉详情 + 日志
    Promise.all([
      fetch('/api/v1/tasks/' + taskId).then(r => r.json()),
      fetch('/api/v1/tasks/' + taskId + '/logs').then(r => r.json()),
    ]).then(([taskResp, logResp]) => {
      if (taskResp.status !== 'success') {
        detail.innerHTML = '<div class="task-detail-empty">详情加载失败</div>';
        return;
      }
      const t = taskResp.task;
      const logs = (logResp.logs || []);
      let html = `
        <div class="task-detail-card">
          <h4>任务 #${t.id} — ${_esc(t.tool_name || t.task_type || '')}</h4>
          <table class="task-attr-table">
            <tr><td>类型</td><td>${_esc(t.task_type || '-')}</td></tr>
            <tr><td>工具</td><td>${_esc(t.tool_name || '-')}</td></tr>
            <tr><td>状态</td><td><span class="task-status task-status-${t.status}">${_esc(t.status)}</span></td></tr>
            <tr><td>进度</td><td>${t.progress || 0}%</td></tr>
            <tr><td>会话</td><td title="${_esc(t.conversation_id || '')}">${_esc((t.conversation_id || '').slice(0, 12))}...</td></tr>
            <tr><td>开始</td><td>${_esc(t.started_at || '-')}</td></tr>
            <tr><td>完成</td><td>${_esc(t.finished_at || '-')}</td></tr>
            <tr><td>耗时</td><td>${_elapsed(t.started_at, t.finished_at)}</td></tr>
            ${t.error ? `<tr><td colspan="2" style="color:#c0392b">错误: ${_esc(t.error)}</td></tr>` : ''}
          </table>
      `;
      if (t.output) {
        html += '<div class="task-output"><b>输出摘要:</b><pre>' + _esc(JSON.stringify(t.output, null, 2).slice(0, 800)) + '</pre></div>';
      }
      html += '<div class="task-logs"><b>执行日志 (' + logs.length + '):</b>';
      if (logs.length) {
        html += '<ul class="log-list">';
        logs.forEach(l => {
          const levelClass = 'log-level-' + (l.level || 'info');
          const elapsed = l.elapsed_ms != null ? ' <span class="log-elapsed">(' + l.elapsed_ms + 'ms)</span>' : '';
          html += `<li class="${levelClass}"><span class="log-time">${_esc((l.created_at || '').slice(11, 19))}</span> <span class="log-level">[${_esc(l.level || 'info')}]</span> ${_esc(l.message)}${elapsed}</li>`;
        });
        html += '</ul>';
      } else {
        html += '<div class="log-empty">无日志</div>';
      }
      html += '</div></div>';
      detail.innerHTML = html;
    }).catch(e => {
      detail.innerHTML = '<div class="task-detail-empty">加载异常: ' + _esc(e.message) + '</div>';
    });
  };

  function _taskRowHtml(t) {
    const type = t.tool_name || t.task_type || 'task';
    const elapsed = _elapsed(t.started_at, t.finished_at);
    return `
      <div class="task-row" data-id="${t.id}">
        <div class="task-row-head">
          <span class="task-status task-status-${t.status}">${_esc(t.status)}</span>
          <span class="task-row-type">#${t.id} ${_esc(type)}</span>
        </div>
        <div class="task-row-meta">
          <span>⏱ ${elapsed}</span>
          <span>📅 ${_esc((t.created_at || '').slice(0, 19))}</span>
        </div>
        ${t.error ? `<div class="task-row-error">${_esc(t.error.slice(0, 80))}</div>` : ''}
      </div>
    `;
  }

  function _elapsed(start, end) {
    if (!start || !end) return '-';
    try {
      const ms = new Date(end) - new Date(start);
      if (ms < 1000) return ms + 'ms';
      if (ms < 60000) return (ms / 1000).toFixed(1) + 's';
      return (ms / 60000).toFixed(1) + 'min';
    } catch (e) { return '-'; }
  }

  function _esc(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function _ensureStyles() {
    if (document.getElementById('task-monitor-styles')) return;
    const css = `
      .task-monitor-mask { position:fixed; inset:0; background:rgba(0,0,0,0.5); z-index:9999; display:flex; align-items:center; justify-content:center; }
      .task-monitor-modal { background:#fff; border-radius:10px; width:88vw; max-width:1100px; height:78vh; display:flex; flex-direction:column; overflow:hidden; box-shadow:0 10px 40px rgba(0,0,0,0.3); }
      .task-monitor-header { padding:14px 18px; background:var(--bg-panel,#f1f7fc); border-bottom:1px solid var(--border-light,#cee0ee); display:flex; justify-content:space-between; align-items:center; }
      .task-monitor-header h3 { margin:0; color:var(--text-primary,#1e2d3a); font-size:16px; }
      .task-monitor-actions { display:flex; gap:8px; align-items:center; }
      .task-monitor-actions select, .task-monitor-actions input { padding:5px 8px; border:1px solid var(--border-light,#cee0ee); border-radius:4px; font-size:13px; }
      .tm-btn { padding:5px 12px; background:var(--accent-blue,#559ed8); color:#fff; border:none; border-radius:4px; cursor:pointer; font-size:13px; }
      .tm-btn.tm-close { background:#888; }
      .tm-btn:hover { opacity:0.9; }
      .task-monitor-body { flex:1; display:flex; overflow:hidden; }
      .task-list-pane { width:42%; border-right:1px solid var(--border-light,#cee0ee); overflow-y:auto; padding:8px; }
      .task-detail-pane { flex:1; overflow-y:auto; padding:14px 18px; background:#fafdff; }
      .task-list-loading, .task-list-empty, .task-detail-empty, .task-detail-loading { padding:20px; text-align:center; color:var(--text-secondary,#5a7488); }
      .task-row { padding:10px 12px; border-radius:6px; cursor:pointer; border:1px solid transparent; margin-bottom:6px; transition:all 0.15s; background:#fff; }
      .task-row:hover { background:var(--bg-hover,#e0eef8); border-color:var(--border-light,#cee0ee); }
      .task-row-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; }
      .task-row-type { font-weight:600; color:var(--text-primary,#1e2d3a); font-size:13px; }
      .task-row-meta { display:flex; gap:14px; font-size:11px; color:var(--text-secondary,#5a7488); }
      .task-row-error { margin-top:4px; font-size:11px; color:#c0392b; }
      .task-status { padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600; color:#fff; }
      .task-status-pending { background:#999; }
      .task-status-running { background:#f39c12; }
      .task-status-done { background:#27ae60; }
      .task-status-failed { background:#e74c3c; }
      .task-status-cancelled { background:#7f8c8d; }
      .task-detail-card h4 { margin:0 0 12px; color:var(--text-primary,#1e2d3a); }
      .task-attr-table { width:100%; border-collapse:collapse; font-size:13px; margin-bottom:14px; }
      .task-attr-table td { padding:5px 10px; border-bottom:1px solid var(--border-light,#eee); }
      .task-attr-table td:first-child { width:80px; color:var(--text-secondary,#5a7488); font-weight:600; }
      .task-output pre, .task-logs pre { background:#f6f8fa; padding:8px; border-radius:4px; font-size:11px; max-height:180px; overflow:auto; }
      .log-list { list-style:none; padding:0; margin:8px 0 0; font-family:Consolas,monospace; font-size:12px; }
      .log-list li { padding:4px 6px; border-bottom:1px solid #f0f0f0; }
      .log-level-error { background:#fff5f5; }
      .log-level-warning { background:#fffaf0; }
      .log-time { color:#999; }
      .log-level { color:#666; font-weight:600; }
      .log-level-error .log-level { color:#c0392b; }
      .log-level-warning .log-level { color:#e67e22; }
      .log-elapsed { color:#aaa; font-size:11px; }
      .log-empty { color:var(--text-secondary,#5a7488); font-style:italic; padding:10px; }
    `;
    const style = document.createElement('style');
    style.id = 'task-monitor-styles';
    style.textContent = css;
    document.head.appendChild(style);
  }

  console.log('[TaskMonitor] 模块已加载 (任务日志监控就绪)');
})();

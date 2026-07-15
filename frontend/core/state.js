// ==================== 多对话状态管理 ====================
// ★ convStates: 每个对话的独立状态 (key = conversation_id)
//   { isSending, _stopped, currentAssistantEl, currentContent,
//     thinkingBuffer, thinkingTimer, thinkingTokenCount, _stopTimeoutId,
//     title, messagesContainer (DOM element) }
// ★ 功能区: 基础设施 (core/) | 全局状态管理 (convStates/activeConvId/wsClient)

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
  // 入参:
  //   convId: 对话唯一标识, 用于隔离每个对话的地图图层与视图状态。
  // 方法:
  //   若状态不存在则初始化一份完整对话状态, 同时恢复该对话持久化的图层快照与地图视图;
  //   这样切换对话时, 图层叠加顺序与地图坐标状态都能独立保存。
  // 出参:
  //   返回 convId 对应的状态对象; 若首次创建则为带默认字段的新对象。
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
      mapView: _loadConvMapView(convId),
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

function _loadConvMapView(convId) {
  // 入参:
  //   convId: 对话唯一标识, 对应 localStorage 中的地图视图快照键。
  // 方法:
  //   读取并解析该对话最近一次保存的地图中心点/缩放级别/旋转角;
  //   失败时回退为 null, 让调用方决定是否执行首次定位。
  // 出参:
  //   返回 {center, zoom, rotation} 结构的视图快照, 或 null。
  try {
    const raw = localStorage.getItem('nrms_map_view_' + convId);
    return raw ? JSON.parse(raw) : null;
  } catch (e) { return null; }
}

function _persistConvMapView(convId) {
  // 入参:
  //   convId: 对话唯一标识, 用于把当前内存中的地图视图同步到本地持久化。
  // 方法:
  //   仅在对应对话状态存在时写入 localStorage, 保证刷新页面后仍能恢复用户离开前的坐标系状态。
  // 出参:
  //   无返回值; 成功时更新 localStorage, 失败时静默忽略。
  try {
    const st = convStates[convId];
    if (!st) return;
    localStorage.setItem('nrms_map_view_' + convId, JSON.stringify(st.mapView || null));
  } catch (e) {}
}

function _clearConvMapView(convId) {
  // 入参:
  //   convId: 对话唯一标识, 对应待清理的地图视图快照。
  // 方法:
  //   删除该对话持久化的地图视图, 让“清空地图”后的恢复语义回到默认初始状态。
  // 出参:
  //   无返回值; 成功时 localStorage 中对应键被移除。
  try { localStorage.removeItem('nrms_map_view_' + convId); } catch (e) {}
}

// 当前活跃对话的状态引用 (便捷访问)
function cs() { return activeConvId ? convStates[activeConvId] : null; }

// ==================== 对话日志追踪 ====================

let convTraceStore = {};
let _traceSyncTimers = {};

function _normalizeTraceSnapshot(trace) {
  const data = trace || {};
  return {
    eventLogs: Array.isArray(data.eventLogs) ? data.eventLogs : [],
    promptLogs: Array.isArray(data.promptLogs) ? data.promptLogs : [],
    wsLogs: Array.isArray(data.wsLogs) ? data.wsLogs : [],
  };
}

function _persistConvTrace(convId) {
  if (!convId || !convTraceStore[convId]) return;
  if (_traceSyncTimers[convId]) clearTimeout(_traceSyncTimers[convId]);
  _traceSyncTimers[convId] = setTimeout(function() {
    _flushConvTrace(convId);
  }, 300);
}

async function _flushConvTrace(convId) {
  if (!convId || !convTraceStore[convId]) return;
  delete _traceSyncTimers[convId];
  try {
    const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
    const host = location.host || 'localhost:8020';
    await fetch(`${protocol}//${host}/api/v1/conversations/${convId}/trace`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(convTraceStore[convId]),
    });
  } catch (e) {}
}

function _loadConvTrace(convId) {
  if (!convId) return null;
  return null;
}

function setConversationTraceSnapshot(convId, trace) {
  if (!convId) return;
  convTraceStore[convId] = _normalizeTraceSnapshot(trace);
}

function _clearConvTrace(convId) {
  if (!convId) return;
  delete convTraceStore[convId];
  if (_traceSyncTimers[convId]) {
    clearTimeout(_traceSyncTimers[convId]);
    delete _traceSyncTimers[convId];
  }
}

function _ensureConvTrace(convId) {
  if (!convId) return null;
  if (!convTraceStore[convId]) {
    convTraceStore[convId] = _normalizeTraceSnapshot(_loadConvTrace(convId));
  }
  return convTraceStore[convId];
}

function _traceNow() {
  return new Date().toISOString();
}

function _traceText(payload) {
  try {
    if (typeof payload === 'string') return payload;
    return JSON.stringify(payload, null, 2);
  } catch (e) {
    return String(payload);
  }
}

function _pushTraceItem(list, item, maxItems) {
  list.push(item);
  if (list.length > maxItems) list.splice(0, list.length - maxItems);
}

function recordConversationEventLog(convId, dir, msg, detail) {
  const store = _ensureConvTrace(convId);
  if (!store) return;
  _pushTraceItem(store.eventLogs, {
    time: _traceNow(),
    dir: dir === 'in' ? 'in' : 'out',
    msg: msg || '',
    detail: detail || '',
  }, 500);
  _persistConvTrace(convId);
  if (typeof refreshConversationTraceWindow === 'function') refreshConversationTraceWindow();
}

function recordConversationPromptLog(convId, phase, payload) {
  const store = _ensureConvTrace(convId);
  if (!store) return;
  _pushTraceItem(store.promptLogs, {
    time: _traceNow(),
    phase: phase || 'prompt',
    payload: _traceText(payload),
  }, 200);
  _persistConvTrace(convId);
  if (typeof refreshConversationTraceWindow === 'function') refreshConversationTraceWindow();
}

function recordConversationWsLog(convId, direction, kind, payload) {
  const store = _ensureConvTrace(convId);
  if (!store) return;
  _pushTraceItem(store.wsLogs, {
    time: _traceNow(),
    direction: direction || 'in',
    kind: kind || 'ws',
    payload: _traceText(payload),
  }, 800);
  _persistConvTrace(convId);
  if (typeof refreshConversationTraceWindow === 'function') refreshConversationTraceWindow();
}

function getConversationTraceSnapshot(convId) {
  const store = _ensureConvTrace(convId) || { eventLogs: [], promptLogs: [], wsLogs: [] };
  return {
    convId: convId,
    eventLogs: store.eventLogs.slice(),
    promptLogs: store.promptLogs.slice(),
    wsLogs: store.wsLogs.slice(),
  };
}

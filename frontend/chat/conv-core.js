// ==================== Tab 栏管理 ====================


// ★ 功能区: AI 聊天 (chat/) | 对话切换/回调注册/历史消息加载重建

function switchToConv(convId) {
  // 入参:
  //   convId: 即将切换到的目标对话 ID。
  // 方法:
  //   切换前保留当前对话的地图快照, 切换后按目标对话的图层与地图视图恢复工作区状态。
  // 出参:
  //   无返回值; 成功时当前激活对话、工作区与地图状态完成同步切换。
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
  if (typeof _renderEventLogPanelForConv === 'function') {
    _renderEventLogPanelForConv(convId);
  }
  // ★ 同步侧栏高亮
  document.querySelectorAll('.conversation-card').forEach(el => el.classList.remove('active'));
  const sideItem = document.querySelector(`.conversation-card[data-conv-id="${convId}"]`);
  if (sideItem) sideItem.classList.add('active');
  scrollBottom();
}

// ★ 把当前预览面板的影像快照存到指定对话的 state.wmsImages
//   不读 DOM, 而是信任"加载时已记录到 state"的原则;
//   这里只做兜底: 若 state.wmsImages 与 DOM 不同步, 以 state 为准 (DOM 是渲染产物)
function _snapshotWmsPanelToConv(convId) {
  // 入参:
  //   convId: 当前即将离开的对话 ID。
  // 方法:
  //   图层快照在加载时已实时记录, 这里补做地图视图同步, 把用户离开前的中心点和缩放状态写回对话。
  // 出参:
  //   无返回值; 成功时该对话的 mapView 更新为最新视图状态。
  if (convId && activeConvId === convId && typeof _syncCurrentConvMapView === 'function') {
    _syncCurrentConvMapView();
  }
  // 实际的 wmsImages 在 showImageInPanel/showWmsInPanel 加载时已实时记录,
  // 此处无需额外操作。保留函数占位以便未来扩展 (如按 DOM 校验)。
}

// ★ 按指定对话的 state.wmsImages 重建预览面板 (OpenLayers 地图容器版)
//   清空地图业务图层 → 逐项重新加载 (走 showImageInPanel/showWmsInPanel)
//   重建是异步的 (图层要重新加载), 不阻塞对话切换
function _restoreWmsPanelFromConv(convId) {
  // 入参:
  //   convId: 需要恢复地图工作区内容的目标对话 ID。
  // 方法:
  //   先恢复该对话的地图视图, 再静默重建业务图层, 避免恢复过程中每个图层都把坐标系重新 fit 一遍。
  // 出参:
  //   无返回值; 成功时地图内容、视图状态与工作区页签都与目标对话保持一致。
  const st = convStates[convId];
  if (!st) return;
  // ★ 改造后: 不能清空 map 容器的 innerHTML (会破坏 OL), 改为清业务图层
  if (typeof _clearMapLayers === 'function') {
    _clearMapLayers();
  }
  closeWmsPopup();

  if (!st.wmsImages || st.wmsImages.length === 0) {
    // 该对话无影像 → 显示空提示
    if (typeof restoreMapViewForConv === 'function') restoreMapViewForConv(convId);
    showWmsEmpty();
    if (typeof _restoreWorkbenchTab === 'function') _restoreWorkbenchTab(convId);
    return;
  }

  if (typeof setMapRestoreMode === 'function') setMapRestoreMode(true);
  if (typeof restoreMapViewForConv === 'function') restoreMapViewForConv(convId);

  // 逐项重建 (不传 record=false 的内部调用, 避免重复 push 回同一 state)
  st.wmsImages.forEach(function(item) {
    if (item.type === 'wms') {
      showWmsInPanel(item.layerName, item.workspace, item.bbox, item.wmsUrl, item.caption, false, { forceFit: false });
    } else if (item.type === 'vector') {
      // ★ 矢量层 (geojson / shp) 恢复
      if (typeof showVectorFile === 'function') {
        showVectorFile(item.url, item.caption, false, { forceFit: false });
      }
    } else {
      // ★ overlay 字段: 掩膜叠加层透明度 0.5 (恢复时保持原状态)
      showImageInPanel(item.url, item.caption, item.legend, false, item.imagePath, item.artifactPath, item.overlay, { forceFit: false });
    }
  });
  if (typeof setMapRestoreMode === 'function') {
    setTimeout(function() { setMapRestoreMode(false); }, 0);
  }

  // ★ 触发地图尺寸重算 (地图固定显示后, 对话切换无需恢复工作区页签)
  if (typeof _restoreWorkbenchTab === 'function') _restoreWorkbenchTab(convId);
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
    if (typeof setConversationTraceSnapshot === 'function') {
      setConversationTraceSnapshot(convId, data.trace || null);
    }
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

  function normalizeThinkingText(value) {
    return (value || '').trim();
  }

  for (const m of messages) {
    const msgDbId = m.id;
    const nodeId = m.node_id || null;  // ★ v2.5 消息分支: 带 node_id
    const thinkingContent = normalizeThinkingText(m.thinking_content);
    const finalContent = normalizeThinkingText(m.content);
    const hasToolCalls = !!(m.tool_calls && m.tool_calls.length);
    if (m.role === 'human') {
      finishMessageForConv(convId);
      appendUserMessageTo(st, m.content, msgDbId, nodeId);
    } else if (m.role === 'ai') {
      if (thinkingContent) {
        ensureAssistantBubbleFor(st);
        appendThinkingTo(st, thinkingContent);
      }
      if (hasToolCalls) {
        ensureAssistantBubbleFor(st);
        m.tool_calls.forEach(tc => appendToolCallTo(st, tc.name || tc.get('name', ''), tc.args || tc.get('args', {})));
      }
      const shouldRenderFinalContent = finalContent && (!hasToolCalls || finalContent !== thinkingContent);
      if (shouldRenderFinalContent) {
        ensureAssistantBubbleFor(st);
        appendContentTo(st, m.content);
        finishMessageForConv(convId);
        // ★ v2.5 消息分支: 给 AI 消息容器挂 node_id (供后续分支交互)
        if (nodeId && st.container.lastElementChild) {
          st.container.lastElementChild.dataset.nodeId = nodeId;
        }
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

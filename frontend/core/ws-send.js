// ==================== 初始化 ====================

// ★ 功能区: 基础设施 (core/) | WS 初始化 + 消息发送 (initWsClient/sendMessage)

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
          const sideItem = document.querySelector(`.conversation-card[data-conv-id="${activeConvId}"]`);
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
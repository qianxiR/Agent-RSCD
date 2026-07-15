/**
 * WebSocket 聊天客户端 (v2.1 多对话并行)
 * - 入参: 消息回调函数集合
 * - 方法: 连接管理、消息发送、对话停止、前端工具执行结果回传、多对话路由
 * - 出参: 连接状态、发送接口
 *
 * ★ v2.1 多对话并行:
 *   同一 WebSocket 连接可同时运行多个对话。每条下行消息携带
 *   conversation_id, 前端据此路由到对应对话的回调集。
 *
 * 核心交互:
 *   1. 连接 WebSocket → ws://host/api/v1/agent/ws/chat
 *   2. 发送 {type: "chat_request", conversation_id, prompt: "..."} 触发 Agent 推理
 *   3. 接收 thinking/tool_call/frontend_action/content/done 消息 (均带 conversation_id)
 *   4. 收到 frontend_action 时 → 执行前端操作 → 回传 {type: "tool_result"}
 *   5. 发送 {type: "stop_chat", conversation_id} → 取消指定对话
 *   6. 发送 {type: "get_active_tasks"} → 查询后台运行的对话列表
 *   7. 收到 done 时 → 对话轮次结束
 */

// ★ 功能区: 基础设施 (core/) | WebSocket 传输层 (WsChatClient 类定义)

class WsChatClient {
  constructor({ onThinking, onToolCall, onFrontendAction, onContent, onError, onDone,
                onStatusChange, onSessionStart, onChatStopped, onBackgroundDone,
                onActiveTasksList }) {
    this.ws = null;
    this.sessionId = null;
    this.conversationId = null; // 当前活跃对话 id (向后兼容)
    this.callbacks = {
      onThinking, onToolCall, onFrontendAction, onContent, onError, onDone,
      onStatusChange, onSessionStart, onChatStopped, onBackgroundDone,
      onActiveTasksList,
    };
    this.reconnectTimer = null;

    // ★ v2.1: 多对话回调路由表 conversation_id → callbacks
    this.conversationCallbacks = new Map();
    // ★ v2.1: 后台对话未处理消息缓冲 (conversation_id → [])
    this.pendingMessages = new Map();
  }

  /** 建立 WebSocket 连接 */
  connect(url) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      return;
    }

    this.callbacks.onStatusChange?.('connecting');
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      console.log('[WS] Connected');
      this.callbacks.onStatusChange?.('connected');
    };

    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      const convId = data.conversation_id || this.conversationId || activeConvId || null;
      if (convId && typeof recordConversationWsLog === 'function') {
        recordConversationWsLog(convId, 'in', data.type || 'ws_message', data);
      }
      this._dispatch(data);
    };

    this.ws.onclose = () => {
      console.log('[WS] Disconnected');
      this.callbacks.onStatusChange?.('disconnected');
    };

    this.ws.onerror = (err) => {
      console.error('[WS] Error:', err);
      this.callbacks.onStatusChange?.('error');
    };
  }

  // ==================== 多对话回调管理 (v2.1) ====================

  /**
   * 注册指定对话的回调集。
   * - 注册后, 该对话的所有消息将路由到此回调集
   * - 如果该对话有缓冲的未处理消息, 立即回放
   */
  registerConversation(convId, callbacks) {
    this.conversationCallbacks.set(convId, callbacks);
    console.log(`[WS] Registered conversation: ${convId.slice(0, 8)}...`);

    // 回放缓冲消息
    const pending = this.pendingMessages.get(convId);
    if (pending && pending.length > 0) {
      console.log(`[WS] Replaying ${pending.length} pending messages for ${convId.slice(0, 8)}...`);
      for (const data of pending) {
        this._routeToCallbacks(convId, callbacks, data);
      }
      this.pendingMessages.delete(convId);
    }
  }

  /** 注销指定对话的回调集 */
  unregisterConversation(convId) {
    this.conversationCallbacks.delete(convId);
    console.log(`[WS] Unregistered conversation: ${convId.slice(0, 8)}...`);
  }

  /** 设置当前活跃对话 (向后兼容) */
  setActiveConversation(convId) {
    this.conversationId = convId;
  }

  // ==================== 消息发送 ====================

  /** 发送聊天消息 */
  sendChat({ prompt, model = 'qwen-plus', temperature = 0.7, conversation_id = null, project_id = null }) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.error('[WS] Not connected');
      return false;
    }

    const convId = conversation_id || this.conversationId || activeConvId || null;
    const payload = {
      type: 'chat_request',
      prompt,
      model,
      temperature,
      conversation_id,
      project_id,
      user_id: 'study_user',
      multi_round: true,
    };

    if (convId && typeof recordConversationPromptLog === 'function') {
      recordConversationPromptLog(convId, 'user_prompt', prompt);
      recordConversationPromptLog(convId, 'ai_request_payload', payload);
    }
    if (convId && typeof recordConversationWsLog === 'function') {
      recordConversationWsLog(convId, 'out', 'chat_request', payload);
    }

    this.ws.send(JSON.stringify(payload));

    return true;
  }

  /**
   * 停止对话 (v2.1: 支持指定 conversation_id)
   * - conversation_id: 指定则停止该对话, 不传则停止全部
   */
  sendStop(conversation_id = null) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.error('[WS] sendStop failed: WebSocket not open, state=' + (this.ws ? this.ws.readyState : 'null'));
      return false;
    }

    const payload = { type: 'stop_chat' };
    if (conversation_id) {
      payload.conversation_id = conversation_id;
    }
    const convId = conversation_id || this.conversationId || activeConvId || null;
    if (convId && typeof recordConversationWsLog === 'function') {
      recordConversationWsLog(convId, 'out', 'stop_chat', payload);
    }
    this.ws.send(JSON.stringify(payload));
    console.log(`[WS] Stop sent: conv=${conversation_id ? conversation_id.slice(0, 8) + '...' : 'ALL'}`);
    return true;
  }

  /** 回传前端工具执行结果 (解除后端 wait_for_frontend_result 阻塞) */
  sendToolResult({ request_id, status = 'success', data = {} }) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.error('[WS] Not connected, cannot send tool result');
      return;
    }

    const payload = {
      type: 'tool_result',
      request_id,
      status,
      data,
    };
    const convId = this.conversationId || activeConvId || null;
    if (convId && typeof recordConversationWsLog === 'function') {
      recordConversationWsLog(convId, 'out', 'tool_result', payload);
    }

    this.ws.send(JSON.stringify(payload));

    console.log(`[WS] Tool result sent: request_id=${request_id}, status=${status}`);
  }

  /** ★ v2.1 新增: 查询当前 session 下所有活跃对话 */
  getActiveTasks() {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.error('[WS] Not connected, cannot query active tasks');
      return;
    }
    this.ws.send(JSON.stringify({ type: 'get_active_tasks' }));
  }

  /** 断开连接 */
  disconnect() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  // ==================== 消息分发 (v2.1 多对话路由) ====================

  /** 消息分发 - 根据后端推送的 type + conversation_id 调用对应回调 */
  _dispatch(data) {
    const { type } = data;
    const convId = data.conversation_id;

    // ★ v2.1: 会话级消息 (不携带 conversation_id) → 使用默认回调
    if (!convId) {
      this._dispatchToDefault(data);
      return;
    }

    // ★ v2.1: 对话级消息 → 路由到注册的对话回调
    const callbacks = this.conversationCallbacks.get(convId);
    if (callbacks) {
      this._routeToCallbacks(convId, callbacks, data);
    } else {
      // 对话未注册 → 缓冲消息 (等待前端打开该对话 Tab)
      if (!this.pendingMessages.has(convId)) {
        this.pendingMessages.set(convId, []);
      }
      this.pendingMessages.get(convId).push(data);
      console.log(`[WS] Buffered message for unregistered conv: ${convId.slice(0, 8)}..., type=${type}`);

      // 通知默认回调有后台消息 (可用于 UI 提醒)
      if (type === 'done' || type === 'chat_stopped' || type === 'error') {
        this.callbacks.onBackgroundDone?.({
          conversation_id: convId,
          type: type,
        });
      }
    }
  }

  /** 会话级消息处理 (向后兼容) */
  _dispatchToDefault(data) {
    const { type } = data;

    switch (type) {
      case 'session_started':
        this.conversationId = data.conversation_id;
        this.callbacks.onSessionStart?.(data.conversation_id);
        break;

      case 'active_tasks_list':
        this.callbacks.onActiveTasksList?.(data.conversations || [], data.count || 0);
        break;

      case 'ping':
        this.ws?.send(JSON.stringify({ type: 'pong' }));
        break;

      case 'chat_stopped':
        this.callbacks.onChatStopped?.(data);
        break;

      default:
        console.warn('[WS] Unknown session-level message type:', type, data);
    }
  }

  /** 将消息路由到指定对话的回调集 */
  _routeToCallbacks(convId, callbacks, data) {
    const { type } = data;

    switch (type) {
      case 'session_started':
        this.conversationId = convId;
        this.callbacks.onSessionStart?.(convId);
        break;

      case 'thinking':
        callbacks.onThinking?.(data.content);
        break;

      case 'tool_call':
        callbacks.onToolCall?.(data.tool_calls);
        break;

      case 'frontend_action':
        callbacks.onFrontendAction?.({
          eventType: data.event_type,
          eventData: data.event_data,
          description: data.description,
          requestId: data.request_id,
        });
        break;

      case 'content':
        callbacks.onContent?.(data.content);
        break;

      case 'error':
        callbacks.onError?.(data.content);
        break;

      case 'done':
        callbacks.onDone?.();
        break;

      case 'chat_stopped':
        callbacks.onChatStopped?.(data);
        break;

      case 'background_done':
        callbacks.onBackgroundDone?.(data);
        break;

      case 'memory_updated':
        callbacks.onMemoryUpdated?.(data);
        break;

      default:
        console.warn('[WS] Unknown message type:', type, data);
    }
  }
}

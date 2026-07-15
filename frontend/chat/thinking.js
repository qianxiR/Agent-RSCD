// ==================== Thinking 日志聚合 ====================

// ★ 功能区: AI 聊天 (chat/) | Thinking 日志聚合 + frontend_action 派发

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
  addEventLog('in', `thinking (${st.thinkingTokenCount} tokens)`, st.thinkingBuffer);
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
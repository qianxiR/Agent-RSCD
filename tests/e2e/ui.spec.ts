import { expect, test } from '@playwright/test';
import { writeAuditRecord } from './helpers/audit';

test('前端主界面加载核心功能入口', async ({ page }) => {
  // 入参: Playwright page fixture 使用真实浏览器访问 FastAPI 静态前端。
  // 方法: 检查标题、项目区、工作区、聊天区、快捷功能按钮和 WS 客户端状态对象。
  // 出参: 断言通过表示首屏与关键入口可用，并保存 UI 审计记录。
  const errors: string[] = [];
  page.on('pageerror', err => errors.push(err.message));
  page.on('console', msg => {
    if (msg.type() === 'error') errors.push(msg.text());
  });

  await page.goto('/');
  await expect(page.locator('h1')).toContainText('国土智察');
  await expect(page.locator('#convSidebar')).toBeVisible();
  await expect(page.locator('#wmsPanel')).toBeVisible();
  await expect(page.locator('#chatArea')).toBeVisible();
  await expect(page.locator('#inputBox')).toBeVisible();
  await expect(page.locator('.quick-question')).toHaveCount(4);
  expect(await page.locator('.demo-btn').count()).toBeGreaterThanOrEqual(24);
  await expect(page.locator('.demo-btn', { hasText: '显示影像' })).toBeVisible();
  await expect(page.locator('.demo-btn', { hasText: '分割分析' })).toBeVisible();
  await expect(page.locator('.demo-btn', { hasText: '变化检测' })).toBeVisible();
  await expect(page.locator('.demo-btn', { hasText: '监测报告' })).toBeVisible();
  await expect(page.locator('#wmsEmptyHint')).toContainText('尚未加载影像');

  const clientReady = await page.waitForFunction(() => typeof wsClient !== 'undefined' && Boolean(wsClient), null, { timeout: 8_000 });
  expect(clientReady).toBeTruthy();

  const featureSnapshot = await page.evaluate(() => ({
    title: document.title,
    quickQuestions: Array.from(document.querySelectorAll('.quick-question')).map(item => item.textContent?.trim()),
    demoButtons: Array.from(document.querySelectorAll('.demo-btn')).map(item => item.textContent?.trim()).filter(Boolean),
    wsClientReady: typeof wsClient !== 'undefined' && Boolean(wsClient),
  }));

  await writeAuditRecord({
    name: 'ui-shell',
    status: 'passed',
    steps: ['page:open', 'layout:assert', 'shortcuts:assert', 'ws-client:ready'],
    evidence: { featureSnapshot, errors },
  });

  const actionableErrors = errors.filter(text =>
    !text.includes('favicon') &&
    !text.includes('ERR_CONNECTION_CLOSED') &&
    !text.includes('Failed to load resource')
  );
  expect(actionableErrors.slice(0, 3)).toEqual([]);
});

test('前端会话 trace 能记录提示词与 WebSocket 消息', async ({ page }) => {
  // 入参: Playwright page fixture 访问真实前端，但不触发 LLM 长任务。
  // 方法: 在浏览器上下文创建对话状态并调用 trace 记录函数，验证 prompt/ws/event 三类轨迹可导出。
  // 出参: 断言通过表示前端具备可复盘日志能力，并保存 trace 快照。
  await page.goto('/');
  await page.waitForFunction(() => Boolean((window as any).getConvState), null, { timeout: 8_000 });

  const trace = await page.evaluate(() => {
    const convId = `e2e_trace_${Date.now()}`;
    (window as any).getConvState(convId);
    (window as any).recordConversationEventLog(convId, 'out', 'e2e_start', '端到端验证开始');
    (window as any).recordConversationPromptLog(convId, 'user_prompt', '列出 GeoServer 所有服务');
    (window as any).recordConversationWsLog(convId, 'out', 'chat_request', { type: 'chat_request', prompt: '列出 GeoServer 所有服务' });
    (window as any).recordConversationWsLog(convId, 'in', 'active_tasks_list', { type: 'active_tasks_list', conversations: [] });
    return (window as any).getConversationTraceSnapshot(convId);
  });

  expect(trace.eventLogs).toHaveLength(1);
  expect(trace.promptLogs).toHaveLength(1);
  expect(trace.wsLogs).toHaveLength(2);

  await writeAuditRecord({
    name: 'ui-trace-store',
    status: 'passed',
    steps: ['trace:create', 'trace:event', 'trace:prompt', 'trace:websocket'],
    evidence: trace,
  });
});

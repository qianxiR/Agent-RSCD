import { expect, test } from '@playwright/test';
import { writeAuditRecord } from './helpers/audit';

type WsMessage = {
  type?: string;
  conversations?: string[];
  count?: number;
};

test('WebSocket 会话控制协议可查询活跃任务', async ({ page, baseURL }) => {
  // 入参: page 用于在浏览器环境打开原生 WebSocket，baseURL 来自 Playwright 配置。
  // 方法: 连接 /api/v1/agent/ws/chat 并发送 get_active_tasks，收集协议消息。
  // 出参: 收到 active_tasks_list 即表示 WS 双向控制链路可用，并写入审计证据。
  const messages = await page.evaluate(async url => {
    // 入参: url 是 HTTP baseURL，必须能转换为 ws/wsS 协议。
    // 方法: 在页面上下文中创建 WebSocket，等待 open 后发送 get_active_tasks。
    // 出参: 返回收到的消息数组，超时前未收到目标消息也会返回已有消息。
    const wsUrl = url.replace(/^http/, 'ws') + '/api/v1/agent/ws/chat';
    const events: WsMessage[] = [];
    const ws = new WebSocket(wsUrl);

    await new Promise<void>((resolve, reject) => {
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error('WebSocket open failed'));
    });

    ws.send(JSON.stringify({ type: 'get_active_tasks' }));

    await new Promise<void>(resolve => {
      const timer = window.setTimeout(resolve, 5_000);
      ws.onmessage = event => {
        const data = JSON.parse(event.data);
        events.push(data);
        if (data.type === 'active_tasks_list') {
          window.clearTimeout(timer);
          resolve();
        }
      };
    });

    ws.close();
    return events;
  }, baseURL!);

  const activeTasks = messages.find(item => item.type === 'active_tasks_list');
  expect(activeTasks).toBeTruthy();
  expect(activeTasks).toHaveProperty('conversations');

  await writeAuditRecord({
    name: 'websocket-control',
    status: 'passed',
    steps: ['ws:connect', 'ws:get_active_tasks', 'ws:active_tasks_list'],
    evidence: { messages },
  });
});

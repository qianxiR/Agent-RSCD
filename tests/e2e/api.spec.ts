import { expect, test } from '@playwright/test';
import { writeAuditRecord } from './helpers/audit';

test('REST 基础能力可达并返回结构化数据', async ({ request }) => {
  // 入参: Playwright request fixture 绑定到 baseURL，要求后端服务已启动。
  // 方法: 依次验证项目、会话、任务、工作区 API 的 HTTP 状态与关键 JSON 字段。
  // 出参: 断言通过表示 REST 骨架可用，并写入审计 JSON 作为验证记录。
  const steps: string[] = [];
  const projects = await request.get('/api/v1/projects?user_id=e2e_user');
  expect(projects.ok()).toBeTruthy();
  const projectData = await projects.json();
  expect(projectData).toHaveProperty('projects');
  steps.push('projects:list');

  const conversations = await request.get('/api/v1/conversations?user_id=e2e_user');
  expect(conversations.ok()).toBeTruthy();
  const conversationData = await conversations.json();
  expect(conversationData).toHaveProperty('conversations');
  steps.push('conversations:list');

  const tasks = await request.get('/api/v1/tasks?limit=5');
  expect(tasks.ok()).toBeTruthy();
  const taskData = await tasks.json();
  expect(taskData).toHaveProperty('tasks');
  steps.push('tasks:list');

  const workspace = await request.get(`/api/v1/workspace/list?roots=${encodeURIComponent(process.cwd())}&depth=1`);
  expect(workspace.ok()).toBeTruthy();
  const workspaceData = await workspace.json();
  expect(workspaceData).toHaveProperty('roots');
  steps.push('workspace:list');

  await writeAuditRecord({
    name: 'api-foundation',
    status: 'passed',
    steps,
    evidence: {
      projectCount: projectData.projects.length,
      conversationCount: conversationData.conversations.length,
      taskCount: taskData.tasks.length,
      workspaceRootCount: workspaceData.roots.length,
    },
  });
});

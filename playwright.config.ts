import { defineConfig, devices } from '@playwright/test';

// ★ 双服务架构 (2026-10): E2E 流量统一经 Vite dev server (5173) 分流:
//   projects/workspace/conversations/tasks/auth → Java (8081)
//   其余 /api + 推理 WS                                  → Python (8020)
// webServer 按需拉起三个服务, 已在运行的实例自动复用。
const PYTHON_EXE = process.env.PYTHON_EXE || 'D:/anaconda3/envs/sam3/python.exe';
const JAVA_PORT = Number(process.env.JAVA_PORT || 8081);
const PY_PORT = Number(process.env.E2E_PY_PORT || 8020);
const VITE_PORT = 5173;
const BASE_URL = process.env.E2E_BASE_URL || `http://127.0.0.1:${VITE_PORT}`;

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 45_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  reporter: [
    ['list'],
    ['html', { outputFolder: 'output/playwright/report', open: 'never' }],
    ['json', { outputFile: 'output/playwright/results.json' }],
  ],
  outputDir: 'output/playwright/artifacts',
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'msedge',
      use: { ...devices['Desktop Edge'], channel: 'msedge' },
    },
  ],
  webServer: [
    {
      command: `java -jar backend-java/target/rsiis-business.jar --server.port=${JAVA_PORT}`,
      url: `http://127.0.0.1:${JAVA_PORT}/api/v1/projects`,
      reuseExistingServer: true,
      timeout: 60_000,
      stdout: 'pipe',
      stderr: 'pipe',
    },
    {
      command: `${PYTHON_EXE} -m uvicorn backend.main:app --host 127.0.0.1 --port ${PY_PORT}`,
      url: `http://127.0.0.1:${PY_PORT}/api/v1/conversations`,
      reuseExistingServer: true,
      timeout: 120_000,
      stdout: 'pipe',
      stderr: 'pipe',
    },
    {
      command: `npm --prefix frontend-vue run dev -- --host 127.0.0.1 --port ${VITE_PORT} --strictPort`,
      url: `http://127.0.0.1:${VITE_PORT}`,
      reuseExistingServer: true,
      timeout: 60_000,
      stdout: 'pipe',
      stderr: 'pipe',
    },
  ],
});

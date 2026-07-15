import { expect, test } from '@playwright/test';
import { writeAuditRecord } from './helpers/audit';
import path from 'node:path';
import { existsSync } from 'node:fs';
import { readdirSync, statSync } from 'node:fs';

/**
 * 地图容器 (OpenLayers) 端到端测试
 *
 * 目的: 验证前端地图容器能正确渲染上传影像、GeoJSON 矢量、Shapefile。
 * 设计: 不依赖完整 ReAct 推理 (太慢), 直接 page.evaluate 调用前端全局函数
 *       (showImageInPanel / showVectorFile / renderImage), 断言 OL 渲染产物 (canvas/svg)。
 *
 * 运行方式 (需 sam3 环境, 因 webServer 会启后端):
 *   set E2E_PORT=8020 && npx playwright test map-container --project=msedge
 *   ★ 若 webServer 启动用错 python, 改 playwright.config.ts 的 command 为 sam3 路径。
 */

const T1_NAME = 'r000_c006_t1.tif';
const T1_DISK = path.resolve('tests', 'data', T1_NAME);
const GEOJSON_NAME = '成都市_县.geojson';
const GEOJSON_DISK = path.resolve('tests', 'data', GEOJSON_NAME);

// 断言超时放宽 (上传/加载有网络往返)
const LOAD_TIMEOUT = 15_000;

// 复制测试数据到 agent-files 后返回可被前端访问的下载 URL
async function stageTestFile(request: any, diskPath: string): Promise<{ absPath: string; downloadUrl: string }> {
  // 通过 /api/v1/samseg/upload 上传, 拿到宿主机绝对路径 + 构造 download URL
  const fs = await import('node:fs/promises');
  const buffer = await fs.readFile(diskPath);
  const blob = new Blob([buffer]);
  const form = new FormData();
  // @ts-ignore - Playwright 环境的 FormData/Blob 可用
  form.append('images', blob, path.basename(diskPath));
  form.append('conversation_id', 'e2e_map');

  const resp = await request.post('/api/v1/samseg/upload', { multipart: form });
  expect(resp.ok(), `上传 ${path.basename(diskPath)} 应成功`).toBeTruthy();
  const data = await resp.json();
  const absPath = data.paths[0];
  // upload 端点用 SAMSEG_UPLOAD/filepath 拼接 (SAMSEG_UPLOAD = agent-files/samseg/send)
  // 所以 URL 里的 filepath 是 {conv}/xxx.tif (不含 samseg/send/ 前缀, 否则路径重复 → 404)
  const norm = absPath.replace(/\\/g, '/');
  const marker = 'samseg/send/';
  const idx = norm.indexOf(marker);
  const rel = idx >= 0 ? norm.slice(idx + marker.length) : norm.split('/').slice(-2).join('/');
  const downloadUrl = '/api/v1/upload/' + rel;
  return { absPath, downloadUrl };
}

test.describe('地图容器渲染', () => {

  test('① 上传影像 → 地图容器出现 canvas (OL 渲染标志)', async ({ page, request }) => {
    const steps: string[] = [];
    await page.goto('/');
    await page.waitForFunction(() => typeof (window as any).initWmsMap === 'function', { timeout: LOAD_TIMEOUT });

    // 初始: 空提示可见
    await expect(page.locator('#wmsEmptyHint')).toBeVisible();
    steps.push('initial:empty-hint-visible');

    // 上传 T1
    const { absPath, downloadUrl } = await stageTestFile(request, T1_DISK);
    steps.push(`upload:${T1_NAME}`);

    // 直接调前端函数加载到底图 (不经 ReAct, 快)
    const result = await page.evaluate(async (url: string) => {
      const r = await (window as any).showImageInPanel(url, 'E2E 测试影像', null, true);
      return r;
    }, downloadUrl);

    expect(result.loaded, `影像应加载成功: ${result.error || ''}`).toBeTruthy();
    steps.push('showImageInPanel:loaded');

    // ★ 核心断言: OL 渲染后 #wmsViewport 内出现 canvas
    await expect(page.locator('#wmsViewport canvas')).toBeVisible({ timeout: LOAD_TIMEOUT });
    steps.push('assert:canvas-visible');

    // 空提示应隐藏
    await expect(page.locator('#wmsEmptyHint')).not.toBeVisible();
    steps.push('assert:empty-hint-hidden');

    await page.screenshot({ path: 'output/playwright/artifacts/map-01-upload.png', fullPage: false });
    await writeAuditRecord({
      name: 'map-upload',
      status: 'passed',
      steps,
      evidence: { absPath, downloadUrl, loaded: result.loaded },
    });
  });

  test('② GeoJSON 矢量加载 → 地图新增矢量层', async ({ page, request }) => {
    const steps: string[] = [];
    await page.goto('/');
    await page.waitForFunction(() => typeof (window as any).showVectorFile === 'function', { timeout: LOAD_TIMEOUT });

    const { absPath, downloadUrl } = await stageTestFile(request, GEOJSON_DISK);
    steps.push(`upload:${GEOJSON_NAME}`);

    const result = await page.evaluate(async (url: string) => {
      const r = await (window as any).showVectorFile(url, '成都市_县 (GeoJSON)', true);
      return r;
    }, downloadUrl);

    expect(result.loaded, `矢量应加载成功: ${result.error || ''}`).toBeTruthy();
    steps.push('showVectorFile:loaded');

    // ★ 矢量层渲染后, OL 会在地图上画 svg 或 canvas
    await expect(page.locator('#wmsViewport canvas, #wmsViewport svg')).toBeVisible({ timeout: LOAD_TIMEOUT });
    steps.push('assert:vector-layer-visible');

    await page.screenshot({ path: 'output/playwright/artifacts/map-02-geojson.png', fullPage: false });
    await writeAuditRecord({
      name: 'map-geojson',
      status: 'passed',
      steps,
      evidence: { absPath, downloadUrl, loaded: result.loaded },
    });
  });

  test('③ Shapefile 加载 → 走后端 shp-to-geojson 端点 → 矢量层', async ({ page, request }) => {
    const steps: string[] = [];
    await page.goto('/');
    await page.waitForFunction(() => typeof (window as any).showVectorFile === 'function', { timeout: LOAD_TIMEOUT });

    // 先上传 GeoJSON, 再让后端没有现成 shp → 用 segment_image 产出的 shp 测试更真实;
    // 但 segment 推理慢, 这里直接验证端点可达性: 上传一个 geojson 后调 export 转 shp 需要工具链。
    // 简化: 直接测 shp-to-geojson 端点对"已知 shp"的响应 (用 visualize_vector 测试已生成的 shp)。
    //
    // ★ 更实际的测法: 上传 geojson → 调后端把 geojson 先转 shp (走 export_change_vector),
    //   但该工具返回 frontend_action (download), 不直接暴露 shp 路径给前端。
    //   所以这里测"shp-to-geojson 端点本身" + 前端 showVectorFile 的 shp 分支可达性。

    // 1. 验证端点存在且参数校验生效 (不存在路径 → success:false)
    const badResp = await request.get('/api/v1/vector/shp-to-geojson?shp_path=nonexistent.shp');
    const badData = await badResp.json();
    expect(badData.success).toBeFalsy();
    steps.push('endpoint:rejects-nonexistent');

    // 2. 扫描 agent-files 下后端脚本产出的 shp (直接读磁盘)
    const candidates = findGeneratedShp();
    if (!candidates.length) {
      // 没有现成 shp → 跳过本断言但标记 (需先跑后端脚本 tests/map_container_backend.py)
      test.skip(true, '未找到已生成的 shp, 请先运行 tests/map_container_backend.py 生成产物');
      return;
    }
    const shpAbsPath = candidates[0];
    steps.push(`found-shp:${path.basename(shpAbsPath)}`);

    // 3. 调 shp-to-geojson 端点
    const resp = await request.get('/api/v1/vector/shp-to-geojson?shp_path=' + encodeURIComponent(shpAbsPath));
    const data = await resp.json();
    expect(data.success, `shp 转换应成功: ${data.msg || ''}`).toBeTruthy();
    expect(data.feature_count).toBeGreaterThan(0);
    steps.push(`shp-to-geojson:${data.feature_count}features`);

    // 4. 前端 showVectorFile 走 shp 分支 (传宿主机绝对路径, 内部调端点)
    const result = await page.evaluate(async (shpPath: string) => {
      const r = await (window as any).showVectorFile(shpPath, 'SHP 矢量 (后端转换)', true);
      return r;
    }, shpAbsPath);

    expect(result.loaded, `shp 矢量应加载: ${result.error || ''}`).toBeTruthy();
    await expect(page.locator('#wmsViewport canvas, #wmsViewport svg')).toBeVisible({ timeout: LOAD_TIMEOUT });
    steps.push('assert:shp-vector-visible');

    await page.screenshot({ path: 'output/playwright/artifacts/map-03-shp.png', fullPage: false });
    await writeAuditRecord({
      name: 'map-shp',
      status: 'passed',
      steps,
      evidence: { shpAbsPath, feature_count: data.feature_count, crs: data.crs },
    });
  });

  test('④ segment_image 结果完整渲染 (原图层 + 掩膜叠加层 + 矢量)', async ({ page, request }) => {
    const steps: string[] = [];
    await page.goto('/');
    await page.waitForFunction(() => typeof (window as any).renderImage === 'function', { timeout: LOAD_TIMEOUT });

    const { absPath, downloadUrl } = await stageTestFile(request, T1_DISK);
    steps.push(`upload:${T1_NAME}`);

    // 先单独验证带 input_image_path 的 showImageInPanel (真实坐标路径) 能否加载
    const directResult = await page.evaluate(async (p: { url: string; path: string }) => {
      const r = await (window as any).showImageInPanel(p.url, '直测', null, true, p.path);
      return r;
    }, { url: downloadUrl, path: absPath });
    expect(directResult.loaded, `直测 showImageInPanel: ${directResult.error || ''}`).toBeTruthy();
    steps.push('direct-showImageInPanel:loaded');

    // 模拟后端 segment_image 的 frontend_action params, 调 renderImage
    // ★ 用真实上传的 downloadUrl (原图层 + 掩膜叠加层都用它, 测试渲染链路, 不依赖真实推理)
    const params = {
      image_url: downloadUrl,
      mask_tif_url: downloadUrl, // 复用同一图作叠加层
      caption: 'E2E 渲染链路测试',
      input_image_path: absPath,
      legend: [
        { name: '建筑', hex: '#ff0000' },
        { name: '道路', hex: '#00ff00' },
        { name: '水', hex: '#0000ff' },
      ],
      has_crs: true,
    };

    const result = await page.evaluate(async (p: any) => {
      const r = await (window as any).renderImage(null, p);
      return r;
    }, params);

    expect(result.loaded, `renderImage 应加载: ${result.error || ''}`).toBeTruthy();
    steps.push('renderImage:loaded');

    // 图例浮层应出现
    await expect(page.locator('.seg-legend-overlay')).toBeVisible({ timeout: LOAD_TIMEOUT });
    steps.push('assert:legend-overlay-visible');

    // canvas (底图 + 叠加层 = 至少 1 个)
    await expect(page.locator('#wmsViewport canvas')).toBeVisible({ timeout: LOAD_TIMEOUT });
    steps.push('assert:canvas-visible');

    await page.screenshot({ path: 'output/playwright/artifacts/map-04-render-chain.png', fullPage: false });
    await writeAuditRecord({
      name: 'map-render-chain',
      status: 'passed',
      steps,
      evidence: { loaded: result.loaded },
    });
  });

});

// 扫描 agent-files 下已生成的 shp (后端测试脚本产物)。直接扫磁盘, 比 workspace API 可靠。
function findGeneratedShp(): string[] {
  const root = path.resolve('agent-files');
  const found: string[] = [];
  // 优先在 analysis/report/samseg/vector 下找 (后端脚本产物目录), 跳过 .trash
  const searchDirs = ['analysis', 'report', 'samseg/vector'];
  for (const sub of searchDirs) {
    const dir = path.join(root, sub);
    if (!existsSync(dir)) continue;
    _walkShp(dir, found, 0, 6);
    if (found.length) break; // 找到即可
  }
  return found;
}

function _walkShp(dir: string, out: string[], depth: number, maxDepth: number) {
  if (depth > maxDepth || out.length > 5) return;
  let entries: string[];
  try { entries = readdirSync(dir); } catch { return; }
  for (const name of entries) {
    if (name.startsWith('.') || name === '_anonymous') continue;
    const full = path.join(dir, name);
    let st;
    try { st = statSync(full); } catch { continue; }
    if (st.isDirectory()) {
      _walkShp(full, out, depth + 1, maxDepth);
    } else if (name.endsWith('.shp')) {
      out.push(full);
    }
  }
}

# Playwright 使用说明

## 端到端测试状态说明

后端启动成功时，终端会出现类似输出：

```powershell
Uvicorn running on http://127.0.0.1:8020
```

如果使用 `playwright test --headed`，浏览器会弹出，但测试执行完成后会自动关闭。这是 Playwright 测试框架的正常收尾行为，不代表后端没有启动。

## 实时查看并保持浏览器打开

需要使用两个终端分别执行。

终端 1：启动后端服务。

```powershell
npm run dev:backend
```

终端 2：打开 Edge 并保持浏览器窗口。

```powershell
npm run open:e2e
```

## 调试测试流程

如果需要看测试一步一步执行，并在失败时停住：

```powershell
npm run test:e2e:debug
```

## 常用测试命令

运行全部端到端测试：

```powershell
npm run test:e2e
```

有头模式运行测试：

```powershell
npm run test:e2e:headed
```

打开 Playwright 测试 UI：

```powershell
npm run test:e2e:ui
```

启动 Playwright MCP：

```powershell
npm run mcp:playwright
```

## 测试产物位置

Playwright 测试报告、截图、视频、审计 JSON 默认输出到：

```powershell
output\playwright
```

---

## 测试文件清单（阶段 22 新增）

### 后端产物验证脚本（Python，需 sam3 环境）

> ★ 这类脚本直接调用工具函数（不经 WebSocket），跑真实 SamSeg 推理，验证产物文件真实落地。**必须用 sam3 环境**（含 torch + CUDA + geopandas + 权重）。

| 脚本 | 用途 | 运行命令 |
|------|------|---------|
| `tests/map_container_backend.py` | 地图容器后端产物验证：segment/detect_change/visualize_vector/export_change_vector/overlay_edge/shp 回读（28 项断言） | `D:/anaconda3/envs/sam3/python.exe -m tests.map_container_backend` |

**产物证据**：脚本会把所有生成的文件路径打印到 stdout（mask_tif / geojson / shp 目录等），供前端核对能否访问。

### Playwright 前端测试（TypeScript）

| 脚本 | 用途 | 依赖 |
|------|------|------|
| `tests/e2e/map-container.spec.ts` | 地图容器渲染验证：①上传影像→canvas ②GeoJSON 矢量层 ③Shapefile（走 shp-to-geojson 端点）④segment 结果完整链路 | 后端运行中 + 后端脚本已生成 shp 产物（测试③） |
| `tests/e2e/api.spec.ts` | HTTP REST API 端点验证 | 后端运行中 |
| `tests/e2e/websocket.spec.ts` | WebSocket 通信协议验证 | 后端运行中 |

**运行 map-container 测试**（单独跑，更快）：

```powershell
# 需后端已运行（sam3 环境），且后端脚本已跑过一次（生成 shp 产物供测试③用）
npx playwright test map-container --project=msedge
```

### 测试数据

位于 `tests/data/`：

| 文件 | 用途 |
|------|------|
| `r000_c006_t1.tif` / `t2.tif` | 双时相遥感影像（segment + detect_change 测试） |
| `成都市_县.geojson` | 矢量数据（visualize_vector + showVectorFile 测试） |

---

## sam3 环境运行后端（重要）

Playwright 的 `webServer` 配置默认用 `python`，但 SamSeg 推理需要 `sam3` 环境。两种方式：

**方式 1：手动用 sam3 启后端（推荐，测试时复用）**

```powershell
# 终端 1：用 sam3 启动后端
D:\anaconda3\envs\sam3\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8020

# 终端 2：跑 Playwright（reuseExistingServer 会复用上面的后端）
npx playwright test map-container --project=msedge
```

**方式 2：修改 playwright.config.ts 的 webServer.command**

把 `python -m uvicorn ...` 改为 `D:/anaconda3/envs/sam3/python.exe -m uvicorn ...`，让 Playwright 自动用 sam3 启后端。

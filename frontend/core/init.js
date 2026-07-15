// ==================== 启动 ====================
// ★ 功能区: 基础设施 (core/) | 启动编排 (最后加载, 调用所有初始化函数)

// ★ OpenLayers 地图容器初始化 (最先: 上传/分析结果需要地图已就绪)
initWmsMap();
initWsClient();
restoreActiveFolder();
loadConvListImmediate();
initResizers();
initCollapsibles();
initTextareaAutoResize();
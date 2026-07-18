// ==================== REST 服务层 ====================
// ★ 对应原生散落在各文件的 fetch 调用，统一收敛
//   开发期走 Vite proxy（相对路径），生产期前后端同源，路径不变

const DEFAULT_USER = 'study_user'

// 入参: url, options(fetch 配置)
// 方法: 统一 fetch 封装，非 2xx 抛错
// 出参: 解析后的 JSON
async function request(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${url}`)
  }
  return res.json()
}

// 拉取对话列表（可选按 project_id 过滤）
export function getConversations({ projectId = null } = {}) {
  const qs = projectId
    ? `?user_id=${DEFAULT_USER}&project_id=${projectId}`
    : `?user_id=${DEFAULT_USER}`
  return request(`/api/v1/conversations${qs}`)
}

// 拉取项目分组列表
export function getProjects() {
  return request(`/api/v1/projects?user_id=${DEFAULT_USER}`)
}

// 拉取指定对话的历史消息 + trace
// 出参: { messages: [...], trace: {...} }
export function getConvMessages(convId) {
  return request(`/api/v1/conversations/${convId}/messages`)
}

// ==================== 对话 CRUD ====================

// 修改对话标题或所属项目
// body: { title?, project_id? }
export function patchConversation(convId, body) {
  return request(`/api/v1/conversations/${convId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

// 删除对话
export function deleteConversation(convId) {
  return request(`/api/v1/conversations/${convId}`, { method: 'DELETE' })
}

// ==================== 项目分组 CRUD ====================

// 新建项目分组
// body: { name, description?, folder_path? }
export function createProject(body) {
  return request(`/api/v1/projects?user_id=${DEFAULT_USER}`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// 重命名项目
export function patchProject(projectId, body) {
  return request(`/api/v1/projects/${projectId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

// 删除项目（组内会话自动回未分组）
export function deleteProject(projectId) {
  return request(`/api/v1/projects/${projectId}`, { method: 'DELETE' })
}

// ==================== GeoServer 图层管理 ====================

// 拉取工作空间树（含图层 + 类型）
// 出参: { status, workspaces: [{workspace, layers_typed:[{name, full_name, type}]}] }
export function getGeoServerServices() {
  return request('/api/v1/geoserver/services')
}

// 拉取指定图层范围
// 出参: { status, bbox: {minx, miny, maxx, maxy} }
export function getLayerBbox(workspace, layerName) {
  return request(`/api/v1/geoserver/layers/${encodeURIComponent(workspace)}/${encodeURIComponent(layerName)}/bbox`)
}

// 删除图层（从 GeoServer 永久移除）
export function deleteGeoServerLayer(workspace, layerName) {
  return request(`/api/v1/geoserver/layers/${encodeURIComponent(workspace)}/${encodeURIComponent(layerName)}`, {
    method: 'DELETE',
  })
}

// 发布图层 (工作空间由后端按类型固定: raster→cd_raster, shp→cd_shp)
// body: { file_path, layer_name, layer_type } (workspace 已废弃, 后端忽略)
export function publishGeoServerLayer(body) {
  return request('/api/v1/geoserver/layers', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// 删除整个工作空间（含其下所有图层）
export function deleteWorkspace(workspace) {
  return request(`/api/v1/geoserver/workspaces/${encodeURIComponent(workspace)}`, {
    method: 'DELETE',
  })
}

// ==================== 影像上传 + 元数据 ====================

// 上传待分割影像到服务器（落盘 samseg/send/{conv}/ + CRS 校验 + 登记元数据）
// 入参: files File 数组, conversationId 可选（归属对话）
// 方法: FormData multipart 上传，绕开 request()（它硬编码 JSON header，multipart 需浏览器自动加 boundary）
// 出参: { status, paths:[绝对路径...], count } —— paths 为宿主机绝对路径，供 agent 分割引用
export async function uploadSamsegImages(files, conversationId = null) {
  const form = new FormData()
  files.forEach((f) => form.append('images', f))
  if (conversationId) form.append('conversation_id', conversationId)
  const res = await fetch('/api/v1/samseg/upload', { method: 'POST', body: form })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`上传失败 (HTTP ${res.status}): ${txt}`)
  }
  return res.json()
}

// 查询影像地理元数据（即"验证工具"：确认已上传 + 有 CRS + 取 bbox/尺寸）
// 入参: path 绝对路径或相对 agent-files/ 路径
// 出参: { has_crs, crs?, bbox?:[minx,miny,maxx,maxy], width?, height?, driver?, msg? }
export function getImageMeta(path) {
  return request(`/api/v1/image/meta?path=${encodeURIComponent(path)}`)
}

// ==================== 对话分支 (fork / regenerate) ====================

// 从指定消息节点分叉 + 回显新 prompt（后端只 fork，LLM 由前端走 WS chat_request）
// 入参: convId, { parent_node_id?(分叉点), replace_node_id?(被替换节点), prompt, model? }
// 出参: { status, leaf_node_id, prompt, message } 或 { status:'error', msg }
export function regenerateConversation(
  convId,
  { parent_node_id = null, replace_node_id = null, prompt, model = null },
) {
  const body = { prompt }
  if (parent_node_id) body.parent_node_id = parent_node_id
  if (replace_node_id) body.replace_node_id = replace_node_id
  if (model) body.model = model
  return request(`/api/v1/conversations/${convId}/regenerate`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// 查询会话分支统计（判断是否有被 fork 隐藏的旧分支）
// 出参: { status, active_messages, hidden_messages, has_branches }
export function getConvBranches(convId) {
  return request(`/api/v1/conversations/${convId}/branches`)
}

// ==================== AI 任务查询（进度轮询用）====================

// 拉取任务列表（简表，不含 input/output 大字段）
// 入参: { conversation_id?, status?, limit? }
// 出参: { status, count, tasks:[{id,conversation_id,task_type,tool_name,status,progress,error,started_at,finished_at,created_at}] }
export function getTasks({ conversation_id = null, status = null, limit = 50 } = {}) {
  const qs = new URLSearchParams()
  if (conversation_id) qs.set('conversation_id', conversation_id)
  if (status) qs.set('status', status)
  qs.set('limit', limit)
  return request(`/api/v1/tasks?${qs}`)
}

// 单任务详情（含完整 input/output/error）
// 出参: { status, task:{id,conversation_id,task_type,tool_name,status,input,output,progress,error,started_at,finished_at,created_at} }
export function getTaskDetail(taskId) {
  return request(`/api/v1/tasks/${taskId}`)
}

// 任务执行日志（按 id 正序，含 level/elapsed_ms）
// 出参: { status, count, logs:[{id,level,message,elapsed_ms,created_at}] }
export function getTaskLogs(taskId, limit = 200) {
  return request(`/api/v1/tasks/${taskId}/logs?limit=${limit}`)
}

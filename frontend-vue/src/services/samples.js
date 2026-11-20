// ==================== 样本制作 API ====================
// 走 Python 服务 /api/v1/samples/*（Vite proxy 已把 /api 转发到 8020）。

export const SAMPLE_CLASSES = [
  { id: 1, name: 'building', color: [230, 25, 75] },
  { id: 2, name: 'road', color: [245, 130, 48] },
  { id: 3, name: 'water', color: [0, 130, 200] },
  { id: 4, name: 'bareland', color: [170, 110, 40] },
  { id: 5, name: 'vegetation', color: [60, 180, 75] },
  { id: 6, name: 'farmland', color: [0, 128, 0] },
]

// 自定义类别按顺序取色，与后端 CUSTOM_CLASS_COLORS 保持一致
export const CUSTOM_CLASS_COLORS = [
  [148, 103, 189], [227, 119, 194], [188, 189, 34],
  [23, 190, 207], [174, 199, 232], [255, 187, 120],
]

async function request(url, options = {}) {
  const response = await fetch(url, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok || data.status === 'error') {
    throw new Error(data.msg || data.detail || `请求失败 (${response.status})`)
  }
  return data
}

// 入参: { project, task_dir, annotations, source_images }
// 出参: { status, path, instance_count, task_dir }
export function saveSampleAnnotations(body) {
  return request('/api/v1/samples/annotations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// 入参: { project, taskDir }
// 出参: { status, annotations, phase_previews }
export function loadSampleAnnotations({ project, taskDir }) {
  const query = new URLSearchParams({ project, task_dir: taskDir })
  return request(`/api/v1/samples/annotations?${query.toString()}`)
}

// 入参: project
// 出参: { status, tasks:[{name,instance_count,class_count,saved_at,width,height,phase_dates,restorable}] }
export function listSampleTasks(project) {
  const query = new URLSearchParams({ project })
  return request(`/api/v1/samples/tasks?${query.toString()}`)
}

// 入参: { project, taskDir, format: 'coco'|'shp' }
// 出参: 浏览器可直接下载的 URL
export function sampleExportUrl({ project, taskDir, format }) {
  const query = new URLSearchParams({ project, task_dir: taskDir, format })
  return `/api/v1/samples/export?${query.toString()}`
}

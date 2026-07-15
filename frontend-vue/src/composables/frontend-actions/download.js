// ==================== handler: download ====================
// ★ 对应原生 renderDownload (wms-render.js:318)
//   触发浏览器文件下载; wait_for_result=False, 统一回传

// 入参: data = { url|download_url, filename|layer_name, file_type, file_size }
//      ctx 未使用(纯 DOM 下载)
// 方法: 构造隐藏 <a download> 点击触发下载; 无 url 时返回 downloading 状态(对齐原生)
// 出参: { status: 'success'|'downloading', download_url?, filename }
export async function handleDownload(data, ctx) {
  const url = data.url || data.download_url
  const filename = data.filename || `${data.layer_name || 'download'}.${data.file_type || 'tif'}`
  if (!url) {
    return { status: 'downloading', filename }
  }
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  setTimeout(() => document.body.removeChild(a), 100)
  return { status: 'success', download_url: url, filename }
}

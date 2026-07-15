// ==================== handler: chat_image (沙盒图片 → 聊天窗口) ====================
// ★ 沙盒代码生成的静态 PNG 直接渲染进聊天消息流
//   (区别于 render_image 走右侧地图/GeoServer 面板, 那条要 base_layer)。
//   data = { image_url, caption }; 图片累积到当前进行中 AI 消息,
//   finishAssistant 归档后由 MessageBubble 的 el-image(preview-src-list) 渲染 + 点击放大。

// 入参: data = { image_url, caption }, ctx.convId / ctx.store (useConversationStore)
// 方法: 调 store.appendChatImage 把图片推入进行中消息的 pendingImages
// 出参: { status: 'rendered', image_url } (wait_for_result=False, 仅回执不阻塞)
export function handleChatImage(data, ctx) {
  const store = ctx?.store
  const convId = ctx?.convId
  if (!store || !convId) {
    return { status: 'error', msg: '缺少会话上下文, 无法渲染聊天图片' }
  }
  const url = data?.image_url || ''
  const caption = data?.caption || ''
  if (!url) {
    return { status: 'error', msg: 'chat_image 缺少 image_url' }
  }
  store.appendChatImage(convId, { url, caption })
  return { status: 'rendered', image_url: url }
}

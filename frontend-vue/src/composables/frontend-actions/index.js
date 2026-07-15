// ==================== frontend_action 事件注册表 ====================
// ★ { event_type → handler } 纯映射。handler 之间零耦合。
//   新增事件类型: 新建 handler 文件 + 此处一行注册, 分发器/WS 层无需改动。

import { handleLayerControl } from './layerControl'
import { handleLayerGroupControl } from './layerGroupControl'
import { handleLayerDisplay } from './layerDisplay'
import { handleLayerLocate } from './layerLocate'
import { handleRenderImage } from './renderImage'
import { handleChatImage } from './chatImage'
import { handleDownload } from './download'
import { handleSkipVisual } from './skipVisual'

export const frontendActionHandlers = {
  layer_control: handleLayerControl,
  layer_group_control: handleLayerGroupControl,
  layer_display: handleLayerDisplay,
  layer_locate: handleLayerLocate,
  render_image: handleRenderImage,
  chat_image: handleChatImage,
  download: handleDownload,
  // 三类纯视觉渲染共用静默策略(结果由 AI 文字承载)
  render_table: handleSkipVisual,
  render_chart: handleSkipVisual,
  ui_panel_control: handleSkipVisual,
}

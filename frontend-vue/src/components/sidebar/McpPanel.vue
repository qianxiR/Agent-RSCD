<script setup>
// ==================== MCP 工具管理面板 ====================
// 数据源: Python /api/v1/mcp/servers (mcp_server 配置表)。
// 功能: 服务器配置的添加/删除/启用开关。
// ★ 首期定位: 配置管理 — MCP 协议客户端接入推理链路为后续工程,
//   面板状态列展示「已配置」, 不做连接探测。
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ChatLineRound, Delete, Plus, Refresh, Connection } from '@element-plus/icons-vue'
import { getMcpServers, addMcpServer, toggleMcpServer, deleteMcpServer } from '@/services/api'

const emit = defineEmits(['back'])
const servers = ref([])
const loading = ref(false)
const addVisible = ref(false)
const addSubmitting = ref(false)
const form = reactive({ name: '', transport: 'sse', url: '', command: '', description: '' })

onMounted(() => load())

async function load() {
  loading.value = true
  try {
    const data = await getMcpServers()
    if (data.status !== 'success') throw new Error(data.msg || '加载失败')
    servers.value = data.servers || []
  } catch (e) {
    ElMessage.error('MCP 服务器加载失败: ' + e.message)
  } finally {
    loading.value = false
  }
}

function openAdd() {
  Object.assign(form, { name: '', transport: 'sse', url: '', command: '', description: '' })
  addVisible.value = true
}

// 入参: 无（读取 form）
// 方法: 校验必填 → addMcpServer → 成功关闭并刷新
// 出参: 无
async function submitAdd() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写服务器名称')
    return
  }
  if (form.transport === 'sse' && !form.url.trim()) {
    ElMessage.warning('sse 传输必须填写 URL')
    return
  }
  if (form.transport === 'stdio' && !form.command.trim()) {
    ElMessage.warning('stdio 传输必须填写启动命令')
    return
  }
  addSubmitting.value = true
  try {
    const data = await addMcpServer({
      name: form.name.trim(),
      transport: form.transport,
      url: form.transport === 'sse' ? form.url.trim() : '',
      command: form.transport === 'stdio' ? form.command.trim() : '',
      description: form.description.trim(),
    })
    if (data.status !== 'success') throw new Error(data.msg || '后端返回失败')
    addVisible.value = false
    await load()
    ElMessage.success('MCP 服务器已添加')
  } catch (e) {
    ElMessage.error('添加失败: ' + e.message)
  } finally {
    addSubmitting.value = false
  }
}

async function onToggle(server) {
  try {
    const data = await toggleMcpServer(server.id, !server.enabled)
    if (data.status !== 'success') throw new Error(data.msg || '后端返回失败')
    server.enabled = !server.enabled
  } catch (e) {
    ElMessage.error('状态切换失败: ' + e.message)
  }
}

async function onDelete(server) {
  await ElMessageBox.confirm(`删除 MCP 服务器「${server.name}」？`, '删除', {
    type: 'warning',
    confirmButtonText: '确认删除',
    cancelButtonText: '取消',
  })
  try {
    const data = await deleteMcpServer(server.id)
    if (data.status !== 'success') throw new Error(data.msg || '后端返回失败')
    await load()
    ElMessage.success('已删除')
  } catch (e) {
    ElMessage.error('删除失败: ' + e.message)
  }
}
</script>

<template>
  <div class="mcp-panel">
    <div class="panel-actions">
      <button type="button" class="panel-action primary" @click="openAdd">
        <el-icon><Plus /></el-icon>
        <span>添加服务器</span>
      </button>
      <button type="button" class="panel-action" @click="load">
        <el-icon><Refresh /></el-icon>
        <span>刷新</span>
      </button>
      <button type="button" class="panel-action" @click="emit('back')">
        <el-icon><ChatLineRound /></el-icon>
        <span>对话管理</span>
      </button>
    </div>

    <div class="panel-list">
      <div class="panel-section-header">MCP 服务器</div>
      <div v-if="loading && !servers.length" class="panel-empty">加载中…</div>
      <div v-else-if="!servers.length" class="panel-empty">暂无 MCP 服务器，点击「添加服务器」</div>

      <div v-for="s in servers" :key="s.id" class="mcp-card" :class="{ disabled: !s.enabled }">
        <div class="mcp-card-head">
          <el-icon class="mcp-card-icon"><Connection /></el-icon>
          <span class="mcp-card-title">{{ s.name }}</span>
          <el-switch :model-value="s.enabled" size="small" @change="onToggle(s)" />
        </div>
        <div class="mcp-card-meta">
          <span class="mcp-tag">{{ s.transport.toUpperCase() }}</span>
          <span class="mcp-endpoint">{{ s.transport === 'sse' ? s.url : s.command }}</span>
        </div>
        <div v-if="s.description" class="mcp-card-desc">{{ s.description }}</div>
        <div class="mcp-card-foot">
          <span class="mcp-status" :class="s.enabled ? 'on' : 'off'">
            {{ s.enabled ? '已配置' : '已停用' }}
          </span>
          <el-icon class="mcp-del" title="删除" @click="onDelete(s)"><Delete /></el-icon>
        </div>
      </div>

      <div class="panel-hint">
        MCP (Model Context Protocol) 服务器为 Agent 提供外部工具源。当前版本支持
        配置管理；协议客户端接入推理链路后，已启用的服务器将自动挂载其工具。
      </div>
    </div>

    <el-dialog v-model="addVisible" title="添加 MCP 服务器" width="400px" append-to-body>
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如: 高德地图 MCP" />
        </el-form-item>
        <el-form-item label="传输类型">
          <el-radio-group v-model="form.transport">
            <el-radio value="sse">SSE (远程)</el-radio>
            <el-radio value="stdio">STDIO (本地)</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.transport === 'sse'" label="URL" required>
          <el-input v-model="form.url" placeholder="https://example.com/sse" />
        </el-form-item>
        <el-form-item v-else label="启动命令" required>
          <el-input v-model="form.command" placeholder="如: npx -y @example/mcp-server" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addVisible = false">取消</el-button>
        <el-button type="primary" :loading="addSubmitting" @click="submitAdd">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.mcp-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-panel);
  overflow: hidden;
}
.panel-actions {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 14px 8px 12px;
  flex-shrink: 0;
}
.panel-action {
  width: 100%;
  height: 32px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 8px;
  border: none;
  border-radius: var(--r-md);
  background: transparent;
  color: #2f3b49;
  cursor: pointer;
  font-size: 13px;
  text-align: left;
}
.panel-action:hover {
  background: var(--bg-hover);
}
.panel-action.primary {
  background: var(--accent);
  color: #fff;
}
.panel-action.primary:hover {
  background: var(--accent-hover);
}
.panel-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 10px 10px;
}
.panel-section-header {
  font-size: var(--fs-md);
  color: var(--text-muted);
  padding: 4px 6px 8px;
}
.panel-empty {
  text-align: center;
  font-size: var(--fs-md);
  color: var(--text-muted);
  padding: 32px 0;
}
.mcp-card {
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: 10px 12px;
  margin-bottom: 8px;
  background: var(--bg-base);
}
.mcp-card.disabled {
  opacity: 0.65;
}
.mcp-card-head {
  display: flex;
  align-items: center;
  gap: 6px;
}
.mcp-card-icon {
  color: var(--accent);
  flex-shrink: 0;
}
.mcp-card-title {
  flex: 1;
  min-width: 0;
  font-size: var(--fs-lg);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mcp-card-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  min-width: 0;
}
.mcp-tag {
  flex-shrink: 0;
  font-size: var(--fs-xs);
  font-family: var(--mono);
  color: var(--accent);
  background: var(--accent-soft);
  padding: 1px 5px;
  border-radius: var(--r-sm);
}
.mcp-endpoint {
  font-family: var(--mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mcp-card-desc {
  margin-top: 6px;
  font-size: var(--fs-md);
  color: var(--text-secondary);
  line-height: 1.5;
}
.mcp-card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
}
.mcp-status {
  font-size: var(--fs-xs);
}
.mcp-status.on {
  color: var(--accent-green);
}
.mcp-status.off {
  color: var(--text-muted);
}
.mcp-del {
  color: var(--text-muted);
  cursor: pointer;
}
.mcp-del:hover {
  color: var(--accent-red);
}
.panel-hint {
  margin-top: 10px;
  padding: 8px 10px;
  border-radius: var(--r-md);
  background: var(--bg-inset);
  color: var(--text-muted);
  font-size: var(--fs-sm);
  line-height: 1.6;
}
</style>

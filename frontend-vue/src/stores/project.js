import { defineStore } from 'pinia'
import { getConversations, getProjects } from '@/services/api'

// ==================== 项目/会话列表状态层 ====================
// ★ 对应原生 _convListCache + _groupsCache + activeProjectId
//   收敛会话摘要列表、项目分组、激活项目、搜索/折叠状态

export const useProjectStore = defineStore('project', {
  state: () => ({
    convList: [],          // 会话摘要 [{id, project_id, title, updated_at, status, message_count}]
    projects: [],          // 项目分组 [{id, name, folder_path}]
    activeProjectId: null, // 当前激活项目（仅影响新会话归属 + 高亮）
    searchKeyword: '',     // 搜索关键词
    collapsedGroups: [],   // 折叠的分组 id 列表（替代原生 localStorage Set）
    layerListVersion: 0,   // 图层列表版本号（bump 触发 LayerPanel 自动刷新，跨组件通信）
  }),

  getters: {
    // 按搜索词过滤后的会话
    filtered(state) {
      const kw = state.searchKeyword.trim().toLowerCase()
      if (!kw) return state.convList
      return state.convList.filter(
        (c) => (c.title || '').toLowerCase().includes(kw) || c.id.includes(kw),
      )
    },

    // 按 project_id 分组：{ byProject: {pid: [...]}, ungrouped: [...] }
    grouped(state) {
      const byProject = {}
      const ungrouped = []
      this.filtered.forEach((c) => {
        if (c.project_id) {
          if (!byProject[c.project_id]) byProject[c.project_id] = []
          byProject[c.project_id].push(c)
        } else {
          ungrouped.push(c)
        }
      })
      return { byProject, ungrouped }
    },

    isGroupCollapsed(state) {
      return (gid) => state.collapsedGroups.includes(gid)
    },
  },

  actions: {
    // 并发拉取会话列表 + 项目分组（对应原生 loadConvListImmediate）
    async loadAll() {
      const [convRes, groupRes] = await Promise.all([
        getConversations(),
        getProjects(),
      ])
      this.convList = convRes.conversations || []
      this.projects = groupRes.projects || []
    },

    setActiveProject(id) {
      this.activeProjectId = id
    },

    setSearch(kw) {
      this.searchKeyword = kw
    },

    toggleGroup(gid) {
      const idx = this.collapsedGroups.indexOf(gid)
      if (idx >= 0) this.collapsedGroups.splice(idx, 1)
      else this.collapsedGroups.push(gid)
    },

    // 从摘要列表移除（删除对话后调用）
    removeConv(id) {
      const idx = this.convList.findIndex((c) => c.id === id)
      if (idx >= 0) this.convList.splice(idx, 1)
    },

    // 新增或更新摘要（新建对话/收到 done 后同步）
    upsertConv(conv) {
      const idx = this.convList.findIndex((c) => c.id === conv.id)
      if (idx >= 0) this.convList[idx] = { ...this.convList[idx], ...conv }
      else this.convList.unshift(conv)
    },
    // 递增图层列表版本号 → 触发 LayerPanel watch 自动刷新（发布/删除图层后调用）
    bumpLayerVersion() {
      this.layerListVersion++
    },
  },
})
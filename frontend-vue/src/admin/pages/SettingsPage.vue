<!-- 管理端 · 系统设置（P0 只读展示，编辑与审计在 P1 开放） -->
<script setup>
import Icon from '@/shared/ui/Icon.vue'
import PageHeader from '@/shared/ui/PageHeader.vue'
import { useAdminAuthStore } from '@/shared/auth/authStore'

const auth = useAdminAuthStore()

const SECURITY = [
  { label: '口令策略', value: '最短 12 位，BCrypt 存储', state: '已生效' },
  { label: '登录失败锁定', value: 'P1：连续失败 5 次锁定 15 分钟', state: '待接入' },
  { label: '双因子认证', value: '管理端强制 TOTP（首次登录绑定）', state: '已生效' },
  { label: '会话时长', value: 'access 30 分钟 · refresh 14 天（旋转 + 重放检测）', state: '已生效' },
  { label: 'IP 白名单', value: 'P1：按管理员/办公网段限制', state: '待接入' },
  { label: '审计', value: '管理动作全量留痕，只读不可改', state: '已生效' },
]

const SERVICES = [
  { name: 'Java 业务服务', value: '127.0.0.1:8081 · /api/v1/**', note: '认证 / 项目 / 用户 / 权限 / 审计' },
  { name: 'Python AI 服务', value: '127.0.0.1:8020 · /api/v1/** · /internal/**', note: '会话 / 任务 / 解译 / 图层发布' },
  { name: 'GeoServer', value: '127.0.0.1:8080 · /geoserver/**', note: '工作空间与图层服务' },
  { name: 'PostgreSQL', value: 'rsiis_business / cd / Agent_study', note: '业务 / 变化检测 / 会话记忆' },
]
</script>

<template>
  <div class="page">
    <PageHeader title="系统设置" subtitle="安全策略、组织与服务配置。所有变更需填写原因并写审计（P1）。">
      <template #actions>
        <span class="badge badge-gray">只读</span>
        <button class="btn btn-primary" disabled title="设置编辑与原因审计在 P1 开放">保存设置</button>
      </template>
    </PageHeader>

    <p class="alert alert-info" style="margin-bottom: 14px">
      <Icon name="alert" :size="14" />
      <span>
        P0 阶段设置项由 <code class="mono">application.yml</code> 环境变量控制，页面先做只读展示；P1 接入可编辑配置与「保存 + 原因 + 审计」流程。
      </span>
    </p>

    <div class="grid-2">
      <section class="card">
        <div class="card-hd"><span class="card-title">安全策略</span></div>
        <div class="card-bd flush">
          <table class="table" style="min-width: 0">
            <tbody>
              <tr v-for="s in SECURITY" :key="s.label">
                <td style="width: 34%" class="cell-strong">{{ s.label }}</td>
                <td class="small secondary">{{ s.value }}</td>
                <td style="width: 84px">
                  <span class="badge" :class="s.state === '已生效' ? 'badge-green' : 'badge-gray'">
                    {{ s.state }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <div class="stack">
        <section class="card">
          <div class="card-hd"><span class="card-title">组织</span></div>
          <div class="card-bd">
            <dl class="kv">
              <dt>模式</dt>
              <dd>单组织（多租户字段 <code class="mono">org_id</code> 已预留）</dd>
              <dt>组织 ID</dt>
              <dd class="mono small">{{ auth.user?.org_id || 'org_default' }}</dd>
              <dt>当前账号</dt>
              <dd>{{ auth.displayName }} · {{ (auth.roles || []).join(' / ') }}</dd>
              <dt>MFA</dt>
              <dd>
                <span class="badge" :class="auth.user?.mfa_enabled ? 'badge-green' : 'badge-amber'">
                  {{ auth.user?.mfa_enabled ? '已绑定' : '未绑定' }}
                </span>
              </dd>
            </dl>
          </div>
        </section>

        <section class="card">
          <div class="card-hd"><span class="card-title">部署形态</span></div>
          <div class="card-bd">
            <dl class="kv">
              <dt>入口</dt>
              <dd class="small">app.example.com（现有工作台） / admin.example.com（管理端）</dd>
              <dt>构建</dt>
              <dd class="small">同一 Vite 工程双入口：index.html / admin.html</dd>
              <dt>当前环境</dt>
              <dd><span class="env-badge">生产环境</span></dd>
            </dl>
          </div>
        </section>
      </div>
    </div>

    <section class="card">
      <div class="card-hd"><span class="card-title">服务</span></div>
      <div class="card-bd flush">
        <table class="table" style="min-width: 0">
          <thead>
            <tr>
              <th style="width: 20%">服务</th>
              <th style="width: 42%">地址</th>
              <th>职责</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in SERVICES" :key="s.name">
              <td class="cell-strong">{{ s.name }}</td>
              <td class="mono small">{{ s.value }}</td>
              <td class="small secondary">{{ s.note }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.stack .card + .card {
  margin-top: 0;
}
</style>

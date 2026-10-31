<!-- ==================== 共享登录 / MFA 页面 ====================
     用户端与管理端共用，通过 portal 区分文案与安全提示：
       user  → 核心工作台
       admin → 管理控制台（强制 TOTP，首次登录先绑定）
     三段式：login → setup（首次绑定）/ verify（已有 MFA）
     边界: setup_token / mfa_token 只存在 store 内存；本组件不落盘任何密钥。
-->
<script setup>
import { computed, nextTick, ref } from 'vue'
import Icon from '@/shared/ui/Icon.vue'

const props = defineProps({
  portal: { type: String, default: 'user' }, // user | admin
  store: { type: Object, required: true },
})
const emit = defineEmits(['authenticated'])

const isAdmin = computed(() => props.portal === 'admin')
const productName = '国土智察'
const portalName = computed(() => (isAdmin.value ? '管理控制台' : '核心工作台'))

const step = ref('login') // login | setup | verify
const username = ref('')
const password = ref('')
const passwordVisible = ref(false)
const code = ref('')
const loading = ref(false)
const error = ref('')

const setup = ref(null) // { secret, otpauth_uri, recovery_codes }
const savedCodes = ref(false)
const copied = ref('')
const showUri = ref(false)
const codeInput = ref(null)

// ---------- 工具 ----------
async function copyText(text, key) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    // 兜底：非 HTTPS 环境下 clipboard 不可用
    const el = document.createElement('textarea')
    el.value = text
    el.style.position = 'fixed'
    el.style.opacity = '0'
    document.body.appendChild(el)
    el.select()
    document.execCommand('copy')
    document.body.removeChild(el)
  }
  copied.value = key
  setTimeout(() => {
    if (copied.value === key) copied.value = ''
  }, 1600)
}

function resetToLogin(message = '') {
  step.value = 'login'
  code.value = ''
  setup.value = null
  savedCodes.value = false
  error.value = message
  password.value = ''
}

async function focusCode() {
  await nextTick()
  codeInput.value && codeInput.value.focus()
}

// ---------- 1. 口令登录 ----------
async function submitLogin() {
  if (loading.value) return
  error.value = ''
  if (!username.value.trim()) {
    error.value = '请输入用户名'
    return
  }
  if (!password.value) {
    error.value = '请输入口令'
    return
  }
  loading.value = true
  try {
    const status = await props.store.login(username.value.trim(), password.value)
    if (status === 'mfa_setup_required') {
      await enterSetup()
    } else if (status === 'mfa_required') {
      step.value = 'verify'
      code.value = ''
      await focusCode()
    } else {
      emit('authenticated')
    }
  } catch (e) {
    error.value = e.message || '登录失败'
  } finally {
    loading.value = false
  }
}

// ---------- 2. 首次绑定 MFA ----------
async function enterSetup() {
  loading.value = true
  try {
    // 先拿到密钥再切步骤，避免模板在 setup 为空时渲染
    const data = await props.store.startMfaSetup()
    setup.value = data
    step.value = 'setup'
    await focusCode()
  } catch (e) {
    resetToLogin(e.message || '无法开始 MFA 绑定，请重新登录')
  } finally {
    loading.value = false
  }
}

async function submitSetup() {
  if (loading.value) return
  error.value = ''
  if (!savedCodes.value) {
    error.value = '请先保存恢复码，再完成绑定'
    return
  }
  if (!/^\d{6}$/.test(code.value.trim())) {
    error.value = '请输入认证器中的 6 位数字验证码'
    return
  }
  loading.value = true
  try {
    await props.store.confirmMfaSetup(code.value.trim())
    emit('authenticated')
  } catch (e) {
    error.value = e.message || '验证码校验失败'
    code.value = ''
    await focusCode()
  } finally {
    loading.value = false
  }
}

// ---------- 3. 已有 MFA 校验 ----------
async function submitVerify() {
  if (loading.value) return
  error.value = ''
  const value = code.value.trim().replace(/\s/g, '')
  if (value.length < 6) {
    error.value = '请输入 6 位动态码或恢复码'
    return
  }
  loading.value = true
  try {
    await props.store.verifyMfa(value)
    emit('authenticated')
  } catch (e) {
    error.value = e.message || '验证码不正确'
    code.value = ''
    await focusCode()
  } finally {
    loading.value = false
  }
}

// 6 位纯数字时自动提交，减少一次点击
function onCodeInput() {
  const value = code.value.trim()
  if (step.value === 'verify' && /^\d{6}$/.test(value) && !loading.value) {
    submitVerify()
  }
}

function switchAccount() {
  resetToLogin()
}
</script>

<template>
  <div class="auth" :class="{ 'auth--admin': isAdmin }">
    <!-- 左：品牌区 -->
    <aside class="auth-brand">
      <div class="auth-brand-top">
        <img src="/image.svg" alt="国土智察" class="brand-logo" />
        <div>
          <p class="auth-product">{{ productName }}</p>
        </div>
      </div>

      <div class="auth-brand-main">
        <h2 class="auth-slogan">
          {{ isAdmin ? '统一治理人、项目、数据与服务' : '从影像到结论，一站完成' }}
        </h2>
        <p class="auth-desc">
          {{
            isAdmin
              ? '管理控制台汇聚账号权限、项目资产、图层服务、任务队列与审计日志，所有管理动作留痕可追溯。'
              : '上传影像、发起解译、查看变化图斑、导出报告，项目内的数据与产物集中管理。'
          }}
        </p>
        <ul class="auth-points">
          <li v-for="point in isAdmin
            ? ['强制 TOTP 双因子认证', '最小权限与角色矩阵', '关键操作写入审计日志']
            : ['变化检测 / 语义分割', '图层叠加与地图核查', '任务队列与产物归档']" :key="point">
            <Icon name="check" :size="14" />
            <span>{{ point }}</span>
          </li>
        </ul>
      </div>

      <div class="auth-brand-foot">
        <span>自然资源监测中心</span>
        <span class="auth-dot"></span>
        <span>内部系统 · 请勿外传</span>
      </div>
    </aside>

    <!-- 右：表单区 -->
    <main class="auth-panel">
      <div class="auth-card">
        <div class="auth-head">
          <div class="auth-head-title">
            <h1>{{ step === 'login' ? '登录' : step === 'setup' ? '绑定双因子认证' : '双因子校验' }}</h1>
            <span v-if="isAdmin" class="env-badge">生产环境</span>
          </div>
        </div>

        <!-- 管理员安全提示 -->
        <div v-if="isAdmin && step === 'login'" class="auth-notice">
          <Icon name="lock" :size="13" />
          <span>管理端已强制启用 TOTP 双因子认证，首次登录需先绑定认证器。</span>
        </div>

        <p v-if="error" class="alert alert-error auth-error">
          <Icon name="alert" :size="14" />
          <span>{{ error }}</span>
        </p>

        <!-- ========== 登录 ========== -->
        <form v-if="step === 'login'" class="auth-form" @submit.prevent="submitLogin">
          <div class="field">
            <label for="auth-username">用户名</label>
            <input
              id="auth-username"
              v-model="username"
              class="input auth-input"
              autocomplete="username"
              placeholder="请输入用户名"
              :disabled="loading"
            />
          </div>

          <div class="field">
            <label for="auth-password">口令</label>
            <div class="auth-input-wrap">
              <input
                id="auth-password"
                v-model="password"
                class="input auth-input"
                :type="passwordVisible ? 'text' : 'password'"
                autocomplete="current-password"
                placeholder="请输入登录口令"
                :disabled="loading"
              />
              <button
                type="button"
                class="auth-eye"
                :aria-label="passwordVisible ? '隐藏口令' : '显示口令'"
                @click="passwordVisible = !passwordVisible"
              >
                <Icon name="eye" :size="15" />
              </button>
            </div>
          </div>

          <div class="auth-row">
            <span class="auth-help">登录状态保留 14 天（可随时退出）</span>
            <span class="auth-help">忘记口令请联系管理员</span>
          </div>

          <button class="btn btn-primary btn-lg btn-block" type="submit" :disabled="loading">
            {{ loading ? '登录中…' : '登录' }}
          </button>

          <p class="auth-foot">
            <template v-if="isAdmin">
              非管理员账号无法通过此入口访问管理控制台。
            </template>
            <template v-else>
              账号由管理员统一开通，登录即代表同意《数据安全使用规范》。
            </template>
          </p>
        </form>

        <!-- ========== 首次绑定 ========== -->
        <div v-else-if="step === 'setup' && setup" class="auth-form">
          <ol class="auth-steps">
            <li>用认证器 App（如 Microsoft Authenticator）扫描或手动录入密钥</li>
            <li>输入 App 显示的 6 位验证码完成绑定</li>
            <li>恢复码只显示一次，请离线保存</li>
          </ol>

          <div class="auth-secret">
            <div class="auth-secret-head">
              <span>密钥（手动录入）</span>
              <button class="link-btn" type="button" @click="copyText(setup.secret, 'secret')">
                {{ copied === 'secret' ? '已复制' : '复制' }}
              </button>
            </div>
            <code class="auth-secret-value">{{ setup.secret }}</code>
            <div class="auth-secret-head" style="margin-top: 8px">
              <span>otpauth 链接</span>
              <button class="link-btn" type="button" @click="showUri = !showUri">
                {{ showUri ? '收起' : '展开' }}
              </button>
            </div>
            <code v-if="showUri" class="auth-secret-uri">{{ setup.otpauth_uri }}</code>
          </div>

          <div class="auth-recovery">
            <div class="auth-secret-head">
              <span>恢复码（{{ (setup.recovery_codes || []).length }} 个，一次性使用）</span>
              <button
                class="link-btn"
                type="button"
                @click="copyText((setup.recovery_codes || []).join('\n'), 'codes')"
              >
                {{ copied === 'codes' ? '已复制' : '复制全部' }}
              </button>
            </div>
            <div class="auth-codes">
              <code v-for="rc in setup.recovery_codes" :key="rc">{{ rc }}</code>
            </div>
          </div>

          <label class="checkbox auth-saved">
            <input v-model="savedCodes" type="checkbox" />
            <span>我已将恢复码保存到安全位置（丢失后需管理员重置）</span>
          </label>

          <form @submit.prevent="submitSetup">
            <div class="field">
              <label for="auth-setup-code">认证器验证码</label>
              <input
                id="auth-setup-code"
                ref="codeInput"
                v-model="code"
                class="input auth-code"
                inputmode="numeric"
                autocomplete="one-time-code"
                maxlength="6"
                placeholder="000000"
                :disabled="loading"
              />
            </div>
            <button class="btn btn-primary btn-lg btn-block" type="submit" :disabled="loading">
              {{ loading ? '校验中…' : '完成绑定并登录' }}
            </button>
          </form>
          <button class="btn btn-ghost btn-block" type="button" @click="switchAccount">
            更换账号
          </button>
        </div>

        <!-- ========== 已有 MFA ========== -->
        <div v-else class="auth-form">
          <div class="auth-account">
            <span class="avatar">{{ (username || 'A').slice(0, 1).toUpperCase() }}</span>
            <div>
              <strong>{{ username || '当前账号' }}</strong>
              <p class="hint">已绑定 TOTP，请输入认证器中的动态码</p>
            </div>
          </div>

          <form @submit.prevent="submitVerify">
            <div class="field">
              <label for="auth-verify-code">动态码</label>
              <input
                id="auth-verify-code"
                ref="codeInput"
                v-model="code"
                class="input auth-code"
                inputmode="numeric"
                autocomplete="one-time-code"
                maxlength="10"
                placeholder="000000"
                :disabled="loading"
                @input="onCodeInput"
              />
              <p class="hint">6 位数字动态码；也可输入 10 位恢复码。</p>
            </div>
            <button class="btn btn-primary btn-lg btn-block" type="submit" :disabled="loading">
              {{ loading ? '校验中…' : '验证并进入' }}
            </button>
          </form>
          <button class="btn btn-ghost btn-block" type="button" @click="switchAccount">
            返回登录 / 更换账号
          </button>
        </div>

        <p class="auth-version">
          {{ productName }} {{ portalName }} · v0.1 开发预览
        </p>
      </div>
    </main>
  </div>
</template>

<style scoped>
.auth {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(430px, 1fr);
  min-height: 100vh;
  background: var(--bg-panel);
}

/* ---------- 品牌区 ---------- */
.auth-brand {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 30px;
  padding: 34px 42px;
  overflow: hidden;
  background:
    radial-gradient(760px 420px at 8% 0%, rgba(77, 107, 254, 0.14), transparent 62%),
    radial-gradient(560px 380px at 92% 100%, rgba(106, 134, 255, 0.16), transparent 60%),
    linear-gradient(160deg, #fafbff 0%, #f2f4ff 52%, #eef1ff 100%);
  border-right: 1px solid var(--border);
}
.auth-brand::after {
  /* 网格纹理，弱化留白 */
  content: '';
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(77, 107, 254, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(77, 107, 254, 0.05) 1px, transparent 1px);
  background-size: 44px 44px;
  mask-image: radial-gradient(120% 90% at 30% 20%, #000 30%, transparent 78%);
  pointer-events: none;
}
.auth-brand > * {
  position: relative;
  z-index: 1;
}
.auth-brand-top {
  display: flex;
  align-items: center;
  gap: 10px;
}
.brand-logo {
  width: 34px;
  height: 34px;
  object-fit: contain;
  display: block;
  flex: 0 0 auto;
}
.auth-product {
  font-size: 16px;
  font-weight: 650;
  letter-spacing: 0.4px;
}
.auth-product-sub {
  margin-top: 2px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  letter-spacing: 0.6px;
}
.auth-brand-main {
  max-width: 460px;
}
.auth-slogan {
  font-size: 30px;
  line-height: 1.3;
  font-weight: 700;
  letter-spacing: -0.6px;
  color: #1a1a1a;
}
.auth-desc {
  margin-top: 12px;
  font-size: var(--fs-lg);
  line-height: 1.85;
  color: var(--text-secondary);
}
.auth-points {
  list-style: none;
  margin-top: 22px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.auth-points li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--fs-lg);
  color: var(--text-secondary);
}
.auth-points li svg {
  color: var(--accent);
  flex: 0 0 auto;
}
.auth-brand-foot {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: var(--fs-sm);
  color: var(--text-muted);
}
.auth-dot {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: var(--text-muted);
}

/* ---------- 表单区 ---------- */
.auth-panel {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px 28px;
  overflow-y: auto;
  background: var(--bg-panel);
}
.auth-card {
  width: 100%;
  max-width: 384px;
}
.auth-head {
  margin-bottom: 18px;
}
.auth-head-title {
  display: flex;
  align-items: center;
  gap: 9px;
}
.auth-head-title h1 {
  font-size: 23px;
  font-weight: 680;
  letter-spacing: -0.3px;
}
.auth-sub {
  margin-top: 6px;
  font-size: var(--fs-md);
  color: var(--text-secondary);
}
.auth-notice {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  padding: 9px 10px;
  margin-bottom: 14px;
  border-radius: var(--r-md);
  background: var(--accent-soft);
  color: var(--accent-hover);
  font-size: var(--fs-sm);
  line-height: 1.55;
}
.auth-notice svg {
  margin-top: 1px;
  flex: 0 0 auto;
}
.auth-error {
  margin-bottom: 14px;
}
.auth-form {
  display: flex;
  flex-direction: column;
}
.auth-input {
  height: 38px;
  font-size: var(--fs-lg);
}
.auth-input-wrap {
  position: relative;
}
.auth-eye {
  position: absolute;
  right: 4px;
  top: 50%;
  transform: translateY(-50%);
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
}
.auth-eye:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
}
.auth-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 2px 0 18px;
}
.auth-help {
  font-size: var(--fs-sm);
  color: var(--text-muted);
}
.auth-foot {
  margin-top: 14px;
  font-size: var(--fs-sm);
  color: var(--text-muted);
  line-height: 1.65;
  text-align: center;
}
.auth-version {
  margin-top: 26px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  text-align: center;
  letter-spacing: 0.2px;
}

/* ---------- MFA ---------- */
.auth-steps {
  margin: 0 0 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  line-height: 1.55;
}
.auth-secret {
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--bg-inset);
  padding: 10px 11px;
  margin-bottom: 10px;
}
.auth-secret-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  margin-bottom: 5px;
}
.auth-secret-value {
  display: block;
  font-family: var(--mono);
  font-size: var(--fs-lg);
  letter-spacing: 1.2px;
  word-break: break-all;
  color: var(--text-primary);
}
.auth-secret-uri {
  display: block;
  margin-top: 4px;
  font-family: var(--mono);
  font-size: var(--fs-xs);
  line-height: 1.55;
  word-break: break-all;
  color: var(--text-muted);
}
.auth-recovery {
  border: 1px solid #f3e2c3;
  border-radius: var(--r-md);
  background: #fffaf0;
  padding: 10px 11px;
  margin-bottom: 12px;
}
.auth-codes {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 5px;
}
.auth-codes code {
  font-family: var(--mono);
  font-size: var(--fs-md);
  letter-spacing: 0.6px;
  padding: 4px 0;
  text-align: center;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--r-xs);
}
.auth-saved {
  margin-bottom: 14px;
}
.auth-code {
  height: 44px;
  font-family: var(--mono);
  font-size: 20px;
  letter-spacing: 8px;
  text-align: center;
}
.auth-account {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 11px;
  margin-bottom: 16px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--bg-inset);
}
.auth-account .avatar {
  width: 32px;
  height: 32px;
  font-size: var(--fs-lg);
}
.auth-account strong {
  font-size: var(--fs-lg);
}
.btn-block + .btn-block {
  margin-top: 6px;
}

/* ---------- 响应式 ---------- */
@media (max-width: 980px) {
  .auth {
    grid-template-columns: minmax(0, 1fr);
  }
  .auth-brand {
    padding: 20px 24px;
    gap: 14px;
  }
  .auth-brand-main {
    display: none;
  }
  .auth-brand-foot {
    display: none;
  }
  .auth-panel {
    padding: 22px 18px 40px;
  }
}
</style>

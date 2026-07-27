<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { Message, Lock, User } from '@element-plus/icons-vue'
import client from '@/api/client'

const auth = useAuthStore()
const router = useRouter()

const isLogin = ref(true)
const loading = ref(false)
const errorMsg = ref('')
const formRef = ref()
const registerSuccess = ref(false)

const form = reactive({
  email: '',
  password: '',
  displayName: '',
  confirmPassword: '',
})

function validateEmail(_rule: any, value: string, callback: any) {
  if (!value) {
    callback(new Error('请输入邮箱'))
  } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
    callback(new Error('邮箱格式不正确'))
  } else {
    callback()
  }
}

const loginRules = {
  email: [
    { required: true, validator: validateEmail, trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码长度至少6位', trigger: 'blur' },
  ],
}

const registerRules = {
  email: [
    { required: true, validator: validateEmail, trigger: 'blur' },
  ],
  displayName: [
    { required: true, message: '请输入姓名', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码长度至少6位', trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: '请确认密码', trigger: 'blur' },
    {
      validator: (_rule: any, value: string, callback: any) => {
        if (value !== form.password) {
          callback(new Error('两次密码不一致'))
        } else {
          callback()
        }
      },
      trigger: 'blur',
    },
  ],
}

function toggleMode() {
  isLogin.value = !isLogin.value
  errorMsg.value = ''
  registerSuccess.value = false
}

async function submit() {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }

  loading.value = true
  errorMsg.value = ''

  try {
    if (isLogin.value) {
      await auth.login(form.email, form.password)
      router.replace('/')
    } else {
      await client.post('/auth/register', {
        email: form.email,
        password: form.password,
        display_name: form.displayName,
      }, { timeout: 10000 })
      registerSuccess.value = true
      // 注册成功后自动填入邮箱
      isLogin.value = true
    }
  } catch (e: any) {
    if (e?.code === 'ECONNABORTED') {
      errorMsg.value = '请求超时，请确认后端服务已启动'
    } else {
      const detail = e?.response?.data?.detail
      errorMsg.value = detail || e?.message || '操作失败，请重试'
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-container">
    <div class="login-card">
      <!-- Left decorative panel -->
      <div class="login-brand">
        <div class="brand-content">
          <div class="brand-seal">微</div>
          <h1>AI 运营平台</h1>
          <p class="brand-subtitle">微信公众号智能运营系统</p>
          <div class="brand-steps">
            <div class="step done">
              <span class="step-dot" />
              <span>灵感入库</span>
            </div>
            <div class="step done">
              <span class="step-dot" />
              <span>AI 成稿</span>
            </div>
            <div class="step current">
              <span class="step-dot" />
              <span>人工审核</span>
            </div>
            <div class="step">
              <span class="step-dot" />
              <span>定时发布</span>
            </div>
          </div>
          <blockquote>
            "把重复交给系统，<br />把判断留给编辑。"
          </blockquote>
        </div>
      </div>

      <!-- Right login form panel -->
      <div class="login-form-panel">
        <div class="form-content">
          <div class="form-header">
            <p class="form-eyebrow">{{ isLogin ? 'WELCOME BACK' : 'CREATE ACCOUNT' }}</p>
            <h2>{{ isLogin ? '回到今天的编辑桌' : '创建你的团队' }}</h2>
            <p class="form-lead">
              {{ isLogin ? '登录后继续处理生成、审核和发布任务。' : '注册后自动创建团队，邀请成员一起运营公众号。' }}
            </p>
          </div>

          <el-alert
            v-if="registerSuccess"
            title="注册成功！请使用邮箱和密码登录"
            type="success"
            show-icon
            closable
            class="form-error"
          />

          <el-form
            ref="formRef"
            :model="form"
            :rules="isLogin ? loginRules : registerRules"
            label-position="top"
            size="large"
            @submit.prevent="submit"
          >
            <el-alert
              v-if="errorMsg"
              :title="errorMsg"
              type="error"
              show-icon
              closable
              class="form-error"
            />

            <el-form-item label="邮箱" prop="email">
              <el-input
                v-model="form.email"
                type="email"
                placeholder="请输入邮箱地址"
                autocomplete="username"
              >
                <template #prefix>
                  <el-icon><Message /></el-icon>
                </template>
              </el-input>
            </el-form-item>

            <!-- 注册专属字段 -->
            <template v-if="!isLogin">
              <el-form-item label="姓名" prop="displayName">
                <el-input
                  v-model="form.displayName"
                  placeholder="请输入姓名"
                >
                  <template #prefix>
                    <el-icon><User /></el-icon>
                  </template>
                </el-input>
              </el-form-item>
            </template>

            <el-form-item label="密码" prop="password">
              <el-input
                v-model="form.password"
                type="password"
                placeholder="请输入密码"
                autocomplete="current-password"
                show-password
              >
                <template #prefix>
                  <el-icon><Lock /></el-icon>
                </template>
              </el-input>
            </el-form-item>

            <!-- 注册确认密码 -->
            <el-form-item v-if="!isLogin" label="确认密码" prop="confirmPassword">
              <el-input
                v-model="form.confirmPassword"
                type="password"
                placeholder="请再次输入密码"
                show-password
              >
                <template #prefix>
                  <el-icon><Lock /></el-icon>
                </template>
              </el-input>
            </el-form-item>

            <el-form-item>
              <el-button
                type="primary"
                native-type="submit"
                :loading="loading"
                class="submit-btn"
                round
              >
                {{ loading ? '处理中...' : (isLogin ? '登录运营台' : '注册并创建团队') }}
              </el-button>
            </el-form-item>

            <div style="text-align:center;font-size:13px;color:#909399;">
              <template v-if="isLogin">
                还没有账号？
                <el-link type="primary" @click="toggleMode" style="font-size:13px;">立即注册</el-link>
              </template>
              <template v-else>
                已有账号？
                <el-link type="primary" @click="toggleMode" style="font-size:13px;">去登录</el-link>
              </template>
            </div>
          </el-form>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-container {
  display: grid;
  min-height: 100vh;
  place-items: center;
  background-color: #f0f2f5;
  padding: 20px;
}

.login-card {
  display: grid;
  grid-template-columns: 1fr 1fr;
  width: 100%;
  max-width: 900px;
  min-height: 560px;
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.1);
  background: #fff;
}

/* Left brand panel */
.login-brand {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 36px;
  background: #1d1e1f;
  color: #eff5f1;
}

.brand-content {
  max-width: 320px;
}

.brand-seal {
  display: grid;
  width: 52px;
  height: 52px;
  place-items: center;
  border: 1.5px solid rgba(255, 255, 255, 0.3);
  border-radius: 50%;
  font-size: 24px;
  font-weight: 700;
  color: #a8d6c9;
  margin-bottom: 24px;
}

.brand-content h1 {
  font-size: 28px;
  font-weight: 700;
  margin-bottom: 6px;
  color: #e9f0ec;
}

.brand-subtitle {
  color: #8fa19b;
  font-size: 13px;
  margin-bottom: 40px;
}

.brand-steps {
  display: grid;
  gap: 18px;
  margin-bottom: 40px;
  position: relative;
}

.brand-steps::before {
  position: absolute;
  top: 8px;
  bottom: 8px;
  left: 7px;
  width: 1px;
  content: '';
  background: #445b54;
}

.step {
  display: flex;
  align-items: center;
  gap: 14px;
  color: #6f827b;
  font-size: 14px;
  position: relative;
}

.step-dot {
  width: 9px;
  height: 9px;
  border: 1px solid #667b74;
  border-radius: 50%;
  flex-shrink: 0;
  z-index: 1;
  background: #1d1e1f;
}

.step.done {
  color: #a8bab4;
}

.step.done .step-dot {
  background: #688b81;
  border-color: #688b81;
}

.step.current {
  color: #fff;
  font-size: 16px;
  font-weight: 600;
}

.step.current .step-dot {
  border-color: #b6e3d6;
  background: #b6e3d6;
  box-shadow: 0 0 0 5px rgba(182, 227, 214, 0.15);
}

blockquote {
  font-size: 18px;
  line-height: 1.6;
  color: #bccfc8;
  font-style: italic;
  letter-spacing: 0.02em;
}

/* Right form panel */
.login-form-panel {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 40px;
}

.form-content {
  width: 100%;
  max-width: 380px;
}

.form-header {
  margin-bottom: 30px;
}

.form-eyebrow {
  font-size: 11px;
  letter-spacing: 0.15em;
  color: #909399;
  margin-bottom: 8px;
}

.form-header h2 {
  font-size: 26px;
  font-weight: 700;
  color: #303133;
  margin-bottom: 8px;
}

.form-lead {
  color: #909399;
  font-size: 14px;
}

.form-error {
  margin-bottom: 18px;
}

.submit-btn {
  width: 100%;
  margin-top: 8px;
}

/* Responsive */
@media (max-width: 720px) {
  .login-card {
    grid-template-columns: 1fr;
    max-width: 440px;
  }

  .login-brand {
    display: none;
  }

  .login-form-panel {
    padding: 36px 28px;
  }
}
</style>

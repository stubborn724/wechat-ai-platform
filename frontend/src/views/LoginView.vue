<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { Message, Lock } from '@element-plus/icons-vue'

const auth = useAuthStore()
const router = useRouter()

const form = reactive({
  email: '',
  password: '',
})

const loading = ref(false)
const errorMsg = ref('')
const formRef = ref()

function validateEmail(_rule: any, value: string, callback: any) {
  if (!value) {
    callback(new Error('请输入邮箱'))
  } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
    callback(new Error('邮箱格式不正确'))
  } else {
    callback()
  }
}

const rules = {
  email: [
    { required: true, validator: validateEmail, trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码长度至少6位', trigger: 'blur' },
  ],
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
    await auth.login(form.email, form.password)
    router.replace('/')
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    errorMsg.value = detail || e?.message || '邮箱或密码不正确，请重试'
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
            <p class="form-eyebrow">WELCOME BACK</p>
            <h2>回到今天的编辑桌</h2>
            <p class="form-lead">登录后继续处理生成、审核和发布任务。</p>
          </div>

          <el-form
            ref="formRef"
            :model="form"
            :rules="rules"
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

            <el-form-item>
              <el-button
                type="primary"
                native-type="submit"
                :loading="loading"
                class="submit-btn"
                round
              >
                {{ loading ? '正在登录...' : '登录运营台' }}
              </el-button>
            </el-form-item>
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

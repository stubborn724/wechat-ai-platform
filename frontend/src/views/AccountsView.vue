<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import type { Account } from '@/api/types'

const accounts = ref<Account[]>([])
const loading = ref(true)
const saving = ref(false)
const showForm = ref(false)

const form = reactive({
  name: '',
  app_id: '',
  app_secret: '',
  auth_mode: 'api' as string,
})

async function load() {
  loading.value = true
  try {
    const res = await client.get<{ total: number; items: Account[] }>('/accounts')
    accounts.value = res.data.items || []
  } catch {
    // ignore
  } finally {
    loading.value = false
  }
}

function openForm() {
  form.name = ''
  form.app_id = ''
  form.app_secret = ''
  form.auth_mode = 'api'
  showForm.value = true
}

async function create() {
  if (!form.name || !form.app_id) return
  saving.value = true
  try {
    await client.post('/accounts', {
      name: form.name,
      app_id: form.app_id,
      app_secret: form.app_secret || undefined,
      auth_mode: form.auth_mode,
    })
    showForm.value = false
    ElMessage.success('公众号已绑定')
    await load()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '绑定失败')
  } finally {
    saving.value = false
  }
}

async function remove(account: Account) {
  try {
    await ElMessageBox.confirm(
      `确定删除「${account.name}」？`,
      '删除确认',
      { confirmButtonText: '确定删除', cancelButtonText: '取消', type: 'warning' }
    )
    await client.delete(`/accounts/${account.id}`)
    ElMessage.success('公众号已删除')
    await load()
  } catch {
    // cancelled
  }
}

const authModeLabels: Record<string, string> = {
  api: '官方 API',
  token: 'Token 接入',
  browser: '浏览器扫码',
  hybrid: 'API + 浏览器兜底',
}

onMounted(load)
</script>

<template>
  <div class="accounts-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">CHANNEL REGISTRY</p>
        <h1>公众号</h1>
        <p class="lead">管理 AppID 与接入方式绑定。</p>
      </div>
      <el-button type="primary" @click="openForm">绑定公众号</el-button>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="loading-section">
      <el-skeleton :rows="3" animated />
    </div>

    <!-- Empty -->
    <div v-else-if="accounts.length === 0" class="empty-state">
      <el-empty description="还没有绑定公众号">
        <el-button type="primary" @click="openForm">绑定第一个公众号</el-button>
      </el-empty>
    </div>

    <!-- Account List -->
    <div v-else class="account-ledger">
      <div v-for="account in accounts" :key="account.id" class="account-card">
        <div class="account-seal">{{ account.name.slice(0, 1) }}</div>
        <div class="account-main">
          <div class="account-title-row">
            <h2>{{ account.name }}</h2>
            <el-tag
              :type="account.status === 'active' ? 'success' : 'info'"
              size="small"
            >
              {{ account.status === 'active' ? '已激活' : account.status }}
            </el-tag>
          </div>
          <code class="account-appid">{{ account.app_id }}</code>
          <p class="account-auth">接入方式：{{ authModeLabels[account.auth_mode] || account.auth_mode }}</p>
        </div>
        <div class="account-meta">
          <div class="meta-item">
            <span class="meta-label">凭据</span>
            <span>{{ account.credential_configured ? '已配置' : '未配置' }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">最近检查</span>
            <span>{{ account.last_health_at ? new Date(account.last_health_at).toLocaleString('zh-CN') : '尚未检查' }}</span>
          </div>
        </div>
        <div class="account-actions">
          <el-button size="small" @click="() => {}">检查配置</el-button>
          <el-button size="small" type="danger" plain @click="remove(account)">删除</el-button>
        </div>
      </div>
    </div>

    <!-- Create Dialog -->
    <el-dialog v-model="showForm" title="绑定公众号" width="480px">
      <el-form label-position="top">
        <el-form-item label="显示名称" required>
          <el-input v-model="form.name" placeholder="例如：品牌主号" />
        </el-form-item>
        <el-form-item label="AppID" required>
          <el-input v-model="form.app_id" placeholder="wx..." />
        </el-form-item>
        <el-form-item label="AppSecret">
          <el-input
            v-model="form.app_secret"
            type="password"
            show-password
            placeholder="微信公众号 AppSecret（加密存储，保存后不再显示）"
          />
          <span class="form-hint">加密存储，保存后界面不再回显</span>
        </el-form-item>
        <el-form-item label="接入方式">
          <el-select v-model="form.auth_mode" style="width: 100%">
            <el-option value="api" label="官方 API" />
            <el-option value="token" label="Token 接入" />
            <el-option value="browser" label="浏览器扫码" />
            <el-option value="hybrid" label="API + 浏览器兜底" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showForm = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="create">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.accounts-page {
  max-width: 1200px;
}

.page-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 28px;
}

.eyebrow {
  font-size: 11px;
  letter-spacing: 0.15em;
  color: #909399;
  margin-bottom: 6px;
}

.page-heading h1 {
  font-size: 24px;
  font-weight: 700;
  color: #303133;
  margin-bottom: 6px;
}

.lead {
  color: #909399;
  font-size: 14px;
}

.loading-section {
  padding: 40px 0;
}

.empty-state {
  padding: 60px 0;
}

.account-ledger {
  display: grid;
  gap: 16px;
}

.account-card {
  display: grid;
  grid-template-columns: 60px 1fr auto auto;
  gap: 20px;
  align-items: center;
  padding: 20px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fff;
  transition: box-shadow 0.2s;
}

.account-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
}

.account-seal {
  display: grid;
  width: 52px;
  height: 52px;
  place-items: center;
  border: 1px solid #dcdfe6;
  border-radius: 50%;
  color: #606266;
  background: #f5f7fa;
  font-size: 22px;
  font-weight: 600;
}

.account-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.account-main h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.account-appid {
  display: block;
  margin: 6px 0;
  color: #909399;
  font-size: 12px;
  font-family: monospace;
}

.account-auth {
  margin: 0;
  color: #909399;
  font-size: 12px;
}

.account-meta {
  display: grid;
  gap: 8px;
}

.meta-item {
  display: grid;
  grid-template-columns: 70px 1fr;
  gap: 8px;
  font-size: 12px;
}

.meta-label {
  color: #909399;
}

.account-actions {
  display: grid;
  gap: 8px;
  justify-items: end;
}

@media (max-width: 800px) {
  .account-card {
    grid-template-columns: 52px 1fr;
  }
  .account-meta,
  .account-actions {
    grid-column: 2;
  }
  .account-actions {
    display: flex;
  }
}

.form-hint {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}
</style>

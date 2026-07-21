<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'

interface OAuthAccount {
  id: number
  app_id: string
  nick_name: string | null
  head_img: string | null
  alias: string | null
  service_type_info: number | null
  verify_type_info: number | null
  user_name: string | null
  qrcode_url: string | null
  func_info: any[] | null
  token_expires_at: string | null
  created_at: string
}

const accounts = ref<OAuthAccount[]>([])
const loading = ref(true)
const authLoading = ref(false)

async function load() {
  loading.value = true
  try {
    const res = await client.get('/wechat-oauth/accounts')
    accounts.value = res.data || []
  } catch {
    accounts.value = []
  } finally {
    loading.value = false
  }
}

async function startAuth() {
  authLoading.value = true
  try {
    const res = await client.get('/wechat-oauth/auth-url')
    const authUrl = res.data.auth_url
    // 在新窗口打开微信扫码页
    const width = 700
    const height = 600
    const left = (window.screen.width - width) / 2
    const top = (window.screen.height - height) / 2
    const win = window.open(
      authUrl,
      'wechat_auth',
      `width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no`
    )

    // 轮询检测是否授权完成（通过 URL 参数检测）
    // 简化：弹窗提示用户扫码后手动输入 auth_code
    ElMessage.info('请在新窗口中使用微信扫码授权，授权后复制回调 URL 中的 auth_code')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '获取授权链接失败，请检查开放平台配置')
  } finally {
    authLoading.value = false
  }
}

async function bindWithCode() {
  let authCode = ''
  try {
    const { value } = await ElMessageBox.prompt(
      '请从授权回调 URL 中复制 auth_code 参数的值',
      '输入授权码',
      { confirmButtonText: '确定绑定', cancelButtonText: '取消', inputPlaceholder: 'auth_code...' }
    )
    authCode = value
  } catch { return }

  if (!authCode) return

  authLoading.value = true
  try {
    await client.post(`/wechat-oauth/bind?auth_code=${encodeURIComponent(authCode)}`)
    ElMessage.success('公众号授权绑定成功')
    await load()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '绑定失败')
  } finally {
    authLoading.value = false
  }
}

async function unbind(account: OAuthAccount) {
  try {
    await ElMessageBox.confirm(
      `确定取消「${account.nick_name || account.app_id}」的授权？`,
      '取消授权',
      { confirmButtonText: '确定取消', cancelButtonText: '取消', type: 'warning' }
    )
    await client.delete(`/wechat-oauth/accounts/${account.id}`)
    ElMessage.success('已取消授权')
    await load()
  } catch { /* cancelled */ }
}

function serviceTypeLabel(type: number | null): string {
  const map: Record<number, string> = { 0: '订阅号', 1: '由历史迁移', 2: '服务号' }
  return type !== null ? (map[type] || `未知(${type})`) : '-'
}

onMounted(load)
</script>

<template>
  <div class="oauth-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">WECHAT OPEN PLATFORM</p>
        <h1>扫码授权</h1>
        <p class="lead">通过微信开放平台扫码授权，自动管理公众号发布。</p>
      </div>
      <div class="header-actions">
        <el-button @click="bindWithCode" :disabled="authLoading">输入授权码</el-button>
        <el-button type="primary" :loading="authLoading" @click="startAuth">
          {{ authLoading ? '获取中...' : '微信扫码授权' }}
        </el-button>
      </div>
    </div>

    <div v-if="loading" class="loading-section">
      <el-skeleton :rows="3" animated />
    </div>

    <div v-else-if="accounts.length === 0" class="empty-state">
      <el-empty description="还没有扫码授权的公众号">
        <el-button type="primary" @click="startAuth">立即扫码授权</el-button>
      </el-empty>
    </div>

    <div v-else class="account-list">
      <div v-for="account in accounts" :key="account.id" class="account-card">
        <div class="account-avatar">
          <img v-if="account.head_img" :src="account.head_img" class="avatar-img" />
          <div v-else class="avatar-placeholder">{{ (account.nick_name || '?').slice(0, 1) }}</div>
        </div>
        <div class="account-info">
          <div class="info-row">
            <strong>{{ account.nick_name || '未命名' }}</strong>
            <el-tag size="small" type="success">已授权</el-tag>
          </div>
          <div class="info-meta">
            <span>类型: {{ serviceTypeLabel(account.service_type_info) }}</span>
            <span v-if="account.alias"> | 微信号: {{ account.alias }}</span>
          </div>
          <div class="info-meta">
            <code>{{ account.app_id }}</code>
            <span v-if="account.user_name"> | 原始 ID: {{ account.user_name }}</span>
          </div>
          <div class="info-meta">
            <span>Token 到期: {{ account.token_expires_at ? new Date(account.token_expires_at).toLocaleString('zh-CN') : '未知' }}</span>
          </div>
        </div>
        <div class="account-actions">
          <el-button size="small" type="danger" plain @click="unbind(account)">取消授权</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.oauth-page { max-width: 1200px; }
.page-heading {
  display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 28px;
}
.eyebrow {
  font-size: 11px; letter-spacing: 0.15em; color: #909399; margin-bottom: 6px;
}
.page-heading h1 { font-size: 24px; font-weight: 700; color: #303133; margin-bottom: 6px; }
.lead { color: #909399; font-size: 14px; }
.header-actions { display: flex; gap: 8px; }
.loading-section { padding: 40px 0; }
.empty-state { padding: 60px 0; }
.account-list { display: grid; gap: 16px; }
.account-card {
  display: grid; grid-template-columns: 60px 1fr auto; gap: 16px; align-items: center;
  padding: 20px; border: 1px solid #e4e7ed; border-radius: 8px; background: #fff;
  transition: box-shadow 0.2s;
}
.account-card:hover { box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06); }
.account-avatar { width: 52px; height: 52px; border-radius: 50%; overflow: hidden; }
.avatar-img { width: 100%; height: 100%; object-fit: cover; }
.avatar-placeholder {
  width: 52px; height: 52px; border-radius: 50%; background: #f5f7fa;
  display: grid; place-items: center; font-size: 20px; font-weight: 600; color: #909399;
  border: 1px solid #dcdfe6;
}
.info-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.info-row strong { font-size: 16px; }
.info-meta { color: #909399; font-size: 12px; margin-bottom: 2px; }
.info-meta code { font-size: 11px; background: #f5f7fa; padding: 1px 4px; border-radius: 2px; }
.account-actions { display: grid; gap: 8px; }
</style>

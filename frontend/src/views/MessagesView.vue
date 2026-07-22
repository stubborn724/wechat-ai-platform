<template>
  <div class="messages-view">
    <div class="page-header">
      <h2>私信管理</h2>
      <div class="header-actions">
        <el-select v-model="filterAccountId" placeholder="选择公众号" clearable style="width:200px">
          <el-option v-for="a in accounts" :key="a.id" :label="a.nick_name" :value="a.id" />
        </el-select>
        <el-select v-model="filterType" placeholder="消息类型" clearable style="width:140px">
          <el-option label="文本" value="text" />
          <el-option label="图片" value="image" />
          <el-option label="视频" value="video" />
        </el-select>
        <el-input v-model="filterOpenid" placeholder="搜索 OpenID" clearable style="width:200px" />
      </div>
    </div>

    <div class="send-card">
      <h3>发送私信</h3>
      <el-form :model="sendForm" label-width="100px" inline>
        <el-form-item label="公众号" required>
          <el-select v-model="sendForm.account_id" placeholder="选择公众号" style="width:200px">
            <el-option v-for="a in accounts" :key="a.id" :label="a.nick_name" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="用户 OpenID" required>
          <el-input v-model="sendForm.openid" placeholder="输入用户 OpenID" style="width:260px" />
        </el-form-item>
      </el-form>
      <el-form :model="sendForm" label-width="100px">
        <el-form-item label="消息类型">
          <el-radio-group v-model="sendForm.msg_type">
            <el-radio value="text">文本</el-radio>
            <el-radio value="image">图片</el-radio>
            <el-radio value="contact">联系方式+二维码</el-radio>
          </el-radio-group>
        </el-form-item>

        <!-- 文本 -->
        <el-form-item v-if="sendForm.msg_type === 'text'" label="内容" required>
          <el-input v-model="sendForm.text" type="textarea" :rows="3" placeholder="输入私信内容" style="width:100%" />
        </el-form-item>

        <!-- 图片 -->
        <el-form-item v-if="sendForm.msg_type === 'image'" label="图片素材" required>
          <el-select v-model="sendForm.media_id" placeholder="选择素材图片" filterable style="width:300px">
            <el-option v-for="a in imageAssets" :key="a.id" :label="a.original_filename" :value="a.storage_key">
              <span>{{ a.original_filename }}</span>
              <el-tag size="small" type="info" style="margin-left:8px">{{ a.file_size ? (a.file_size/1024).toFixed(0)+'KB' : '' }}</el-tag>
            </el-option>
          </el-select>
        </el-form-item>

        <!-- 联系方式 -->
        <template v-if="sendForm.msg_type === 'contact'">
          <el-form-item label="联系文案" required>
            <el-input v-model="sendForm.contact_text" type="textarea" :rows="3" placeholder="如：欢迎咨询，电话：138xxxx" />
          </el-form-item>
          <el-form-item label="二维码素材" required>
            <el-select v-model="sendForm.qr_media_id" placeholder="选择二维码图片" filterable style="width:300px">
              <el-option v-for="a in imageAssets" :key="a.id" :label="a.original_filename" :value="a.storage_key" />
            </el-select>
          </el-form-item>
        </template>

        <el-form-item>
          <el-button type="primary" :loading="sending" @click="handleSend">发送</el-button>
          <el-button @click="resetForm">重置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <el-table :data="messages" v-loading="loading" stripe style="width:100%">
      <el-table-column prop="openid" label="用户 OpenID" width="180" show-overflow-tooltip />
      <el-table-column label="消息类型" width="100">
        <template #default="{ row }">
          <el-tag size="small">{{ row.msg_type }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="content" label="内容" min-width="300" show-overflow-tooltip />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 'sent' ? 'success' : 'danger'" size="small">
            {{ row.status === 'sent' ? '已发送' : '失败' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="时间" width="170">
        <template #default="{ row }">
          {{ formatTime(row.sent_at || row.created_at) }}
        </template>
      </el-table-column>
    </el-table>

    <div class="pagination-wrap">
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="prev, pager, next, total"
        @current-change="fetchMessages"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listMessages, sendTextMessage, sendImageMessage, sendContact } from '@/api/wechat'
import type { MessageRecord } from '@/api/wechat'
import client from '@/api/client'

const messages = ref<MessageRecord[]>([])
const loading = ref(false)
const sending = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const filterAccountId = ref<number | undefined>()
const filterType = ref<string | undefined>()
const filterOpenid = ref('')

const accounts = ref<any[]>([])
const imageAssets = ref<any[]>([])

const sendForm = ref({
  account_id: undefined as number | undefined,
  openid: '',
  msg_type: 'text' as string,
  text: '',
  media_id: '',
  contact_text: '',
  qr_media_id: '',
})

function formatTime(t?: string) {
  if (!t) return ''
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}

async function fetchAccounts() {
  try {
    const res = await client.get('/wechat-oauth/accounts')
    accounts.value = res.data || []
  } catch { /* ignore */ }
}

async function fetchImageAssets() {
  try {
    const res = await client.get('/assets', { params: { type: 'image', page_size: 100 } })
    imageAssets.value = res.data.items || []
  } catch { /* ignore */ }
}

async function fetchMessages() {
  loading.value = true
  try {
    const res = await listMessages({
      page: page.value,
      page_size: pageSize.value,
      msg_type: filterType.value,
      account_id: filterAccountId.value,
      openid: filterOpenid.value || undefined,
    })
    messages.value = res.items
    total.value = res.total
  } catch (e: any) {
    ElMessage.error('加载私信记录失败：' + (e.message || ''))
  } finally {
    loading.value = false
  }
}

async function handleSend() {
  const f = sendForm.value
  if (!f.account_id || !f.openid) {
    ElMessage.warning('请选择公众号并填写用户 OpenID')
    return
  }
  sending.value = true
  try {
    if (f.msg_type === 'text') {
      if (!f.text.trim()) { ElMessage.warning('请输入内容'); return }
      await sendTextMessage({ account_id: f.account_id, openid: f.openid, text: f.text })
    } else if (f.msg_type === 'image') {
      if (!f.media_id) { ElMessage.warning('请选择图片素材'); return }
      await sendImageMessage({ account_id: f.account_id, openid: f.openid, media_id: f.media_id })
    } else if (f.msg_type === 'contact') {
      if (!f.contact_text || !f.qr_media_id) { ElMessage.warning('请填写联系文案并选择二维码素材'); return }
      await sendContact({ account_id: f.account_id, openid: f.openid, contact_text: f.contact_text, qr_code_media_id: f.qr_media_id })
    }
    ElMessage.success('发送成功')
    resetForm()
    fetchMessages()
  } catch (e: any) {
    ElMessage.error('发送失败：' + (e.message || ''))
  } finally {
    sending.value = false
  }
}

function resetForm() {
  sendForm.value = {
    account_id: undefined,
    openid: '',
    msg_type: 'text',
    text: '',
    media_id: '',
    contact_text: '',
    qr_media_id: '',
  }
}

onMounted(() => {
  fetchAccounts()
  fetchImageAssets()
  fetchMessages()
})
</script>

<style scoped>
.messages-view { padding: 20px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 12px; }
.header-actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.send-card { background: #f5f7fa; border-radius: 8px; padding: 20px; margin-bottom: 20px; }
.send-card h3 { margin: 0 0 16px 0; font-size: 16px; }
.pagination-wrap { margin-top: 20px; display: flex; justify-content: center; }
</style>

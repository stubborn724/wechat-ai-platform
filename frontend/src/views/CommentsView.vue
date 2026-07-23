<template>
  <div class="comments-view">
    <div class="page-header">
      <h2>评论管理</h2>
      <div class="header-actions">
        <el-select v-model="filterAccountId" placeholder="选择公众号" clearable style="width:240px" @change="onAccountChange">
          <el-option v-for="a in accounts" :key="a.id" :label="a.label" :value="a.id">
            <span>{{ a.label }}</span>
            <el-tag v-if="a.account_type === 'oauth'" size="small" type="success" style="margin-left:8px">授权</el-tag>
            <el-tag v-else size="small" type="info" style="margin-left:8px">凭据</el-tag>
          </el-option>
        </el-select>
        <el-select v-model="filterStatus" placeholder="状态" clearable style="width:140px">
          <el-option label="待回复" value="pending" />
          <el-option label="已回复" value="replied" />
          <el-option label="已忽略" value="ignored" />
        </el-select>
        <el-button type="primary" :loading="syncingAll" @click="handleSyncAll">
          一键同步所有文章
        </el-button>
      </div>
    </div>

    <!-- 自动回复 & 自动私信 配置卡 -->
    <el-card class="auto-config-card" v-if="filterAccountId">
      <template #header>
        <div class="config-header">
          <span>⚙️ 自动回复 & 自动私信设置</span>
          <el-button type="primary" size="small" :loading="savingConfig" @click="saveAutoConfig">
            保存配置
          </el-button>
        </div>
      </template>
      <el-form :model="autoConfig" label-width="100px" label-position="top">
        <el-row :gutter="24">
          <!-- 自动回复 -->
          <el-col :span="12">
            <el-form-item>
              <template #label>
                <span>
                  自动回复评论
                  <el-switch v-model="autoConfig.auto_reply_enabled" size="small" style="margin-left:8px" />
                </span>
              </template>
              <el-input
                v-model="autoConfig.auto_reply_content"
                type="textarea"
                :rows="3"
                :disabled="!autoConfig.auto_reply_enabled"
                placeholder="输入自动回复的内容，有新评论时自动回复"
              />
            </el-form-item>
          </el-col>

          <!-- 自动私信 -->
          <el-col :span="12">
            <el-form-item>
              <template #label>
                <span>
                  自动发送私信
                  <el-switch v-model="autoConfig.auto_msg_enabled" size="small" style="margin-left:8px" />
                </span>
              </template>
              <el-input
                v-model="autoConfig.auto_msg_content"
                type="textarea"
                :rows="3"
                :disabled="!autoConfig.auto_msg_enabled"
                placeholder="输入自动私信的内容，有新评论时自动给该用户发私信（不重复发送）"
              />
              <div class="config-tip">已发送过私信的用户不会重复发送</div>
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
    </el-card>

    <!-- 已发布文章列表（用于同步评论） -->
    <el-card class="articles-card" v-if="publishedArticles.length > 0">
      <template #header>
        <span>已发布文章（点击同步评论）</span>
      </template>
      <el-table :data="publishedArticles" stripe size="small">
        <el-table-column prop="main_title" label="标题" min-width="250" show-overflow-tooltip />
        <el-table-column prop="topic" label="主题" width="200" show-overflow-tooltip />
        <el-table-column label="msg_data_id" width="180">
          <template #default="{ row }">
            <code style="font-size:12px">{{ row.msg_data_id || '未设置' }}</code>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button type="primary" size="small" :loading="syncingArticleId === row.id" @click="handleSyncArticle(row)">
              同步评论
            </el-button>
            <el-button type="warning" size="small" @click="openSetMsgId(row)">
              设置ID
            </el-button>
            <el-button type="danger" size="small" @click="handleDebugApi(row)">
              诊断
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 评论列表 -->
    <el-tabs v-model="activeTab" style="margin-top:16px">
      <el-tab-pane label="全部评论" name="comments">
        <el-table :data="comments" v-loading="loading" stripe style="width:100%">
          <el-table-column prop="nickname" label="用户" width="140" />
          <el-table-column prop="content" label="评论内容" min-width="300" show-overflow-tooltip />
          <el-table-column label="时间" width="170">
            <template #default="{ row }">
              {{ formatTime(row.create_time) }}
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.status === 'replied' ? 'success' : row.status === 'ignored' ? 'info' : 'warning'" size="small">
                {{ row.status === 'replied' ? '已回复' : row.status === 'ignored' ? '已忽略' : '待回复' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="精选" width="70" align="center">
            <template #default="{ row }">
              <el-switch :model-value="row.is_favorited" @change="(v) => handleToggleFavorite(row, v)" size="small" />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="220" fixed="right">
            <template #default="{ row }">
              <el-button type="primary" link size="small" @click="openReply(row)">回复</el-button>
              <el-button type="success" link size="small" @click="openSendMsg(row)">私信</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination-wrap">
          <el-pagination
            v-model:current-page="page"
            :page-size="pageSize"
            :total="total"
            layout="prev, pager, next, total"
            @current-change="fetchComments"
          />
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 回复对话框 -->
    <el-dialog v-model="replyDialogVisible" title="回复评论" width="500px">
      <div class="reply-context">
        <p><strong>{{ replyTarget?.nickname || '用户' }}：</strong>{{ replyTarget?.content }}</p>
      </div>
      <el-input v-model="replyContent" type="textarea" :rows="4" placeholder="输入回复内容..." />
      <template #footer>
        <el-button @click="replyDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="replying" @click="handleReply">发送回复</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listComments, replyComment, toggleFavorite } from '@/api/wechat'
import type { Comment } from '@/api/wechat'
import client from '@/api/client'

const comments = ref<Comment[]>([])
const loading = ref(false)
const syncingAll = ref(false)
const replying = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const filterAccountId = ref<number | undefined>()
const filterStatus = ref<string | undefined>()
const activeTab = ref('comments')

const accounts = ref<any[]>([])
const publishedArticles = ref<any[]>([])
const syncingArticleId = ref<number | null>(null)

const replyDialogVisible = ref(false)
const replyTarget = ref<Comment | null>(null)
const replyContent = ref('')

// 自动配置
const autoConfig = ref({
  auto_reply_enabled: false,
  auto_reply_content: '',
  auto_msg_enabled: false,
  auto_msg_content: '',
})
const savingConfig = ref(false)

function formatTime(t?: string) {
  if (!t) return ''
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}

async function fetchAccounts() {
  try {
    const res = await client.get('/accounts')
    const items: any[] = res.data?.items || res.data || []
    accounts.value = items
      .filter((a: any) => a.id != null)
      .map((a: any) => ({ ...a, label: a.name || a.app_id }))
  } catch { /* ignore */ }
}

async function fetchPublishedArticles() {
  try {
    const res = await client.get('/articles', {
      params: { status: 'published', page_size: 50 }
    })
    publishedArticles.value = (res.data?.items || res.data || []).filter(
      (a: any) => a.msg_data_id || a.status === 'published'
    )
  } catch { /* ignore */ }
}

async function fetchComments() {
  loading.value = true
  try {
    const res = await listComments({
      page: page.value,
      page_size: pageSize.value,
      status: filterStatus.value,
      account_id: filterAccountId.value,
    })
    comments.value = res.items
    total.value = res.total
  } catch (e: any) {
    ElMessage.error('加载评论失败：' + (e.message || ''))
  } finally {
    loading.value = false
  }
}

// 切换公众号时加载对应的自动配置
async function onAccountChange() {
  if (!filterAccountId.value) return
  try {
    const res = await client.get(`/comments/auto-config/${filterAccountId.value}`)
    autoConfig.value = {
      auto_reply_enabled: res.data.auto_reply_enabled,
      auto_reply_content: res.data.auto_reply_content || '',
      auto_msg_enabled: res.data.auto_msg_enabled,
      auto_msg_content: res.data.auto_msg_content || '',
    }
  } catch {
    // 404 = 尚未配置，使用默认值
    autoConfig.value = {
      auto_reply_enabled: false,
      auto_reply_content: '',
      auto_msg_enabled: false,
      auto_msg_content: '',
    }
  }
}

async function saveAutoConfig() {
  if (!filterAccountId.value) return
  savingConfig.value = true
  try {
    await client.put(`/comments/auto-config/${filterAccountId.value}`, autoConfig.value)
    ElMessage.success('配置已保存')
  } catch (e: any) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message || ''))
  } finally {
    savingConfig.value = false
  }
}

async function handleSyncArticle(article: any) {
  if (!filterAccountId.value) {
    ElMessage.warning('请先选择公众号')
    return
  }
  syncingArticleId.value = article.id
  try {
    const res = await client.post('/comments/sync-by-article', null, {
      params: {
        article_id: article.id,
        account_id: filterAccountId.value,
      }
    })
    const d = res.data
    const parts = [`同步完成：新增 ${d.new} 条，共 ${d.total} 条`]
    if (d.auto_replied > 0) parts.push(`已自动回复 ${d.auto_replied} 条`)
    if (d.auto_messaged > 0) parts.push(`已自动私信 ${d.auto_messaged} 人`)
    if (d.auto_skipped_msg > 0) parts.push(`跳过 ${d.auto_skipped_msg} 人（已发过）`)
    ElMessage.success(parts.join('，'))
    fetchComments()
  } catch (e: any) {
    const detail = e.response?.data?.detail || e.message || ''
    if (detail.includes('no msg_data_id')) {
      ElMessageBox.prompt('该文章还没有 msg_data_id，请在微信后台发布后输入文章 ID', '设置 msg_data_id', {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        inputPlaceholder: '输入微信文章 msg_data_id',
      }).then(async ({ value }) => {
        if (!value) return
        await client.post(`/articles/${article.task_id}/set-msg-data-id`, null, {
          params: { msg_data_id: value }
        })
        ElMessage.success('设置成功，请重新同步')
        fetchPublishedArticles()
      }).catch(() => {})
    } else if (detail.includes('48001')) {
      ElMessage.error('微信 API 返回无权调用评论接口（errcode=48001），请确认该公众号已认证且有评论管理权限')
    } else if (detail.includes('46003')) {
      ElMessage.error('微信 API 返回：该文章不存在或 msg_data_id 不正确')
    } else {
      ElMessage.error('同步失败：' + detail)
    }
  } finally {
    syncingArticleId.value = null
  }
}

async function handleDebugApi(article: any) {
  if (!filterAccountId.value) {
    ElMessage.warning('请先选择公众号')
    return
  }
  try {
    const res = await client.get('/comments/debug-wechat-api', {
      params: { article_id: article.id, account_id: filterAccountId.value }
    })
    const data = res.data
    if (data.error) {
      ElMessageBox.alert(
        `微信 API 返回错误：${data.error}\n\n建议：${data.suggestion || '请检查公众号配置'}`,
        '诊断结果', { type: 'error', confirmButtonText: '知道了' }
      )
    } else {
      const info = [
        `msg_data_id: ${data.msg_data_id}`,
        `打开评论结果: ${JSON.stringify(data.open_comment_result)}`,
        `微信返回总评论数: ${data.total}`,
        `本次获取到: ${data.comment_count} 条`,
        `原始响应: ${JSON.stringify(data.list_raw_response, null, 2)}`,
      ].join('\n\n')
      ElMessageBox.alert(info, '诊断结果', {
        type: data.comment_count > 0 ? 'success' : 'warning',
        confirmButtonText: '知道了',
        dangerouslyUseHTMLString: false,
      })
    }
  } catch (e: any) {
    ElMessage.error('诊断失败：' + (e.response?.data?.detail || e.message || ''))
  }
}

async function handleSyncAll() {
  if (!filterAccountId.value) {
    ElMessage.warning('请先选择公众号')
    return
  }
  syncingAll.value = true
  let totalNew = 0
  let totalReplied = 0
  let totalMsgd = 0
  let totalSkipped = 0
  for (const article of publishedArticles.value) {
    if (!article.msg_data_id) continue
    try {
      const res = await client.post('/comments/sync-by-article', null, {
        params: { article_id: article.id, account_id: filterAccountId.value }
      })
      const d = res.data
      totalNew += d.new || 0
      totalReplied += d.auto_replied || 0
      totalMsgd += d.auto_messaged || 0
      totalSkipped += d.auto_skipped_msg || 0
    } catch { /* skip failed */ }
  }
  ElMessage.success(
    `全量同步完成，新增 ${totalNew} 条` +
    (totalReplied ? `，自动回复 ${totalReplied} 条` : '') +
    (totalMsgd ? `，自动私信 ${totalMsgd} 人` : '') +
    (totalSkipped ? `，跳过 ${totalSkipped} 人` : '')
  )
  syncingAll.value = false
  fetchComments()
}

function openSetMsgId(article: any) {
  ElMessageBox.prompt(
    article.msg_data_id ? `当前 msg_data_id: ${article.msg_data_id}` : '该文章还没有 msg_data_id',
    '设置文章 msg_data_id',
    {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      inputPlaceholder: '输入微信文章 msg_data_id',
      inputValue: article.msg_data_id || '',
    }
  ).then(async ({ value }) => {
    if (!value) return
    await client.post(`/articles/${article.task_id}/set-msg-data-id`, null, {
      params: { msg_data_id: value }
    })
    ElMessage.success('设置成功')
    fetchPublishedArticles()
  }).catch(() => {})
}

function openReply(comment: Comment) {
  replyTarget.value = comment
  replyContent.value = ''
  replyDialogVisible.value = true
}

async function handleReply() {
  if (!replyTarget.value || !replyContent.value.trim()) return
  replying.value = true
  try {
    await replyComment({
      account_id: replyTarget.value.account_id || 0,
      comment_id: parseInt(replyTarget.value.comment_id),
      msg_data_id: replyTarget.value.msg_id,
      content: replyContent.value,
    })
    ElMessage.success('回复成功')
    replyDialogVisible.value = false
    fetchComments()
  } catch (e: any) {
    ElMessage.error('回复失败：' + (e.message || ''))
  } finally {
    replying.value = false
  }
}

async function handleToggleFavorite(row: Comment, val: boolean) {
  try {
    await toggleFavorite({
      account_id: row.account_id || 0,
      comment_id: parseInt(row.comment_id),
      msg_data_id: row.msg_id,
      favorited: val,
    })
    row.is_favorited = val
    ElMessage.success(val ? '已设为精选' : '已取消精选')
  } catch (e: any) {
    ElMessage.error('操作失败：' + (e.message || ''))
  }
}

function openSendMsg(comment: Comment) {
  ElMessageBox.prompt('请输入要发送的私信内容', '发送私信给 ' + comment.nickname, {
    confirmButtonText: '发送',
    cancelButtonText: '取消',
    inputType: 'textarea',
  }).then(async ({ value }) => {
    if (!value) return
    try {
      const { sendTextMessage } = await import('@/api/wechat')
      await sendTextMessage({
        account_id: comment.account_id || 0,
        openid: comment.openid || '',
        text: value,
      })
      ElMessage.success('私信发送成功')
    } catch (e: any) {
      ElMessage.error('私信发送失败：' + (e.message || ''))
    }
  }).catch(() => {})
}

onMounted(() => {
  fetchAccounts()
  fetchComments()
  fetchPublishedArticles()
})
</script>

<style scoped>
.comments-view { padding: 20px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 12px; }
.header-actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.auto-config-card { margin-bottom: 16px; }
.config-header { display: flex; justify-content: space-between; align-items: center; }
.config-tip { font-size: 12px; color: #909399; margin-top: 4px; }
.articles-card { margin-bottom: 16px; }
.pagination-wrap { margin-top: 20px; display: flex; justify-content: center; }
.reply-context { background: #f5f7fa; padding: 12px; border-radius: 6px; margin-bottom: 12px; }
.reply-context p { margin: 0; font-size: 14px; }
</style>
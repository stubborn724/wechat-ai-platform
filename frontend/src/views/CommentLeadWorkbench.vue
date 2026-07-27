<template>
  <div class="lead-workbench">
    <!-- 顶栏：搜索 + 同步 -->
    <div class="top-bar">
      <div class="search-area">
        <el-input
          v-model="searchKeyword"
          placeholder="搜索评论内容、用户昵称、文章标题…"
          clearable
          size="default"
          prefix-icon="Search"
          style="width:360px"
          @clear="handleSearch"
          @keyup.enter="handleSearch"
        />
      </div>
      <div class="top-actions">
        <span v-if="lastSyncTime" class="sync-status">上次同步：{{ formatTime(lastSyncTime) }}</span>
        <el-button size="small" type="primary" :loading="syncing" @click="showSyncDialog = true">
          <el-icon><Refresh /></el-icon> 同步评论
        </el-button>
      </div>
    </div>

    <!-- 统计卡片 -->
    <div class="stats-row">
      <div class="stat-card" v-for="s in statCards" :key="s.key" @click="switchQueue(s.key)">
        <div class="stat-value" :class="s.color">{{ stats[s.key] ?? 0 }}</div>
        <div class="stat-label">{{ s.label }}</div>
      </div>
    </div>

    <div class="workbench-body">
      <!-- 左侧：队列 + 筛选 -->
      <div class="left-panel">
        <div class="queue-section">
          <div class="queue-title">评论线索</div>
          <div
            v-for="q in queues"
            :key="q.key"
            class="queue-item"
            :class="{ active: activeQueue === q.key }"
            @click="switchQueue(q.key)"
          >
            <span class="queue-label">{{ q.label }}</span>
            <el-tag v-if="(stats[q.key] ?? 0) > 0" size="small" :type="q.type" effect="plain">
              {{ stats[q.key] }}
            </el-tag>
          </div>
        </div>

        <el-divider style="margin:8px 0" />

        <div class="filter-section">
          <div class="filter-group">
            <label>公众号</label>
            <el-select v-model="filterAccountId" placeholder="全部" clearable size="small" style="width:100%" @change="onFilterChange">
              <el-option v-for="a in accounts" :key="a.id" :label="a.name || a.app_id" :value="a.id" />
            </el-select>
          </div>
          <div class="filter-group">
            <label>来源文章</label>
            <el-select v-model="filterArticleId" placeholder="全部" clearable size="small" style="width:100%" @change="onFilterChange">
              <el-option v-for="a in publishedArticles" :key="a.id" :label="a.main_title || a.topic || '无标题'" :value="a.id" />
            </el-select>
          </div>
          <div class="filter-group">
            <label>意图</label>
            <el-select v-model="filterIntent" placeholder="全部" clearable size="small" style="width:100%" @change="onFilterChange">
              <el-option label="购买咨询" value="purchase" />
              <el-option label="价格咨询" value="price" />
              <el-option label="合作咨询" value="cooperation" />
              <el-option label="售后投诉" value="after_sale" />
              <el-option label="普通互动" value="interaction" />
              <el-option label="垃圾内容" value="spam" />
            </el-select>
          </div>
          <div class="filter-group">
            <label>负责人</label>
            <el-select v-model="filterOperatorId" placeholder="全部" clearable size="small" style="width:100%" @change="onFilterChange">
              <el-option v-for="u in users" :key="u.id" :label="u.display_name" :value="u.id" />
            </el-select>
          </div>
          <div class="filter-group">
            <label>时间范围</label>
            <el-date-picker v-model="dateRange" type="daterange" range-separator="至" start-placeholder="开始" end-placeholder="结束" size="small" style="width:100%" @change="onFilterChange" />
          </div>
          <el-button size="small" style="width:100%;margin-top:4px" @click="resetFilters">重置筛选</el-button>
        </div>
      </div>

      <!-- 中间：线索卡片列表 -->
      <div class="lead-list-panel">
        <div class="lead-list" v-loading="loading">
          <div
            v-for="lead in leads"
            :key="lead.id"
            class="lead-card"
            :class="{ selected: selectedLeadId === lead.id }"
            @click="openDrawer(lead)"
          >
            <div class="card-row card-top">
              <span class="user-name">{{ lead.nickname || '匿名' }}</span>
              <span class="card-time">{{ formatShortTime(lead.comment_time) }}</span>
            </div>
            <div class="card-comment">{{ lead.comment_content }}</div>
            <div class="card-article" v-if="lead.article_title">
              发表于：《{{ lead.article_title }}》
            </div>
            <div class="card-meta-row">
              <div class="meta-left">
                <el-tag v-if="lead.intent_type" size="small" effect="plain" :type="intentTagType(lead.intent_type)">
                  {{ intentLabel(lead.intent_type) }}
                  <template v-if="lead.intent_score != null">（{{ lead.intent_score }}%）</template>
                </el-tag>
                <el-tag v-if="lead.account_name" size="small" effect="plain" type="info">{{ lead.account_name }}</el-tag>
              </div>
              <div class="meta-right">
                <el-tag v-if="lead.lead_status === 'failed'" size="small" effect="dark" type="danger">缺少用户标识</el-tag>
                <template v-else>
                  <el-tag v-if="!lead.reply_content" size="small" effect="dark" type="warning">未回复</el-tag>
                  <el-tag v-else size="small" effect="dark" type="success">
                    {{ lead.reply_type === 'guide' ? '已引导' : '已回复' }}
                  </el-tag>
                  <el-tag v-if="isEligible(lead)" size="small" effect="dark" type="success">可发送</el-tag>
                  <el-tag v-else-if="lead.eligibility?.reason_code" size="small" effect="dark" type="info">不可发送</el-tag>
                </template>
              </div>
            </div>
            <div class="card-actions" @click.stop>
              <el-button size="small" type="primary" @click.stop="openReply(lead)">公开回复</el-button>
              <el-button size="small" @click.stop="handleSendContact(lead)">{{ sendButtonLabel(lead) }}</el-button>
              <el-dropdown trigger="click" @command="(cmd) => handleMoreAction(cmd, lead)">
                <el-button size="small" text><el-icon><MoreFilled /></el-icon></el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="close">关闭线索</el-dropdown-item>
                    <el-dropdown-item command="assign">分配负责人</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </div>
          <el-empty v-if="!loading && leads.length === 0" description="暂无线索" />
        </div>

        <div class="pagination-wrap" v-if="total > pageSize">
          <el-pagination
            v-model:current-page="page"
            :page-size="pageSize"
            :total="total"
            layout="prev, pager, next, total"
            small
            @current-change="fetchLeads"
          />
        </div>
      </div>
    </div>

    <!-- 右侧详情抽屉 -->
    <LeadDetailDrawer
      v-model:visible="drawerVisible"
      :lead-id="selectedLeadId"
      @reply="handleDrawerReply"
      @send-contact="handleDrawerSendContact"
      @close="handleDrawerClose"
    />

    <!-- 同步弹窗 -->
    <el-dialog v-model="showSyncDialog" title="同步评论" width="420px">
      <el-form :model="syncForm" label-width="80px">
        <el-form-item label="公众号" required>
          <el-select v-model="syncForm.account_id" placeholder="选择公众号" style="width:100%">
            <el-option v-for="a in accounts" :key="a.id" :label="a.name || a.app_id" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="同步范围">
          <el-radio-group v-model="syncForm.scope">
            <el-radio value="all">全部文章</el-radio>
            <el-radio value="article">单篇文章</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="syncForm.scope === 'article'" label="文章" required>
          <el-select v-model="syncForm.article_id" placeholder="选择文章" filterable style="width:100%">
            <el-option v-for="a in publishedArticles" :key="a.id" :label="a.main_title || a.topic || '无标题'" :value="a.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showSyncDialog = false">取消</el-button>
        <el-button type="primary" :loading="syncing" @click="handleSync">开始同步</el-button>
      </template>
    </el-dialog>

    <!-- 公开回复弹窗 -->
    <PublicReplyDialog
      v-model:visible="replyDialogVisible"
      :lead="replyTarget"
      @replied="onReplied"
    />

    <!-- 发送资料弹窗 -->
    <SendContactDialog
      v-model:visible="sendContactVisible"
      :lead-id="sendContactLeadId"
      @sent="onContactSent"
      @guide-reply="onSendContactGuideReply"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, MoreFilled } from '@element-plus/icons-vue'
import { listLeads, getQueueStats, syncLeads, getSyncJobStatus, closeLead } from '@/api/wechat'
import type { LeadItem } from '@/api/wechat'
import client from '@/api/client'
import LeadDetailDrawer from './components/LeadDetailDrawer.vue'
import PublicReplyDialog from './components/PublicReplyDialog.vue'
import SendContactDialog from './components/SendContactDialog.vue'

// --- State ---
const loading = ref(false)
const leads = ref<LeadItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const activeQueue = ref('all')
const stats = ref<Record<string, number>>({})
const accounts = ref<any[]>([])
const users = ref<any[]>([])
const publishedArticles = ref<any[]>([])

const searchKeyword = ref('')
const filterAccountId = ref<number | undefined>()
const filterArticleId = ref<number | undefined>()
const filterIntent = ref<string | undefined>()
const filterOperatorId = ref<number | undefined>()
const dateRange = ref<[Date, Date] | null>(null)

const selectedLeadId = ref<number | null>(null)
const drawerVisible = ref(false)

const lastSyncTime = ref<string | null>(null)
const lastSyncResult = ref<any>(null)

const showSyncDialog = ref(false)
const syncing = ref(false)
const syncForm = ref({ account_id: undefined as number | undefined, scope: 'all' as string, article_id: undefined as number | undefined })

const replyDialogVisible = ref(false)
const replyTarget = ref<LeadItem | null>(null)

const sendContactVisible = ref(false)
const sendContactLeadId = ref<number | null>(null)

// --- Stats cards ---
const statCards = [
  { key: 'pending_reply', label: '待回复', color: 'orange' },
  { key: 'eligible', label: '可发送', color: 'green' },
  { key: 'sent', label: '已发送', color: 'blue' },
  { key: 'awaiting_user', label: '待用户', color: 'purple' },
  { key: 'abnormal', label: '异常', color: 'red' },
]

const queues = [
  { key: 'all', label: '全部', type: 'info' as const },
  { key: 'pending_reply', label: '待回复', type: 'warning' as const },
  { key: 'mine', label: '我的', type: 'info' as const },
  { key: 'eligible', label: '可发送', type: 'success' as const },
  { key: 'awaiting_user', label: '待用户联系', type: 'warning' as const },
  { key: 'sent', label: '资料已发送', type: 'success' as const },
  { key: 'converted', label: '已转化', type: 'success' as const },
  { key: 'abnormal', label: '异常', type: 'danger' as const },
]

// --- Helpers ---
function formatTime(t?: string | null) {
  if (!t) return ''
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}

function formatShortTime(t?: string | null) {
  if (!t) return ''
  const d = new Date(t)
  const now = new Date()
  const isToday = d.toDateString() === now.toDateString()
  return isToday
    ? d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
    : `${d.getMonth() + 1}/${d.getDate()} ${d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })}`
}

function intentTagType(t: string) {
  const map: Record<string, string> = { purchase: 'success', price: 'warning', cooperation: 'primary', after_sale: 'danger', interaction: 'info', spam: '' }
  return map[t] || ''
}

function intentLabel(t: string) {
  const map: Record<string, string> = { purchase: '购买咨询', price: '价格咨询', cooperation: '合作咨询', after_sale: '售后投诉', interaction: '普通互动', spam: '垃圾内容' }
  return map[t] || t
}

function isEligible(lead: LeadItem) {
  return lead.eligibility?.eligible === true
}

function sendButtonLabel(lead: LeadItem) {
  if (lead.lead_status === 'failed') return '发送资料'
  const e = lead.eligibility
  if (e?.eligible) return '发送资料'
  if (e?.reason_code === 'NO_OPENID') return '发送资料'
  return '检查并发送'
}

// --- Data ---
async function fetchAccounts() {
  try {
    const res = await client.get('/accounts')
    const items = res.data?.items || res.data || []
    accounts.value = items.filter((a: any) => a.id != null)
  } catch { /* ignore */ }
}

async function fetchUsers() {
  try {
    const res = await client.get('/users')
    users.value = res.data?.items || res.data || []
  } catch { /* ignore */ }
}

async function fetchPublishedArticles() {
  try {
    const res = await client.get('/articles', { params: { status: 'published', page_size: 100 } })
    const items = res.data?.items || res.data || []
    publishedArticles.value = items.filter((a: any) => a.msg_data_id)
  } catch { /* ignore */ }
}

async function fetchQueueStats() {
  try {
    stats.value = await getQueueStats({ account_id: filterAccountId.value })
  } catch { /* ignore */ }
}

async function fetchLeads() {
  loading.value = true
  try {
    const res = await listLeads({
      queue: activeQueue.value,
      page: page.value,
      page_size: pageSize.value,
      account_id: filterAccountId.value,
      intent_type: filterIntent.value,
      operator_id: filterOperatorId.value,
      keyword: searchKeyword.value || undefined,
    })
    leads.value = res.items
    total.value = res.total
  } catch (e: any) {
    ElMessage.error('加载失败：' + (e.message || ''))
  } finally {
    loading.value = false
  }
}

async function refreshAll() {
  await Promise.all([fetchLeads(), fetchQueueStats()])
}

// --- Interactions ---
function switchQueue(key: string) {
  activeQueue.value = key
  page.value = 1
  fetchLeads()
}

function onFilterChange() {
  page.value = 1
  fetchLeads()
  fetchQueueStats()
}

function resetFilters() {
  filterAccountId.value = undefined
  filterArticleId.value = undefined
  filterIntent.value = undefined
  filterOperatorId.value = undefined
  dateRange.value = null
  searchKeyword.value = ''
  page.value = 1
  fetchLeads()
  fetchQueueStats()
}

function handleSearch() {
  page.value = 1
  fetchLeads()
}

function openDrawer(lead: LeadItem) {
  selectedLeadId.value = lead.id
  drawerVisible.value = true
}

function handleDrawerReply() {
  drawerVisible.value = false
  const lead = leads.value.find(l => l.id === selectedLeadId.value)
  if (lead) { replyTarget.value = lead; replyDialogVisible.value = true }
}

function handleDrawerSendContact() {
  drawerVisible.value = false
  sendContactLeadId.value = selectedLeadId.value
  sendContactVisible.value = true
}

async function handleDrawerClose() {
  drawerVisible.value = false
  await refreshAll()
}

// --- Sync ---
async function handleSync() {
  if (!syncForm.value.account_id) { ElMessage.warning('请选择公众号'); return }
  syncing.value = true
  try {
    const res = await syncLeads({ account_id: syncForm.value.account_id, scope: syncForm.value.scope, article_id: syncForm.value.article_id })
    showSyncDialog.value = false
    ElMessage.success('同步任务已创建，正在后台执行...')
    let timedOut = true
    const poll = setInterval(async () => {
      try {
        const job = await getSyncJobStatus(res.job_id)
        if (job.status === 'completed') {
          clearInterval(poll); timedOut = false
          lastSyncTime.value = job.completed_at
          lastSyncResult.value = job.result
          ElMessage.success(`同步完成：${job.result?.new_leads || 0} 条新线索`)
          await refreshAll()
        } else if (job.status === 'failed') {
          clearInterval(poll); timedOut = false
          ElMessage.error('同步失败：' + (job.error_message || ''))
        }
      } catch { /* retry */ }
    }, 2000)
    setTimeout(() => { if (timedOut) { clearInterval(poll); ElMessage.warning('同步超时，请稍后刷新查看') } }, 60000)
  } catch (e: any) {
    ElMessage.error('同步失败：' + (e.response?.data?.detail || e.message || ''))
  } finally {
    syncing.value = false
  }
}

// --- Reply ---
function openReply(lead: LeadItem) {
  replyTarget.value = lead
  replyDialogVisible.value = true
}

async function onReplied() {
  replyDialogVisible.value = false
  await refreshAll()
}

function handleSendContact(lead: LeadItem) {
  sendContactLeadId.value = lead.id
  sendContactVisible.value = true
}

async function onContactSent() {
  await refreshAll()
}

function onSendContactGuideReply() {
  const lead = leads.value.find(l => l.id === sendContactLeadId.value)
  if (lead) {
    replyTarget.value = lead
    replyDialogVisible.value = true
  }
}

function handleMoreAction(cmd: string, lead: LeadItem) {
  if (cmd === 'close') handleClose(lead)
  if (cmd === 'assign') ElMessage.info('分配功能将在后续版本上线')
}

async function handleClose(lead: LeadItem) {
  try {
    await ElMessageBox.confirm('确认关闭此线索？', '确认')
    await closeLead(lead.id)
    ElMessage.success('已关闭')
    await refreshAll()
  } catch { /* cancelled */ }
}

onMounted(async () => {
  await Promise.all([fetchAccounts(), fetchUsers(), fetchPublishedArticles()])
  await refreshAll()
})
</script>

<style scoped>
.lead-workbench { display: flex; flex-direction: column; height: 100%; }

/* Top bar */
.top-bar {
  display: flex; justify-content: space-between; align-items: center;
  padding-bottom: 12px; border-bottom: 1px solid #e4e7ed; margin-bottom: 12px;
}
.search-area { display: flex; align-items: center; gap: 8px; }
.top-actions { display: flex; align-items: center; gap: 12px; }
.sync-status { font-size: 12px; color: #909399; white-space: nowrap; }

/* Stats row */
.stats-row { display: flex; gap: 12px; margin-bottom: 16px; }
.stat-card {
  flex: 1; background: #fff; border: 1px solid #ebeef5;
  border-radius: 8px; padding: 12px 16px; cursor: pointer;
  transition: all 0.15s; text-align: center;
}
.stat-card:hover { border-color: #409eff; box-shadow: 0 1px 4px rgba(64,158,255,0.1); }
.stat-value { font-size: 24px; font-weight: 700; line-height: 1.3; }
.stat-value.orange { color: #e6a23c; }
.stat-value.green { color: #67c23a; }
.stat-value.blue { color: #409eff; }
.stat-value.purple { color: #b37feb; }
.stat-value.red { color: #f56c6c; }
.stat-label { font-size: 12px; color: #909399; margin-top: 2px; }

/* Body */
.workbench-body { display: flex; flex: 1; gap: 16px; overflow: hidden; }

/* Left panel */
.left-panel { width: 200px; flex-shrink: 0; overflow-y: auto; }
.queue-title { font-size: 13px; font-weight: 600; color: #303133; margin-bottom: 6px; padding: 0 4px; }
.queue-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 6px 10px; border-radius: 6px; cursor: pointer; font-size: 13px;
  transition: background 0.15s; margin-bottom: 1px;
}
.queue-item:hover { background: #f0f5ff; }
.queue-item.active { background: #ecf5ff; color: #409eff; font-weight: 500; }
.filter-section { display: flex; flex-direction: column; gap: 8px; }
.filter-group label { display: block; font-size: 12px; color: #909399; margin-bottom: 3px; }

/* Card List */
.lead-list-panel { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.lead-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }

.lead-card {
  background: #fff; border: 1px solid #ebeef5; border-radius: 8px;
  padding: 12px 16px; cursor: pointer; transition: all 0.15s;
}
.lead-card:hover { border-color: #409eff; }
.lead-card.selected { border-color: #409eff; background: #f0f8ff; }

.card-row { display: flex; justify-content: space-between; align-items: center; }
.card-top { margin-bottom: 4px; }
.user-name { font-weight: 600; font-size: 14px; color: #303133; }
.card-time { font-size: 12px; color: #909399; }
.card-comment { font-size: 14px; color: #303133; margin-bottom: 4px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.card-article { font-size: 12px; color: #909399; margin-bottom: 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card-meta-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; gap: 6px; flex-wrap: wrap; }
.meta-left, .meta-right { display: flex; gap: 4px; align-items: center; flex-wrap: wrap; }
.card-actions { display: flex; gap: 6px; align-items: center; border-top: 1px solid #f0f0f0; padding-top: 8px; }

.pagination-wrap { display: flex; justify-content: center; padding: 12px 0 0 0; }
.placeholder-text { color: #909399; font-size: 13px; text-align: center; padding: 24px; }
</style>

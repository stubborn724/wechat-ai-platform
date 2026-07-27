<template>
  <el-drawer :model-value="visible" title="线索详情" size="480px" @close="handleClose">
    <div v-loading="loading" class="drawer-body">

      <!-- 线索状态 -->
      <div class="status-bar">
        <span class="status-dot" :style="{ background: statusColor }"></span>
        <span class="status-text">{{ statusLabel }}</span>
      </div>

      <!-- 用户信息卡片 -->
      <div class="info-card">
        <div class="info-row"><span class="info-label">昵称</span><span class="info-value">{{ data?.nickname || '-' }}</span></div>
        <div class="info-row"><span class="info-label">公众号</span><span class="info-value">{{ data?.account_name || '-' }}</span></div>
        <div class="info-row"><span class="info-label">来源文章</span><span class="info-value article-link">{{ data?.article_title || '-' }}</span></div>
        <div class="info-row"><span class="info-label">评论时间</span><span class="info-value">{{ formatTime(data?.comment_time) }}</span></div>
        <div class="info-row" v-if="data?.openid"><span class="info-label">OpenID</span><span class="info-value code">{{ data.openid }}</span></div>
      </div>

      <!-- 对话区 -->
      <div class="chat-section">
        <div class="chat-bubble left">
          <div class="bubble-role">用户评论</div>
          <div class="bubble-content">{{ data?.comment_content || '-' }}</div>
          <div class="bubble-time">{{ formatTime(data?.comment_time) }}</div>
        </div>
        <div v-if="data?.reply_content" class="chat-bubble right">
          <div class="bubble-role">
            官方回复
            <el-tag v-if="data?.reply_type === 'guide'" size="small" type="warning" style="margin-left:6px">引导回复</el-tag>
          </div>
          <div class="bubble-content">{{ data.reply_content }}</div>
          <div class="bubble-time">{{ formatTime(data?.replied_at) }}</div>
        </div>
        <div v-else class="no-reply">尚未公开回复</div>
      </div>

      <!-- 意图 -->
      <div class="info-card" v-if="data?.intent_type">
        <div class="info-row">
          <span class="info-label">意图分析</span>
          <span class="info-value">
            <el-tag size="small" :type="intentTagType(data.intent_type)">{{ intentLabel(data.intent_type) }}</el-tag>
            <span v-if="data?.intent_score != null" style="margin-left:6px;font-size:13px;color:#606266">{{ data.intent_score }}% 置信度</span>
          </span>
        </div>
      </div>

      <!-- 私信资格 -->
      <div class="info-card">
        <div class="info-row">
          <span class="info-label">私信资格</span>
          <span class="info-value">
            <template v-if="data?.eligibility">
              <el-tag :type="data.eligibility.eligible ? 'success' : 'danger'" size="small">
                {{ data.eligibility.eligible ? '可发送' : '不可发送' }}
              </el-tag>
              <span style="margin-left:6px;font-size:12px;color:#909399">{{ data.eligibility.reason_text || '' }}</span>
            </template>
            <span v-else class="unknown-tag">待检查</span>
          </span>
        </div>
      </div>

      <!-- 引导关键词 -->
      <div class="info-card" v-if="data?.guide_keyword">
        <div class="info-row">
          <span class="info-label">Guide keyword</span>
          <span class="info-value">
            <el-tag size="small" type="warning">{{ data.guide_keyword }}</el-tag>
            <span v-if="data.auto_send_on_message" style="margin-left:6px;font-size:12px;color:#67c23a">
              Auto-send: ON
            </span>
          </span>
        </div>
      </div>

      <!-- 发送记录 -->
      <div class="info-card" v-if="deliveries.length > 0">
        <div class="section-title">发送记录</div>
        <div v-for="d in deliveries" :key="d.id" class="delivery-item">
          <div class="delivery-header">
            <el-tag size="small" :type="deliveryStatusType(d.status)" effect="dark">{{ deliveryStatusLabel(d.status) }}</el-tag>
            <span class="delivery-time">{{ formatTime(d.created_at) }}</span>
          </div>
          <div class="delivery-steps">
            <span class="step">文字: <el-tag size="small" :type="d.text_status === 'success' ? 'success' : d.text_status === 'failed' ? 'danger' : 'info'">{{ d.text_status || '-' }}</el-tag></span>
            <span class="step">二维码: <el-tag size="small" :type="d.qr_status === 'success' ? 'success' : d.qr_status === 'failed' ? 'danger' : 'info'">{{ d.qr_status || '-' }}</el-tag></span>
            <el-button v-if="d.status === 'partial_failed' || d.status === 'failed'" size="small" text type="primary" @click="handleRetry(d)">重试</el-button>
          </div>
          <div v-if="d.text_error_message" class="delivery-error">文字: {{ d.text_error_message }}</div>
          <div v-if="d.qr_error_message && d.qr_error_message !== d.text_error_message" class="delivery-error">二维码: {{ d.qr_error_message }}</div>
        </div>
      </div>

      <!-- 操作区 -->
      <div class="action-area">
        <el-button type="primary" size="default" @click="$emit('reply')">公开回复</el-button>
        <el-dropdown trigger="click" @command="handleCommand">
          <el-button size="default">
            更多 <el-icon><ArrowDown /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="send">发送资料</el-dropdown-item>
              <el-dropdown-item command="assign">分配负责人</el-dropdown-item>
              <el-dropdown-item command="close" divided>关闭线索</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>

      <!-- 跟进记录 -->
      <div class="timeline-section">
        <div class="section-title">跟进记录</div>
        <div class="timeline">
          <div class="timeline-item" v-for="ev in timeline" :key="ev.id">
            <div class="timeline-dot" :style="{ background: ev.color || '#c0c4cc' }"></div>
            <div class="timeline-content">
              <div class="timeline-text">{{ ev.text }}</div>
              <div class="timeline-time">{{ formatTime(ev.time) }}</div>
            </div>
          </div>
          <div v-if="timeline.length === 0" class="no-data">暂无记录</div>
        </div>
      </div>

    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown } from '@element-plus/icons-vue'
import { getLead, closeLead, listLeadDeliveries, retryDelivery } from '@/api/wechat'

const props = defineProps<{ visible: boolean; leadId: number | null }>()
const emit = defineEmits<{
  'update:visible': [value: boolean]
  reply: []
  'send-contact': []
  close: []
}>()

const loading = ref(false)
const data = ref<any>(null)
const timeline = ref<any[]>([])
const deliveries = ref<any[]>([])

// --- Computed ---
const canSend = computed(() => data.value?.eligibility?.eligible === true)

function deliveryStatusType(s: string) {
  const m: Record<string, string> = { pending: 'info', checking_eligibility: 'warning', preparing_media: 'warning', sending_text: 'warning', sending_qr: 'warning', success: 'success', partial_failed: 'danger', failed: 'danger', ineligible: 'info' }
  return m[s] || 'info'
}
function deliveryStatusLabel(s: string) {
  const m: Record<string, string> = { pending: '待发送', checking_eligibility: '检查资格', preparing_media: '准备素材', sending_text: '发送文字', sending_qr: '发送二维码', success: '发送成功', partial_failed: '部分失败', failed: '发送失败', ineligible: '无资格' }
  return m[s] || s
}

const statusLabel = computed(() => {
  const m: Record<string, string> = {
    pending_reply: '待回复', awaiting_user: '等待用户联系',
    eligible: '可发送', contact_sent: '资料已发送',
    converted: '已转化', closed: '已关闭',
    failed: '缺少用户标识',
  }
  return m[data.value?.status || ''] || data.value?.status || '未知'
})

const statusColor = computed(() => {
  const m: Record<string, string> = {
    pending_reply: '#e6a23c', awaiting_user: '#b37feb',
    eligible: '#67c23a', contact_sent: '#409eff',
    converted: '#67c23a', closed: '#909399',
    failed: '#f56c6c',
  }
  return m[data.value?.status || ''] || '#c0c4cc'
})

// --- Helpers ---
function formatTime(t?: string | null) {
  if (!t) return ''
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}

function intentTagType(t: string) {
  const map: Record<string, string> = { purchase: 'success', price: 'warning', cooperation: 'primary', after_sale: 'danger', interaction: 'info', spam: '' }
  return map[t] || ''
}

function intentLabel(t: string) {
  const map: Record<string, string> = { purchase: '购买咨询', price: '价格咨询', cooperation: '合作咨询', after_sale: '售后投诉', interaction: '普通互动', spam: '垃圾内容' }
  return map[t] || t
}

// --- Fetch ---
async function fetchDetail() {
  if (!props.leadId) return
  loading.value = true
  try {
    const [res, dels] = await Promise.all([
      getLead(props.leadId),
      listLeadDeliveries(props.leadId),
    ])
    data.value = res
    deliveries.value = dels

    // 构建时间线
    const items: any[] = []
    if (res.created_at) items.push({ id: 1, text: '评论同步', time: res.created_at, color: '#909399' })
    if (res.replied_at) items.push({ id: 2, text: res.reply_type === 'guide' ? '公开回复（引导）' : '公开回复', time: res.replied_at, color: '#409eff' })
    if (res.status === 'awaiting_user') items.push({ id: 3, text: '等待用户消息', time: res.updated_at, color: '#b37feb' })
    if (res.status === 'contact_sent') items.push({ id: 4, text: '资料已发送', time: res.updated_at, color: '#67c23a' })
    if (res.status === 'converted') items.push({ id: 5, text: '已转化', time: res.updated_at, color: '#67c23a' })
    if (res.status === 'closed') items.push({ id: 6, text: '已关闭', time: res.updated_at, color: '#909399' })
    if (res.status === 'failed') items.push({ id: 7, text: '异常：缺少用户标识', time: res.created_at, color: '#f56c6c' })
    // delivery 事件
    for (const d of dels) {
      if (d.status === 'success') items.push({ id: 100 + d.id, text: '资料发送成功', time: d.completed_at || d.created_at, color: '#67c23a' })
      else if (d.status === 'partial_failed') items.push({ id: 100 + d.id, text: '资料发送部分失败', time: d.completed_at || d.created_at, color: '#e6a23c' })
      else if (d.status === 'failed') items.push({ id: 100 + d.id, text: '资料发送失败', time: d.completed_at || d.created_at, color: '#f56c6c' })
      else items.push({ id: 100 + d.id, text: `发送任务已创建（${d.status}）`, time: d.created_at, color: '#909399' })
    }
    timeline.value = items.sort((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime())
  } catch {
    ElMessage.error('加载详情失败')
  } finally {
    loading.value = false
  }
}

async function handleRetry(d: any) {
  if (!d.id) return
  try {
    await ElMessageBox.confirm('确认重试此发送任务？将重新检查资格并重试失败步骤。', '确认')
    const uuid = crypto.randomUUID?.() || `${Date.now()}`
    await retryDelivery(d.id, { step: d.qr_status === 'failed' ? 'qr' : 'all', idempotency_key: `retry_${d.id}_${uuid}` })
    ElMessage.success('重试任务已创建')
    fetchDetail()
  } catch { /* cancelled */ }
}

watch(() => props.leadId, (id) => { if (id) fetchDetail() })

// --- Events ---
function handleCommand(cmd: string) {
  if (cmd === 'send') emit('send-contact')
  if (cmd === 'assign') ElMessage.info('分配功能将在后续版本上线')
  if (cmd === 'close') handleCloseLead()
}

async function handleCloseLead() {
  if (!props.leadId) return
  try {
    await ElMessageBox.confirm('确认关闭此线索？', '确认')
    await closeLead(props.leadId)
    ElMessage.success('已关闭')
    emit('close')
  } catch { /* cancelled */ }
}

function handleClose() {
  emit('update:visible', false)
}
</script>

<style scoped>
.drawer-body { display: flex; flex-direction: column; gap: 12px; }

/* Status */
.status-bar { display: flex; align-items: center; gap: 8px; padding-bottom: 8px; border-bottom: 2px solid #f0f0f0; }
.status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.status-text { font-size: 15px; font-weight: 600; color: #303133; }

/* Info card */
.info-card { background: #fafafa; border-radius: 8px; padding: 10px 14px; }
.info-row { display: flex; padding: 4px 0; font-size: 13px; }
.info-label { width: 70px; flex-shrink: 0; color: #909399; }
.info-value { color: #303133; flex: 1; word-break: break-all; }
.article-link { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.code { font-family: monospace; font-size: 12px; }
.unknown-tag { color: #c0c4cc; font-style: italic; }

/* Chat */
.chat-section { display: flex; flex-direction: column; gap: 8px; }
.chat-bubble { max-width: 85%; padding: 10px 14px; border-radius: 12px; position: relative; }
.chat-bubble.left { align-self: flex-start; background: #f0f0f0; border-bottom-left-radius: 4px; }
.chat-bubble.right { align-self: flex-end; background: #ecf5ff; border-bottom-right-radius: 4px; }
.bubble-role { font-size: 11px; color: #909399; margin-bottom: 4px; }
.bubble-content { font-size: 14px; color: #303133; white-space: pre-wrap; line-height: 1.5; }
.bubble-time { font-size: 11px; color: #c0c4cc; margin-top: 4px; text-align: right; }
.no-reply { font-size: 13px; color: #c0c4cc; text-align: center; padding: 12px; }

/* Actions */
.action-area { display: flex; gap: 8px; align-items: center; padding: 8px 0; }

/* Timeline */
.timeline-section { }
.section-title { font-size: 13px; font-weight: 600; color: #303133; margin-bottom: 8px; }
.timeline { position: relative; padding-left: 20px; }
.timeline::before { content: ''; position: absolute; left: 6px; top: 4px; bottom: 4px; width: 2px; background: #e4e7ed; }
.timeline-item { position: relative; padding-bottom: 14px; }
.timeline-dot { position: absolute; left: -17px; top: 5px; width: 8px; height: 8px; border-radius: 50%; }
.timeline-text { font-size: 13px; color: #303133; }
.timeline-time { font-size: 11px; color: #c0c4cc; margin-top: 2px; }
.no-data { font-size: 13px; color: #c0c4cc; text-align: center; padding: 12px; }
</style>

<template>
  <el-dialog :model-value="visible" title="公开回复" width="560px" @close="handleClose">
    <div v-if="lead">
      <div class="original-comment">
        <div class="comment-header">
          <strong>{{ lead.nickname || 'User' }}</strong>
          <span>{{ formatTime(lead.comment_time) }}</span>
        </div>
        <div class="comment-text">{{ lead.comment_content }}</div>
      </div>

      <div class="reply-type-selector">
        <span class="type-label">Reply type:</span>
        <el-radio-group v-model="replyType">
          <el-radio value="normal">Normal</el-radio>
          <el-radio value="guide">Guide with keyword</el-radio>
        </el-radio-group>
      </div>

      <div class="reply-content-area">
        <div class="toolbar">
          <el-button size="small" @click="handleAiGenerate" :loading="generating">AI Generate</el-button>
          <el-button size="small" @click="handleUseTemplate">Template</el-button>
        </div>
        <el-input v-model="content" type="textarea" :rows="5" placeholder="Enter reply content..." />
      </div>

      <!-- Guide options -->
      <div v-if="replyType === 'guide'" class="guide-options">
        <el-form label-width="160px">
          <el-form-item label="Guide keyword">
            <el-input v-model="guideKeyword" placeholder="e.g.: tea table" />
            <div class="form-tip">User must send this exact keyword to trigger auto-send</div>
          </el-form-item>
          <el-form-item label="Auto-send on keyword">
            <el-switch v-model="autoSendOnMessage" />
          </el-form-item>
          <el-form-item v-if="autoSendOnMessage" label="Contact package">
            <el-select v-model="autoSendPackageId" placeholder="Select package" style="width:100%">
              <el-option v-for="p in contactPackages" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
          </el-form-item>
        </el-form>
      </div>
    </div>

    <template #footer>
      <el-button @click="visible = false">Cancel</el-button>
      <el-button type="primary" :loading="submitting" :disabled="!content.trim()" @click="handleSubmit">
        Submit Reply
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { publicReply, generateReply, listContactPackages } from '@/api/wechat'
import type { LeadItem } from '@/api/wechat'

const props = defineProps<{ visible: boolean; lead: LeadItem | null }>()
const emit = defineEmits<{ 'update:visible': [value: boolean]; replied: [] }>()

const replyType = ref('normal')
const content = ref('')
const submitting = ref(false)
const generating = ref(false)
const guideKeyword = ref('')
const autoSendOnMessage = ref(true)
const autoSendPackageId = ref<number | undefined>()
const contactPackages = ref<any[]>([])

function formatTime(t?: string | null) {
  if (!t) return ''
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}

watch(() => props.visible, async (v) => {
  if (v) {
    replyType.value = 'normal'
    content.value = ''
    guideKeyword.value = ''
    autoSendOnMessage.value = true
    autoSendPackageId.value = undefined
    try {
      const res = await listContactPackages({ enabled: true, page_size: 50 })
      contactPackages.value = res.items
      const defPkg = res.items.find((p: any) => p.is_default)
      if (defPkg) autoSendPackageId.value = defPkg.id
    } catch { /* ignore */ }
  }
})

async function handleAiGenerate() {
  if (!props.lead) return
  generating.value = true
  try {
    const res = await generateReply(props.lead.id, {
      reply_type: replyType.value,
      keyword: guideKeyword.value || props.lead.comment_content?.slice(0, 10) || 'details',
    })
    content.value = res.content
  } catch (e: any) {
    ElMessage.error('Generation failed: ' + (e.message || ''))
  } finally {
    generating.value = false
  }
}

function handleUseTemplate() {
  const templates: Record<string, string> = {
    purchase: 'Thank you for your interest! Please reply with the keyword in the official account to receive detailed information.',
    price: 'Thank you! For pricing details, please reply with the keyword in the official account to get the latest quote.',
    cooperation: 'Thank you for your interest in cooperation! Please reply "cooperation" in the official account, and our team will contact you.',
    after_sale: 'Thank you for your feedback. Please describe your issue in the official account dialog, and our customer service will assist you.',
    default: 'Thank you for your message! For more information, please reply with the keyword in the official account.',
  }
  const t = props.lead?.intent_type || 'default'
  content.value = templates[t] || templates.default
}

async function handleSubmit() {
  if (!props.lead || !content.value.trim()) return
  submitting.value = true
  try {
    const payload: any = { reply_type: replyType.value, content: content.value.trim() }
    if (replyType.value === 'guide') {
      payload.guide_keyword = guideKeyword.value || undefined
      payload.auto_send_on_message = autoSendOnMessage.value
      payload.auto_send_package_id = autoSendPackageId.value || undefined
    }
    const res = await publicReply(props.lead.id, payload)
    if (res.wechat_synced === false) {
      ElMessage.warning('Reply saved locally, but sync to WeChat failed: ' + (res.wechat_error || ''))
    } else {
      ElMessage.success('Reply sent')
    }
    emit('replied')
  } catch (e: any) {
    ElMessage.error('Reply failed: ' + (e.response?.data?.detail || e.message || ''))
  } finally {
    submitting.value = false
  }
}

function handleClose() { emit('update:visible', false) }
</script>

<style scoped>
.original-comment { background: #f5f7fa; border-radius: 8px; padding: 12px; margin-bottom: 16px; }
.comment-header { display: flex; justify-content: space-between; margin-bottom: 6px; font-size: 13px; color: #606266; }
.comment-text { font-size: 14px; color: #303133; }
.reply-type-selector { margin-bottom: 12px; }
.type-label { font-size: 13px; color: #606266; margin-right: 8px; }
.reply-content-area { margin-bottom: 12px; }
.toolbar { margin-bottom: 8px; display: flex; gap: 8px; }
.guide-options { background: #f9f9f9; border-radius: 8px; padding: 12px; margin-top: 8px; }
.form-tip { font-size: 11px; color: #909399; margin-top: 4px; }
</style>

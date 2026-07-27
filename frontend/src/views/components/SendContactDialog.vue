<template>
  <el-dialog :model-value="visible" title="发送联系资料" width="520px" @close="handleClose">
    <!-- 检查中 -->
    <div v-if="checking" class="status-center">
      <el-icon class="is-loading" size="24"><Loading /></el-icon>
      <p>正在检查发送资格…</p>
    </div>

    <!-- eligible -->
    <div v-else-if="eligibility?.status === 'eligible'">
      <div class="eligibility-banner eligible">
        <el-tag type="success" size="small">可发送</el-tag>
        <span class="reason">{{ eligibility.reason_text }}</span>
      </div>
      <el-form label-width="80px">
        <el-form-item label="选择资料包">
          <el-select v-model="selectedPackageId" placeholder="选择资料包" style="width:100%">
            <el-option v-for="p in packages" :key="p.id" :label="p.name" :value="p.id">
              <div class="pkg-option">
                <span>{{ p.name }}</span>
                <el-tag v-if="p.is_default" size="small" type="success">默认</el-tag>
              </div>
              <div class="pkg-preview" v-if="p.text_content">{{ p.text_content.slice(0, 50) }}</div>
            </el-option>
          </el-select>
        </el-form-item>
      </el-form>
      <div v-if="selectedPackage" class="package-preview">
        <div class="preview-title">资料包预览</div>
        <div class="preview-content">
          <div v-if="selectedPackage.contact_name">联系人：{{ selectedPackage.contact_name }}</div>
          <div v-if="selectedPackage.wechat_id">微信号：{{ selectedPackage.wechat_id }}</div>
          <div v-if="selectedPackage.phone">电话：{{ selectedPackage.phone }}</div>
          <div v-if="selectedPackage.text_content">文案：{{ selectedPackage.text_content }}</div>
        </div>
      </div>
      <div class="dialog-footer">
        <el-button @click="handleClose">取消</el-button>
        <el-button type="primary" :loading="sending" :disabled="!selectedPackageId" @click="handleSend">
          确认发送
        </el-button>
      </div>
    </div>

    <!-- ineligible -->
    <div v-else-if="eligibility?.status === 'ineligible'">
      <div class="eligibility-banner ineligible">
        <el-tag type="danger" size="small">不可发送</el-tag>
        <span class="reason">{{ eligibility.reason_text }}</span>
      </div>
      <p class="suggestion">建议：使用引导回复让用户主动发送消息后，再发送资料。</p>
      <div class="dialog-footer">
        <el-button @click="handleClose">取消</el-button>
        <el-button type="warning" @click="handleGuideReply">去引导回复</el-button>
      </div>
    </div>

    <!-- unknown -->
    <div v-else>
      <div class="eligibility-banner unknown">
        <el-tag type="info" size="small">无法确认</el-tag>
        <span class="reason">{{ eligibility?.reason_text || '当前无法判断私信资格' }}</span>
      </div>
      <p class="suggestion">微信接口暂时无法确认该用户的私信资格，请稍后重试。</p>
      <div class="dialog-footer">
        <el-button @click="handleClose">取消</el-button>
        <el-button type="primary" :loading="checking" @click="checkEligibility">重新检查</el-button>
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { checkEligibility as apiCheckEligibility, createDelivery, listContactPackages } from '@/api/wechat'
import type { ContactPackage } from '@/api/wechat'

const props = defineProps<{
  visible: boolean
  leadId: number | null
  accountId?: number
}>()
const emit = defineEmits<{
  'update:visible': [value: boolean]
  'guide-reply': []
  sent: []
}>()

const checking = ref(true)
const eligibility = ref<any>(null)
const packages = ref<ContactPackage[]>([])
const selectedPackageId = ref<number | undefined>()
const sending = ref(false)

const selectedPackage = computed(() => packages.value.find(p => p.id === selectedPackageId.value))

async function checkEligibility() {
  if (!props.leadId) return
  checking.value = true
  try {
    const res = await apiCheckEligibility(props.leadId)
    eligibility.value = res
    if (res.status === 'eligible') {
      await loadPackages()
    }
  } catch {
    eligibility.value = { status: 'unknown', reason_text: '接口调用失败' }
  } finally {
    checking.value = false
  }
}

async function loadPackages() {
  try {
    const res = await listContactPackages({ enabled: true, page_size: 50 })
    packages.value = res.items
    const def = res.items.find(p => p.is_default)
    if (def) selectedPackageId.value = def.id
  } catch { /* ignore */ }
}

watch(() => props.visible, (v) => {
  if (v) {
    selectedPackageId.value = undefined
    eligibility.value = null
    checkEligibility()
  }
})

async function handleSend() {
  if (!props.leadId || !selectedPackageId.value) return
  sending.value = true
  try {
    const uuid = crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`
    await createDelivery(props.leadId, {
      package_id: selectedPackageId.value,
      idempotency_key: uuid,
    })
    ElMessage.success('发送任务已创建，可在详情中查看进度')
    emit('sent')
    emit('update:visible', false)
  } catch (e: any) {
    ElMessage.error('创建失败：' + (e.response?.data?.detail || e.message || ''))
  } finally {
    sending.value = false
  }
}

function handleGuideReply() {
  emit('guide-reply')
  emit('update:visible', false)
}

function handleClose() {
  emit('update:visible', false)
}
</script>

<style scoped>
.status-center { text-align: center; padding: 40px; color: #909399; }
.eligibility-banner { display: flex; align-items: center; gap: 8px; padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; }
.eligibility-banner.eligible { background: #f0f9eb; }
.eligibility-banner.ineligible { background: #fef0f0; }
.eligibility-banner.unknown { background: #f4f4f5; }
.reason { font-size: 13px; color: #606266; }
.suggestion { font-size: 13px; color: #909399; padding: 8px 0; }
.package-preview { background: #fafafa; border-radius: 6px; padding: 10px 14px; margin-bottom: 12px; }
.preview-title { font-size: 12px; color: #909399; margin-bottom: 6px; }
.preview-content { font-size: 13px; color: #303133; line-height: 1.8; }
.pkg-option { display: flex; align-items: center; gap: 6px; }
.pkg-preview { font-size: 11px; color: #909399; }
.dialog-footer { display: flex; justify-content: flex-end; gap: 8px; padding-top: 12px; }
</style>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import type { Account, PublishPlan, ArticleSlot } from '@/api/types'

const loading = ref(false)
const saving = ref(false)
const plans = ref<PublishPlan[]>([])
const accounts = ref<Account[]>([])
const showForm = ref(false)
const editing = ref(false)
const currentId = ref<string | null>(null)

const form = reactive({
  account_id: '',
  day_of_week: null as number | null,
  article_slots: [] as ArticleSlot[],
  publish_times: [] as string[],
  public_count: 1,
  private_count: 0,
})

const dayLabels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const dayOptions = [
  { value: null, label: '每天' },
  ...dayLabels.map((label, i) => ({ value: i, label })),
]

const contentTypeOptions = [
  { value: 'image_text', label: '图文' },
  { value: 'video', label: '视频' },
  { value: 'pure_image', label: '纯图片' },
]

const domainOptions = [
  { value: 'public', label: '公域' },
  { value: 'private', label: '私域' },
]

async function load() {
  loading.value = true
  try {
    const [plansRes, accountsRes] = await Promise.all([
      client.get('/publish-plans'),
      client.get<{ items: Account[] }>('/accounts'),
    ])
    plans.value = (plansRes.data as any)?.items || plansRes.data || []
    accounts.value = accountsRes.data.items || []
  } catch {
    ElMessage.error('加载发布计划失败')
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.account_id = ''
  form.day_of_week = null
  form.article_slots = []
  form.publish_times = []
  form.public_count = 1
  form.private_count = 0
  editing.value = false
  currentId.value = null
}

function openCreate() {
  resetForm()
  showForm.value = true
}

async function openEdit(plan: PublishPlan) {
  editing.value = true
  currentId.value = plan.id
  form.account_id = plan.account_id
  form.day_of_week = plan.day_of_week
  form.article_slots = [...plan.article_slots]
  form.publish_times = [...plan.publish_times]
  form.public_count = plan.public_count
  form.private_count = plan.private_count
  showForm.value = true
}

function addSlot() {
  form.article_slots.push({
    content_type: 'image_text',
    sort_order: form.article_slots.length,
    publish_domain: 'public',
  })
}

function removeSlot(index: number) {
  form.article_slots.splice(index, 1)
  form.article_slots.forEach((s, i) => { s.sort_order = i })
}

function addTime() {
  form.publish_times.push('08:00')
}

function removeTime(index: number) {
  form.publish_times.splice(index, 1)
}

async function save() {
  saving.value = true
  try {
    const payload = {
      account_id: form.account_id,
      day_of_week: form.day_of_week,
      article_slots: form.article_slots,
      publish_times: form.publish_times,
      public_count: form.public_count,
      private_count: form.private_count,
    }

    if (editing.value && currentId.value) {
      await client.patch(`/publish-plans/${currentId.value}`, payload)
      ElMessage.success('计划已更新')
    } else {
      await client.post('/publish-plans', payload)
      ElMessage.success('计划已创建')
    }
    showForm.value = false
    await load()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function confirmDelete(plan: PublishPlan) {
  try {
    await ElMessageBox.confirm('确定删除此发布计划吗？', '确认删除')
    await client.delete(`/publish-plans/${plan.id}`)
    ElMessage.success('已删除')
    await load()
  } catch {
    // cancelled
  }
}

function getDayLabel(d: number | null): string {
  if (d === null) return '每天'
  return dayLabels[d] || '未知'
}

function getAccountName(accountId: string): string {
  return accounts.value.find(a => a.id.toString() === accountId)?.name || accountId.slice(0, 8)
}

const contentTypeLabels: Record<string, string> = {
  image_text: '图文',
  video: '视频',
  pure_image: '纯图片',
}

onMounted(load)
</script>

<template>
  <div class="plans-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">PUBLISH SCHEDULE</p>
        <h1>发布计划</h1>
        <p class="lead">配置定时发布计划，AI 按计划自动执行。</p>
      </div>
      <el-button type="primary" @click="openCreate">+ 新建计划</el-button>
    </div>

    <div v-if="loading" class="loading-section">
      <el-skeleton :rows="3" animated />
    </div>

    <div v-else-if="plans.length === 0" class="empty-state">
      <el-empty description="暂无发布计划">
        <el-button type="primary" @click="openCreate">创建第一个计划</el-button>
      </el-empty>
    </div>

    <div v-else class="plan-grid">
      <div v-for="plan in plans" :key="plan.id" class="plan-card">
        <div class="card-header">
          <div class="card-title">
            {{ getAccountName(plan.account_id) }}
            <el-tag size="small" type="primary">{{ getDayLabel(plan.day_of_week) }}</el-tag>
          </div>
          <el-tag :type="plan.is_active ? 'success' : 'info'" size="small">
            {{ plan.is_active ? '启用' : '停用' }}
          </el-tag>
        </div>

        <div class="card-body">
          <div class="section-title">文章槽 ({{ plan.article_slots.length }})</div>
          <div v-for="(slot, i) in plan.article_slots" :key="i" class="slot-item">
            <span class="slot-order">#{{ i + 1 }}</span>
            <el-tag size="small">{{ contentTypeLabels[slot.content_type] || slot.content_type }}</el-tag>
            <el-tag size="small" :type="slot.publish_domain === 'public' ? '' : 'warning'">
              {{ slot.publish_domain === 'public' ? '公域' : '私域' }}
            </el-tag>
          </div>

          <div class="section-title">发布时间</div>
          <div class="time-list">
            <el-tag v-for="(t, i) in plan.publish_times" :key="i" size="small" type="info">
              {{ t }}
            </el-tag>
            <span v-if="plan.publish_times.length === 0" class="muted">未设置</span>
          </div>

          <div class="count-row">
            <span>公域: <strong>{{ plan.public_count }}</strong></span>
            <span>私域: <strong>{{ plan.private_count }}</strong></span>
          </div>
        </div>

        <div class="card-actions">
          <el-button size="small" @click="openEdit(plan)">编辑</el-button>
          <el-button size="small" type="danger" plain @click="confirmDelete(plan)">删除</el-button>
        </div>
      </div>
    </div>

    <!-- Create/Edit Dialog -->
    <el-dialog
      v-model="showForm"
      :title="editing ? '编辑发布计划' : '新建发布计划'"
      width="560px"
    >
      <el-form label-position="top">
        <el-form-item label="公众号" required>
          <el-select v-model="form.account_id" style="width: 100%" filterable>
            <el-option
              v-for="acct in accounts"
              :key="acct.id"
              :value="acct.id.toString()"
              :label="acct.name"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="适用星期">
          <el-select v-model="form.day_of_week" style="width: 100%">
            <el-option v-for="opt in dayOptions" :key="String(opt.value)" :value="opt.value" :label="opt.label" />
          </el-select>
        </el-form-item>

        <el-form-item label="文章槽">
          <div class="slots-container">
            <div v-for="(slot, i) in form.article_slots" :key="i" class="slot-row">
              <span class="slot-index">#{{ i + 1 }}</span>
              <el-select v-model="slot.content_type" style="width: 120px">
                <el-option v-for="opt in contentTypeOptions" :key="opt.value" :value="opt.value" :label="opt.label" />
              </el-select>
              <el-select v-model="slot.publish_domain" style="width: 100px">
                <el-option v-for="opt in domainOptions" :key="opt.value" :value="opt.value" :label="opt.label" />
              </el-select>
              <el-button size="small" type="danger" plain @click="removeSlot(i)">删除</el-button>
            </div>
            <el-button size="small" @click="addSlot">+ 添加文章槽</el-button>
          </div>
        </el-form-item>

        <el-form-item label="发布时间">
          <div class="time-container">
            <div v-for="(t, i) in form.publish_times" :key="i" class="time-row">
              <el-time-picker
                v-model="form.publish_times[i]"
                format="HH:mm"
                value-format="HH:mm"
                style="width: 140px"
                placeholder="选择时间"
              />
              <el-button size="small" type="danger" plain @click="removeTime(i)">删除</el-button>
            </div>
            <el-button size="small" @click="addTime">+ 添加时间</el-button>
          </div>
        </el-form-item>

        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="公域篇数">
              <el-input-number v-model="form.public_count" :min="0" :max="10" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="私域篇数">
              <el-input-number v-model="form.private_count" :min="0" :max="10" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="showForm = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">
          {{ editing ? '更新' : '创建' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.plans-page {
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

.plan-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
}

.plan-card {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 16px;
  background: #fff;
  transition: box-shadow 0.2s;
}

.plan-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.card-title {
  font-size: 16px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
}

.card-body {
  margin-bottom: 12px;
}

.section-title {
  font-size: 13px;
  color: #909399;
  margin-bottom: 6px;
  margin-top: 8px;
}

.slot-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 0;
}

.slot-order {
  font-size: 12px;
  color: #909399;
  min-width: 20px;
}

.time-list {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.muted {
  color: #c0c4cc;
  font-size: 13px;
}

.count-row {
  display: flex;
  gap: 16px;
  margin-top: 8px;
  font-size: 13px;
  color: #606266;
}

.card-actions {
  display: flex;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid #f0f0f0;
}

.slots-container,
.time-container {
  width: 100%;
}

.slot-row,
.time-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.slot-index {
  min-width: 24px;
  font-size: 13px;
  color: #909399;
}
</style>

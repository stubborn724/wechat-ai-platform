<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'

interface Pool {
  id: number; name: string; description: string | null; source_count: number
}

interface ImitationTask {
  id: number; name: string; pool_id: number | null; strategy: string
  articles_per_day: number; status: string; total_generated: number
  created_at: string; updated_at: string
}

interface Account {
  id: number; name: string; app_id: string
}

interface KbItem {
  id: number; name: string; kb_type: string
}

const tasks = ref<ImitationTask[]>([])
const pools = ref<Pool[]>([])
const accounts = ref<Account[]>([])
const knowledgeBases = ref<KbItem[]>([])
const loading = ref(true)
const executing = ref<number | null>(null)
const showForm = ref(false)
const saving = ref(false)

const form = reactive({
  name: '', pool_id: null as number | null, strategy: 'random',
  articles_per_day: 1, content_types: ['article'],
  publish_times: [] as string[],
  account_id: null as number | null, approval_mode: 'auto',
  knowledge_base_ids: [] as number[], footer_template: '',
})

const strategyLabels: Record<string, string> = {
  random: '随机选源', round_robin: '轮流选源', exhaust: '全部仿写完',
}

const statusLabels: Record<string, string> = {
  active: '运行中', paused: '已暂停', completed: '已完成',
}

async function load() {
  loading.value = true
  try {
    const [t, p, a, k] = await Promise.all([
      client.get('/imitation/tasks'),
      client.get('/imitation/pools'),
      client.get('/accounts'),
      client.get('/knowledge-bases'),
    ])
    tasks.value = t.data || []
    pools.value = p.data || []
    accounts.value = a.data?.items || []
    knowledgeBases.value = k.data?.items || []
  } catch { /* ignore */ }
  finally { loading.value = false }
}

function openForm() {
  form.name = ''; form.pool_id = null; form.strategy = 'random'
  form.articles_per_day = 1; form.content_types = ['article']
  form.publish_times = []; form.account_id = null; form.approval_mode = 'auto'
  form.knowledge_base_ids = []; form.footer_template = ''
  showForm.value = true
}

async function create() {
  if (!form.name || !form.pool_id) { ElMessage.warning('请填写任务名称并选择仿写池'); return }
  saving.value = true
  try {
    await client.post('/imitation/tasks', form)
    showForm.value = false
    ElMessage.success('仿写任务已创建')
    await load()
  } catch (err: any) { ElMessage.error(err?.response?.data?.detail || '创建失败') }
  finally { saving.value = false }
}

async function executeNow(task: ImitationTask) {
  executing.value = task.id
  try {
    const res = await client.post(`/imitation/tasks/${task.id}/execute`)
    const data = res.data
    ElMessage.success(`生成完成: ${data.generated || 0} 篇成功, ${data.failed || 0} 篇失败`)
    await load()
  } catch (err: any) { ElMessage.error(err?.response?.data?.detail || '执行失败') }
  finally { executing.value = null }
}

async function toggleTask(task: ImitationTask) {
  const action = task.status === 'active' ? 'pause' : 'resume'
  try {
    await client.post(`/imitation/tasks/${task.id}/toggle?action=${action}`)
    ElMessage.success(action === 'pause' ? '任务已暂停' : '任务已恢复')
    await load()
  } catch (err: any) { ElMessage.error(err?.response?.data?.detail || '操作失败') }
}

async function removeTask(task: ImitationTask) {
  try {
    await ElMessageBox.confirm(`确定删除任务「${task.name}」？`, '确认删除',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' })
    await client.delete(`/imitation/tasks/${task.id}`)
    ElMessage.success('已删除')
    await load()
  } catch { /* cancelled */ }
}

function statusType(status: string): string {
  if (status === 'active') return 'success'
  if (status === 'paused') return 'warning'
  if (status === 'completed') return 'info'
  return ''
}

onMounted(load)
</script>

<template>
  <div class="imitation-tasks-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">IMITATION SCHEDULER</p>
        <h1>仿写任务</h1>
        <p class="lead">配置仿写策略、发布频率和时间，AI 每天自动仿写指定数量的文章。</p>
      </div>
      <el-button type="primary" @click="openForm">创建仿写任务</el-button>
    </div>

    <div v-if="loading" class="loading-section"><el-skeleton :rows="3" animated /></div>

    <div v-else-if="tasks.length === 0" class="empty-state">
      <el-empty description="还没有仿写任务">
        <p class="empty-hint">先创建仿写池并添加来源，再创建仿写任务。</p>
        <el-button type="primary" @click="openForm">创建第一个任务</el-button>
      </el-empty>
    </div>

    <el-table v-else :data="tasks" stripe style="width: 100%">
      <el-table-column label="任务名称" min-width="180">
        <template #default="{ row }"><strong>{{ row.name }}</strong></template>
      </el-table-column>
      <el-table-column label="策略" width="120">
        <template #default="{ row }">{{ strategyLabels[row.strategy] || row.strategy }}</template>
      </el-table-column>
      <el-table-column label="每天篇数" width="100" align="center">
        <template #default="{ row }">{{ row.articles_per_day }} 篇</template>
      </el-table-column>
      <el-table-column label="已生成" width="90" align="center">
        <template #default="{ row }">{{ row.total_generated }} 篇</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status) as any" size="small">
            {{ statusLabels[row.status] || row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="更新时间" width="170">
        <template #default="{ row }">
          {{ new Date(row.updated_at).toLocaleString('zh-CN') }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" :loading="executing === row.id" @click="executeNow(row)">
            立即执行
          </el-button>
          <el-button size="small" @click="toggleTask(row)">
            {{ row.status === 'active' ? '暂停' : '恢复' }}
          </el-button>
          <el-button size="small" type="danger" @click="removeTask(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- Create Dialog -->
    <el-dialog v-model="showForm" title="创建仿写任务" width="600px">
      <el-form label-position="top">
        <el-form-item label="任务名称" required>
          <el-input v-model="form.name" placeholder="例如：每日科技仿写" />
        </el-form-item>
        <el-form-item label="仿写池" required>
          <el-select v-model="form.pool_id" style="width: 100%" placeholder="选择仿写池">
            <el-option v-for="p in pools" :key="p.id" :value="p.id" :label="`${p.name} (${p.source_count}个来源)`" />
          </el-select>
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="仿写策略">
              <el-select v-model="form.strategy" style="width: 100%">
                <el-option value="random" label="随机选源" />
                <el-option value="round_robin" label="轮流选源" />
                <el-option value="exhaust" label="全部仿写完" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="每天篇数">
              <el-input-number v-model="form.articles_per_day" :min="1" :max="20" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="发布时间（每天，可设置多个）">
          <el-time-picker
            v-model="form.publish_times"
            is-range
            arrow-control
            value-format="HH:mm"
            range-separator="至"
            start-placeholder="开始时间"
            end-placeholder="结束时间"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="发布方式">
          <el-select v-model="form.approval_mode" style="width: 100%">
            <el-option value="auto" label="自动发布" />
            <el-option value="manual" label="人工审核后发布" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标公众号（发布到）">
          <el-select v-model="form.account_id" style="width: 100%" clearable placeholder="暂不指定">
            <el-option v-for="a in accounts" :key="a.id" :value="a.id" :label="a.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="关联知识库（可选）">
          <el-select v-model="form.knowledge_base_ids" multiple style="width: 100%" placeholder="选择知识库">
            <el-option v-for="kb in knowledgeBases" :key="kb.id" :value="kb.id" :label="kb.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="底部固定内容（可选）">
          <el-input v-model="form.footer_template" type="textarea" :rows="2"
            placeholder="例如联系方式、二维码说明等，会自动追加到文章末尾" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showForm = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="create">创建任务</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.imitation-tasks-page { max-width: 1200px; }
.page-heading {
  display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 28px;
}
.eyebrow { font-size: 11px; letter-spacing: 0.15em; color: #909399; margin-bottom: 6px; }
.page-heading h1 { font-size: 24px; font-weight: 700; color: #303133; margin-bottom: 6px; }
.lead { color: #909399; font-size: 14px; }
.loading-section { padding: 40px 0; }
.empty-state { padding: 60px 0; }
.empty-hint { color: #909399; font-size: 13px; margin-bottom: 16px; }
</style>

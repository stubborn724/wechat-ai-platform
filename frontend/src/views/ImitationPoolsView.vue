<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'

interface Pool {
  id: number; name: string; description: string | null; is_active: boolean; source_count: number; created_at: string
}

interface FeedSource {
  id: number; name: string; source_type: string; source_identifier: string; article_count?: number
}

interface PoolSource {
  id: number; feed_source_id: number | null; source_name: string | null; source_type: string | null; wechat_name: string | null; wechat_app_id: string | null; weight: number; article_count: number
}

const pools = ref<Pool[]>([])
const feedSources = ref<FeedSource[]>([])
const loading = ref(true)
const showForm = ref(false)
const saving = ref(false)
const selectedPool = ref<Pool | null>(null)
const poolSources = ref<PoolSource[]>([])
const showSources = ref(false)
const showAddSource = ref(false)

const form = reactive({ name: '', description: '' })
const addSourceForm = reactive({
  feed_source_id: null as number | null,
  wechat_name: '',
  wechat_app_id: '',
  weight: 1,
})

async function load() {
  loading.value = true
  try {
    const [p, f] = await Promise.all([
      client.get('/imitation/pools'),
      client.get('/feed-sources'),
    ])
    pools.value = p.data || []
    feedSources.value = f.data?.items || []
  } catch { /* ignore */ }
  finally { loading.value = false }
}

function openForm() {
  form.name = ''; form.description = ''
  showForm.value = true
}

async function create() {
  if (!form.name) return
  saving.value = true
  try {
    await client.post('/imitation/pools', form)
    showForm.value = false
    ElMessage.success('仿写池已创建')
    await load()
  } catch (err: any) { ElMessage.error(err?.response?.data?.detail || '创建失败') }
  finally { saving.value = false }
}

async function remove(pool: Pool) {
  try {
    await ElMessageBox.confirm(`确定删除仿写池「${pool.name}」？`, '确认删除',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' })
    await client.delete(`/imitation/pools/${pool.id}`)
    ElMessage.success('已删除')
    await load()
  } catch { /* cancelled */ }
}

async function viewSources(pool: Pool) {
  selectedPool.value = pool
  try {
    const res = await client.get(`/imitation/pools/${pool.id}/sources`)
    poolSources.value = res.data || []
    showSources.value = true
  } catch { ElMessage.error('加载失败') }
}

function openAddSource() {
  addSourceForm.feed_source_id = null
  addSourceForm.wechat_name = ''
  addSourceForm.wechat_app_id = ''
  addSourceForm.weight = 1
  showAddSource.value = true
}

async function addSource() {
  if (!addSourceForm.feed_source_id && !addSourceForm.wechat_name) {
    ElMessage.warning('请选择投喂源或输入公众号名称')
    return
  }
  saving.value = true
  try {
    await client.post(`/imitation/pools/${selectedPool.value?.id}/sources`, addSourceForm)
    showAddSource.value = false
    ElMessage.success('已添加来源')
    if (selectedPool.value) await viewSources(selectedPool.value)
  } catch (err: any) { ElMessage.error(err?.response?.data?.detail || '添加失败') }
  finally { saving.value = false }
}

async function removeSource(sourceId: number) {
  try {
    await client.delete(`/imitation/pools/${selectedPool.value?.id}/sources/${sourceId}`)
    ElMessage.success('已移除')
    if (selectedPool.value) await viewSources(selectedPool.value)
  } catch { /* ignore */ }
}

async function analyzePool(pool: Pool) {
  try {
    ElMessage.info('开始结构分析，请稍候...')
    const res = await client.post(`/imitation/pools/${pool.id}/analyze`)
    const results = res.data?.results || []
    const ok = results.filter((r: any) => r.status === 'analyzed').length
    ElMessage.success(`分析完成: ${ok}/${results.length} 个来源分析成功`)
    await load()
  } catch (err: any) { ElMessage.error(err?.response?.data?.detail || '分析失败') }
}

onMounted(load)
</script>

<template>
  <div class="imitation-pools-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">IMITATION ENGINE</p>
        <h1>仿写池</h1>
        <p class="lead">管理要仿写的公众号/Feed 源，配置结构分析，驱动批量仿写。</p>
      </div>
      <el-button type="primary" @click="openForm">创建仿写池</el-button>
    </div>

    <div v-if="loading" class="loading-section"><el-skeleton :rows="3" animated /></div>

    <div v-else-if="pools.length === 0" class="empty-state">
      <el-empty description="还没有仿写池">
        <el-button type="primary" @click="openForm">创建第一个仿写池</el-button>
      </el-empty>
    </div>

    <div v-else class="pool-list">
      <div v-for="pool in pools" :key="pool.id" class="pool-card">
        <div class="pool-main">
          <div class="pool-title-row">
            <h2>{{ pool.name }}</h2>
          </div>
          <p class="pool-desc">{{ pool.description || '暂无描述' }}</p>
          <div class="pool-meta">
            <span>来源数: <strong>{{ pool.source_count }}</strong></span>
            <span>创建于 {{ new Date(pool.created_at).toLocaleDateString('zh-CN') }}</span>
          </div>
        </div>
        <div class="pool-actions">
          <el-button size="small" @click="viewSources(pool)">管理来源</el-button>
          <el-button size="small" @click="analyzePool(pool)">结构分析</el-button>
          <el-button size="small" type="danger" plain @click="remove(pool)">删除</el-button>
        </div>
      </div>
    </div>

    <!-- Create Pool Dialog -->
    <el-dialog v-model="showForm" title="创建仿写池" width="480px">
      <el-form label-position="top">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="例如：科技类公众号仿写池" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="可选描述" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showForm = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="create">创建</el-button>
      </template>
    </el-dialog>

    <!-- Pool Sources Dialog -->
    <el-dialog v-model="showSources" :title="`来源管理 - ${selectedPool?.name}`" width="700px">
      <div class="sources-toolbar">
        <el-button size="small" type="primary" @click="openAddSource">添加来源</el-button>
      </div>
      <el-table v-if="poolSources.length > 0" :data="poolSources" stripe style="width: 100%">
        <el-table-column label="名称">
          <template #default="{ row }">{{ row.source_name || row.wechat_name || '未知' }}</template>
        </el-table-column>
        <el-table-column label="类型" width="100">
          <template #default="{ row }">{{ row.source_type || '直接录入' }}</template>
        </el-table-column>
        <el-table-column label="文章数" width="80" align="center">
          {{ row.article_count }}
        </el-table-column>
        <el-table-column label="权重" width="80">
          <template #default="{ row }">{{ row.weight }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button link type="danger" size="small" @click="removeSource(row.id)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无来源" />
      <template #footer><el-button @click="showSources = false">关闭</el-button></template>
    </el-dialog>

    <!-- Add Source Dialog -->
    <el-dialog v-model="showAddSource" title="添加仿写来源" width="500px" destroy-on-close>
      <el-form label-position="top">
        <el-form-item label="从投喂源选择（优先）">
          <el-select v-model="addSourceForm.feed_source_id" style="width: 100%" clearable placeholder="选择已有投喂源">
            <el-option v-for="fs in feedSources" :key="fs.id" :value="fs.id" :label="fs.name" />
          </el-select>
        </el-form-item>
        <el-divider>或直接录入公众号</el-divider>
        <el-form-item label="公众号名称">
          <el-input v-model="addSourceForm.wechat_name" placeholder="公众号名称" />
        </el-form-item>
        <el-form-item label="权重（越高越容易被选中仿写）">
          <el-input-number v-model="addSourceForm.weight" :min="1" :max="10" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAddSource = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="addSource">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.imitation-pools-page { max-width: 1200px; }
.page-heading {
  display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 28px;
}
.eyebrow { font-size: 11px; letter-spacing: 0.15em; color: #909399; margin-bottom: 6px; }
.page-heading h1 { font-size: 24px; font-weight: 700; color: #303133; margin-bottom: 6px; }
.lead { color: #909399; font-size: 14px; }
.loading-section { padding: 40px 0; }
.empty-state { padding: 60px 0; }
.pool-list { display: grid; gap: 16px; }
.pool-card {
  display: flex; justify-content: space-between; align-items: center;
  padding: 20px; border: 1px solid #e4e7ed; border-radius: 8px; background: #fff;
}
.pool-title-row h2 { margin: 0; font-size: 18px; font-weight: 600; }
.pool-desc { color: #909399; font-size: 13px; margin: 6px 0; }
.pool-meta { display: flex; gap: 20px; color: #909399; font-size: 12px; }
.pool-actions { display: flex; gap: 8px; flex-shrink: 0; }
.sources-toolbar { margin-bottom: 16px; }
</style>

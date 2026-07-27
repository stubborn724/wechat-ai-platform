<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import type { Article } from '@/api/types'

interface Candidate {
  id: number
  article_id: number
  title: string | null
  topic: string | null
  quality_score: number | null
  optimization_status: string
  created_at: string
}

interface OptimizationDetail {
  id: number
  source_article_id: number
  optimized_article_id: number | null
  optimization_type: string
  status: string
  change_summary: string | null
  created_at: string
}

const loading = ref(false)
const candidates = ref<Candidate[]>([])
const selectedCandidate = ref<Candidate | null>(null)
const sourceArticle = ref<Article | null>(null)
const optimizedArticle = ref<Article | null>(null)
const optimizations = ref<OptimizationDetail[]>([])
const selectedOpt = ref<OptimizationDetail | null>(null)
const sourceQuality = ref<any>(null)
const optQuality = ref<any>(null)
const actionLoading = ref(false)
const filterStatus = ref('')
const activeTab = ref('candidates')

// Load candidates
async function loadCandidates() {
  loading.value = true
  try {
    const params: any = {}
    if (filterStatus.value) params.status = filterStatus.value
    const res = await client.get('/optimizations/candidates', { params })
    candidates.value = res.data || []
  } catch (err: any) {
    ElMessage.error('加载优化候选列表失败')
  } finally {
    loading.value = false
  }
}

// Select candidate
async function selectCandidate(c: Candidate) {
  selectedCandidate.value = c
  selectedOpt.value = null
  sourceArticle.value = null
  optimizedArticle.value = null
  sourceQuality.value = null
  optQuality.value = null

  try {
    // Load source article
    const artRes = await client.get(`/articles/${c.article_id}/metrics/latest`)
    sourceArticle.value = artRes.data as any

    // Load optimizations for this article
    const optRes = await client.get('/optimizations', {
      params: { source_article_id: c.article_id, status: 'draft_ready' }
    })
    optimizations.value = optRes.data?.items || []

    // Load source quality
    const qRes = await client.get(`/articles/${c.article_id}/quality/latest`)
    sourceQuality.value = qRes.data
  } catch (_) { /* ignore */ }
}

// Select optimization version
async function selectOptimization(opt: OptimizationDetail) {
  selectedOpt.value = opt
  optimizedArticle.value = null
  optQuality.value = null

  if (!opt.optimized_article_id) return

  try {
    const artRes = await client.get(`/articles/${opt.optimized_article_id}/metrics/latest`)
    optimizedArticle.value = artRes.data as any

    const qRes = await client.get(`/articles/${opt.optimized_article_id}/quality/latest`)
    optQuality.value = qRes.data
  } catch (_) { /* ignore */ }
}

// Approve
async function approveOptimization() {
  if (!selectedOpt.value) return
  actionLoading.value = true
  try {
    await client.post(`/optimizations/${selectedOpt.value.id}/approve`, { comment: '' })
    ElMessage.success('优化稿已批准')
    selectedOpt.value.status = 'approved'
    await loadCandidates()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '操作失败')
  } finally {
    actionLoading.value = false
  }
}

// Reject
async function rejectOptimization() {
  if (!selectedOpt.value) return
  actionLoading.value = true
  try {
    await client.post(`/optimizations/${selectedOpt.value.id}/reject`, { comment: '' })
    ElMessage.success('优化稿已驳回')
    selectedOpt.value.status = 'rejected'
    await loadCandidates()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '操作失败')
  } finally {
    actionLoading.value = false
  }
}

// Regenerate
async function regenerateOptimization() {
  if (!selectedOpt.value) return
  actionLoading.value = true
  try {
    await client.post(`/optimizations/${selectedOpt.value.id}/regenerate`)
    ElMessage.success('已触发重新生成')
    await loadCandidates()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '操作失败')
  } finally {
    actionLoading.value = false
  }
}

// Create optimization
async function createOptimization() {
  if (!selectedCandidate.value) return
  actionLoading.value = true
  try {
    await client.post(`/articles/${selectedCandidate.value.article_id}/optimization-drafts`, {
      optimization_type: 'structure_optimize',
    })
    ElMessage.success('优化稿已生成')
    await selectCandidate(selectedCandidate.value)
    await loadCandidates()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '创建失败')
  } finally {
    actionLoading.value = false
  }
}

const scoreTag = (score: number | null) => {
  if (score === null) return 'info'
  if (score >= 70) return 'success'
  if (score >= 50) return 'warning'
  return 'danger'
}

const statusLabel: Record<string, string> = {
  suggested: '待优化',
  generating: '生成中',
  draft_ready: '待审核',
  approved: '已批准',
  rejected: '已驳回',
  generating_opt: '生成中',
  observing: '观察中',
  effective: '有效',
  ineffective: '无效',
}

onMounted(loadCandidates)
</script>

<template>
  <div class="optimization-review">
    <div class="page-heading">
      <div>
        <p class="eyebrow">OPTIMIZATION REVIEW</p>
        <h1>优化审核</h1>
        <p class="lead">查看文章优化候选、审核优化稿、追踪优化效果。</p>
      </div>
      <div class="actions">
        <el-select v-model="filterStatus" placeholder="筛选状态" clearable style="width:140px;" @change="loadCandidates">
          <el-option label="待优化" value="suggested" />
          <el-option label="待审核" value="draft_ready" />
          <el-option label="已批准" value="approved" />
          <el-option label="已驳回" value="rejected" />
        </el-select>
        <el-button type="primary" @click="loadCandidates" :loading="loading">刷新</el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="优化候选" name="candidates">
        <el-row :gutter="16" style="height: calc(100vh - 240px);">
          <!-- Left: candidate list -->
          <el-col :span="7" style="height:100%; overflow-y:auto;">
            <div v-if="loading" v-loading="loading" style="height:100%;" />
            <div v-else-if="candidates.length === 0" class="empty-state">
              <el-empty description="暂无优化候选" />
            </div>
            <div v-else class="candidate-list">
              <div
                v-for="c in candidates" :key="c.id"
                class="candidate-item"
                :class="{ active: selectedCandidate?.article_id === c.article_id }"
                @click="selectCandidate(c)"
              >
                <div class="candidate-title">{{ c.title || c.topic || '无标题' }}</div>
                <div class="candidate-meta">
                  <el-tag :type="scoreTag(c.quality_score)" size="small">
                    评分 {{ c.quality_score ?? 'N/A' }}
                  </el-tag>
                  <el-tag :type="c.optimization_status === 'draft_ready' ? 'warning' : 'info'" size="small">
                    {{ statusLabel[c.optimization_status] || c.optimization_status }}
                  </el-tag>
                </div>
              </div>
            </div>
          </el-col>

          <!-- Right: detail -->
          <el-col :span="17" style="height:100%; overflow-y:auto;">
            <div v-if="!selectedCandidate" class="empty-state">
              <el-empty description="请从左侧选择一个优化候选" />
            </div>
            <div v-else>
              <!-- Source quality -->
              <el-card shadow="never" style="margin-bottom:12px;">
                <template #header>
                  <span style="font-weight:600;">
                    {{ selectedCandidate.title || selectedCandidate.topic || '无标题' }}
                  </span>
                  <el-tag :type="scoreTag(selectedCandidate.quality_score)" style="margin-left:8px;">
                    质量分 {{ selectedCandidate.quality_score ?? 'N/A' }}
                  </el-tag>
                </template>
                <div v-if="sourceQuality?.suggestions?.length" style="font-size:13px;color:#666;">
                  <div v-for="(s, i) in sourceQuality.suggestions" :key="i">• {{ s }}</div>
                </div>
              </el-card>

              <!-- Optimizations -->
              <div v-if="optimizations.length > 0">
                <div v-for="opt in optimizations" :key="opt.id"
                  class="opt-card"
                  :class="{ active: selectedOpt?.id === opt.id }"
                  @click="selectOptimization(opt)"
                >
                  <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div>
                      <strong>{{ opt.optimization_type }}</strong>
                      <el-tag size="small" style="margin-left:8px;" :type="opt.status === 'draft_ready' ? 'warning' : 'info'">
                        {{ statusLabel[opt.status] || opt.status }}
                      </el-tag>
                    </div>
                    <span v-if="opt.change_summary" style="font-size:12px;color:#999;">{{ opt.change_summary.slice(0, 60) }}</span>
                  </div>
                </div>
              </div>
              <div v-else style="text-align:center;padding:24px;color:#999;">
                暂无优化稿
                <el-button size="small" text @click="createOptimization" :loading="actionLoading">创建优化稿</el-button>
              </div>

              <!-- Optimized quality -->
              <el-card v-if="optQuality && optQuality.status !== 'not_evaluated'" shadow="never" style="margin-top:12px;">
                <template #header>优化版质量评分：{{ optQuality.overall_score }}</template>
                <div v-if="optQuality.suggestions?.length" style="font-size:13px;color:#666;">
                  <div v-for="(s, i) in optQuality.suggestions" :key="i">• {{ s }}</div>
                </div>
              </el-card>

              <!-- Actions -->
              <div v-if="selectedOpt" style="margin-top:16px;display:flex;gap:8px;">
                <el-button type="success" @click="approveOptimization" :loading="actionLoading">批准发布</el-button>
                <el-button type="danger" @click="rejectOptimization" :loading="actionLoading">驳回</el-button>
                <el-button @click="regenerateOptimization" :loading="actionLoading">重新生成</el-button>
              </div>
            </div>
          </el-col>
        </el-row>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.optimization-review { padding: 0 0 24px 0; }
.page-heading { display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px; }
.eyebrow { font-size:12px;color:#999;margin:0;letter-spacing:1px; }
h1 { margin:4px 0;font-size:22px; }
.lead { font-size:14px;color:#666;margin:4px 0 0 0; }
.actions { display:flex;gap:8px; }
.empty-state { display:flex;align-items:center;justify-content:center;height:400px; }
.candidate-list { padding:4px 0; }
.candidate-item {
  padding:12px;border-radius:8px;cursor:pointer;margin-bottom:4px;
  border:1px solid #ebeef5;transition:all 0.2s;
}
.candidate-item:hover, .candidate-item.active {
  background:#ecf5ff;border-color:#409eff;
}
.candidate-title { font-size:14px;font-weight:500;margin-bottom:6px; }
.candidate-meta { display:flex;gap:6px; }
.opt-card {
  padding:12px;border-radius:8px;cursor:pointer;margin-bottom:4px;
  border:1px solid #ebeef5;transition:all 0.2s;
}
.opt-card:hover, .opt-card.active {
  background:#fdf6ec;border-color:#e6a23c;
}
</style>

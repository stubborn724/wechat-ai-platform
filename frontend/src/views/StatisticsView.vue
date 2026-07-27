<script setup lang="ts">
import { ref, onMounted } from 'vue'
import client from '@/api/client'

interface QualityDistribution {
  excellent: number
  good: number
  fair: number
  poor: number
  not_evaluated: number
  total: number
  avg_dimensions: Record<string, number> | null
}

interface OptReport {
  total_optimizations: number
  draft_ready: number
  approved: number
  rejected: number
  effective: number
  ineffective: number
  pending_review: number
}

const loading = ref(false)
const activeTab = ref('quality')
const qualityDist = ref<QualityDistribution | null>(null)
const optReport = ref<OptReport | null>(null)

async function loadQuality() {
  loading.value = true
  try {
    const res = await client.get('/statistics/articles/quality-distribution')
    qualityDist.value = res.data
  } catch (_) { /* ignore */ }
  finally { loading.value = false }
}

async function loadOptimizationReport() {
  loading.value = true
  try {
    const res = await client.get('/statistics/articles/optimization-report')
    optReport.value = res.data
  } catch (_) { /* ignore */ }
  finally { loading.value = false }
}

function onTabChange(tab: string) {
  if (tab === 'quality') loadQuality()
  if (tab === 'optimization') loadOptimizationReport()
}

onMounted(loadQuality)
</script>

<template>
  <div class="statistics-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">STATISTICS</p>
        <h1>统计报表</h1>
        <p class="lead">查看文章质量分布和优化效果报告。</p>
      </div>
    </div>

    <el-tabs v-model="activeTab" @tab-change="onTabChange">
      <!-- Tab 1: 内容质量 -->
      <el-tab-pane label="内容质量" name="quality">
        <div v-loading="loading">
          <el-row :gutter="16">
            <el-col :span="6" v-if="qualityDist">
              <el-card shadow="never">
                <template #header>优秀 (85+)</template>
                <div style="font-size:32px;font-weight:700;color:#67c23a;">{{ qualityDist.excellent }}</div>
                <div style="font-size:12px;color:#999;">篇</div>
              </el-card>
            </el-col>
            <el-col :span="6" v-if="qualityDist">
              <el-card shadow="never">
                <template #header>合格 (70-84)</template>
                <div style="font-size:32px;font-weight:700;color:#409eff;">{{ qualityDist.good }}</div>
                <div style="font-size:12px;color:#999;">篇</div>
              </el-card>
            </el-col>
            <el-col :span="6" v-if="qualityDist">
              <el-card shadow="never">
                <template #header>待优化 (50-69)</template>
                <div style="font-size:32px;font-weight:700;color:#e6a23c;">{{ qualityDist.fair }}</div>
                <div style="font-size:12px;color:#999;">篇</div>
              </el-card>
            </el-col>
            <el-col :span="6" v-if="qualityDist">
              <el-card shadow="never">
                <template #header>低质量 (&lt;50)</template>
                <div style="font-size:32px;font-weight:700;color:#f56c6c;">{{ qualityDist.poor }}</div>
                <div style="font-size:12px;color:#999;">篇</div>
              </el-card>
            </el-col>
          </el-row>

          <!-- Dimension averages -->
          <el-card v-if="qualityDist?.avg_dimensions" shadow="never" style="margin-top:16px;">
            <template #header>各维度平均分</template>
            <el-row :gutter="12">
              <el-col :span="4" v-for="(val, key) in qualityDist.avg_dimensions" :key="key">
                <div style="text-align:center;">
                  <div style="font-size:24px;font-weight:600;">{{ val }}</div>
                  <div style="font-size:12px;color:#999;margin-top:4px;">
                    {{ { content_score: '内容', readability_score: '可读性', structure_score: '结构', value_score: '价值', title_score: '标题' }[key] || key }}
                  </div>
                </div>
              </el-col>
            </el-row>
          </el-card>

          <el-card v-if="!qualityDist" shadow="never" style="margin-top:16px;">
            暂无数据
          </el-card>
        </div>
      </el-tab-pane>

      <!-- Tab 2: 优化报告 -->
      <el-tab-pane label="优化报告" name="optimization">
        <div v-loading="loading">
          <el-row :gutter="16">
            <el-col :span="6">
              <el-card shadow="never">
                <template #header>优化总数</template>
                <div style="font-size:32px;font-weight:700;">{{ optReport?.total_optimizations || 0 }}</div>
              </el-card>
            </el-col>
            <el-col :span="6">
              <el-card shadow="never">
                <template #header>待审核</template>
                <div style="font-size:32px;font-weight:700;color:#e6a23c;">{{ optReport?.pending_review || 0 }}</div>
              </el-card>
            </el-col>
            <el-col :span="6">
              <el-card shadow="never">
                <template #header>已批准</template>
                <div style="font-size:32px;font-weight:700;color:#67c23a;">{{ optReport?.approved || 0 }}</div>
              </el-card>
            </el-col>
            <el-col :span="6">
              <el-card shadow="never">
                <template #header>已驳回</template>
                <div style="font-size:32px;font-weight:700;color:#f56c6c;">{{ optReport?.rejected || 0 }}</div>
              </el-card>
            </el-col>
          </el-row>

          <el-row :gutter="16" style="margin-top:16px;">
            <el-col :span="6">
              <el-card shadow="never">
                <template #header>效果有效</template>
                <div style="font-size:32px;font-weight:700;color:#67c23a;">{{ optReport?.effective || 0 }}</div>
              </el-card>
            </el-col>
            <el-col :span="6">
              <el-card shadow="never">
                <template #header>效果无效</template>
                <div style="font-size:32px;font-weight:700;color:#f56c6c;">{{ optReport?.ineffective || 0 }}</div>
              </el-card>
            </el-col>
          </el-row>

          <el-card v-if="!optReport" shadow="never" style="margin-top:16px;">
            暂无优化数据
          </el-card>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.statistics-page { padding: 0 0 24px 0; }
.page-heading { margin-bottom:16px; }
.eyebrow { font-size:12px;color:#999;margin:0;letter-spacing:1px; }
h1 { margin:4px 0;font-size:22px; }
.lead { font-size:14px;color:#666;margin:4px 0 0 0; }
</style>

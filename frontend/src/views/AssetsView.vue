<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import type { Asset } from '@/api/types'
import {
  importErpProductImages,
  listErpProductSources,
  searchErpProducts,
  type ErpProduct,
  type ErpProductSource,
} from '@/api/erpProducts'

const assets = ref<Asset[]>([])
const loading = ref(true)
const total = ref(0)
const filterType = ref('')
// 素材来源是本地素材库内的分类标签，不代表浏览器仍需访问 ERP 远端地址。
const filterSource = ref<'all' | 'uploaded' | 'generated' | 'erp'>('all')
const showUpload = ref(false)
const uploading = ref(false)
const uploadFile = ref<File | null>(null)
const uploadTags = ref('')
const selectedAsset = ref<Asset | null>(null)
const showPreview = ref(false)
const docContent = ref('')
const docLoading = ref(false)
const selectedAssetIds = ref<number[]>([])
const deletingAssets = ref(false)

// ERP 素材导入状态。远端产品仅在此弹窗中浏览，选中的图片会复制到本地素材库。
const showErpImport = ref(false)
const erpSources = ref<ErpProductSource[]>([])
const selectedErpSource = ref('')
const erpModelFilter = ref('')
const erpSeriesFilter = ref('')
const erpProducts = ref<ErpProduct[]>([])
const selectedErpImageUrls = ref<string[]>([])
const erpImportLimit = ref(10)
const erpPageNo = ref(1)
const erpPageSize = 50
const erpProductTotal = ref(0)
const loadingErpProducts = ref(false)
const importingErpProducts = ref(false)

// Watermark controls
const applyingWatermark = ref(false)
const removingWatermark = ref(false)
const showWatermarkDialog = ref(false)
const watermarkConfig = ref<any>(null)
const watermarkLoading = ref(false)
const wmPositionOptions = [
  { value: 'top-left', label: '左上角' },
  { value: 'top-right', label: '右上角' },
  { value: 'bottom-left', label: '左下角' },
  { value: 'bottom-right', label: '右下角' },
  { value: 'center', label: '居中' },
]
const wmForm = ref({
  type: 'logo' as 'logo' | 'text',
  image_key: '',
  content: '',
  position: 'bottom-right',
  opacity: 0.8,
  scale: 0.15,
  font_size: 36,
  color: '#FFFFFF',
  margin: 20,
})

const assetTypeNames: Record<string, string> = {
  image: '图片',
  video: '视频',
  document: '文档',
}

const assetTypeOptions = [
  { value: '', label: '全部类型' },
  { value: 'image', label: '图片' },
  { value: 'video', label: '视频' },
  { value: 'document', label: '文档' },
]

const selectedAssetCount = computed(() => selectedAssetIds.value.length)
const allVisibleAssetsSelected = computed({
  get: () => assets.value.length > 0 && assets.value.every(asset => selectedAssetIds.value.includes(asset.id)),
  set: (checked: boolean) => {
    selectedAssetIds.value = checked ? assets.value.map(asset => asset.id) : []
  },
})

function formatSize(bytes: number | undefined): string {
  if (!bytes || bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, string | number> = { page: 1, page_size: 50 }
    if (filterType.value) params.asset_type = filterType.value
    if (filterSource.value === 'generated') params.tags = 'auto-archived'
    else if (filterSource.value === 'erp') params.tags = 'ERP产品'
    // 手工上传不包含系统归档或 ERP 导入来源，避免两个来源混入“上传的”筛选。
    else if (filterSource.value === 'uploaded') params.tags = '-auto-archived,-ERP产品'
    const res = await client.get<{ items: Asset[]; total: number }>('/assets', { params })
    assets.value = res.data.items || []
    total.value = res.data.total || 0
    selectedAssetIds.value = []
  } catch {
    ElMessage.error('加载素材失败')
  } finally {
    loading.value = false
  }
}

function handleFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files?.length) uploadFile.value = input.files[0]
}

async function uploadAsset() {
  if (!uploadFile.value) {
    ElMessage.warning('请选择文件')
    return
  }
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', uploadFile.value)
    const params: Record<string, string> = {}
    if (uploadTags.value.trim()) params.tags = uploadTags.value.trim()
    await client.post('/assets/upload', formData, { params })
    showUpload.value = false
    uploadFile.value = null
    uploadTags.value = ''
    ElMessage.success('上传成功')
    await load()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '上传失败')
  } finally {
    uploading.value = false
  }
}

async function openErpImport() {
  showErpImport.value = true
  if (erpSources.value.length === 0) {
    try {
      erpSources.value = await listErpProductSources()
      selectedErpSource.value = erpSources.value[0]?.key || ''
    } catch (err: any) {
      ElMessage.error(err?.response?.data?.detail || 'ERP 产品来源未配置')
      return
    }
  }
  if (selectedErpSource.value && erpProducts.value.length === 0) await searchErpProductsForImport()
}

async function searchErpProductsForImport(pageNo = 1) {
  if (!selectedErpSource.value) return
  loadingErpProducts.value = true
  selectedErpImageUrls.value = []
  erpPageNo.value = pageNo
  try {
    const page = await searchErpProducts(selectedErpSource.value, {
      pageNo,
      pageSize: erpPageSize,
      productModel: erpModelFilter.value.trim() || undefined,
      series: erpSeriesFilter.value.trim() || undefined,
    })
    erpProducts.value = page.items || []
    erpProductTotal.value = page.total || 0
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '查询 ERP 产品失败')
  } finally {
    loadingErpProducts.value = false
  }
}

/** 切换 ERP 来源或主动更新筛选时，从第一页重新浏览。 */
function resetErpProductSearch() {
  void searchErpProductsForImport(1)
}

function toggleErpProduct(product: ErpProduct) {
  const index = selectedErpImageUrls.value.indexOf(product.image_url)
  if (index >= 0) {
    selectedErpImageUrls.value.splice(index, 1)
    return
  }
  if (selectedErpImageUrls.value.length >= erpImportLimit.value) {
    ElMessage.warning(`本次最多选择 ${erpImportLimit.value} 张图片`)
    return
  }
  selectedErpImageUrls.value.push(product.image_url)
}

const selectedErpProducts = computed(() => erpProducts.value.filter(
  product => selectedErpImageUrls.value.includes(product.image_url),
))

async function importSelectedErpProducts() {
  if (!selectedErpSource.value || selectedErpProducts.value.length === 0) return
  importingErpProducts.value = true
  try {
    const result = await importErpProductImages(selectedErpSource.value, selectedErpProducts.value)
    const suffix = result.failed_count > 0 ? `，${result.failed_count} 张失败` : ''
    ElMessage.success(`已导入 ${result.imported_count} 张，复用 ${result.reused_count} 张${suffix}`)
    if (result.errors.length > 0) ElMessage.warning(result.errors[0])
    selectedErpImageUrls.value = []
    showErpImport.value = false
    await load()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '批量导入 ERP 产品图片失败')
  } finally {
    importingErpProducts.value = false
  }
}

async function confirmDelete(asset: Asset) {
  try {
    await ElMessageBox.confirm(`确定删除「${asset.filename}」？`, '删除确认', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await client.delete(`/assets/${asset.id}`)
    selectedAssetIds.value = selectedAssetIds.value.filter(id => id !== asset.id)
    ElMessage.success('已删除')
    if (selectedAsset.value?.id === asset.id) {
      selectedAsset.value = null
      showPreview.value = false
    }
    await load()
  } catch {
    // cancelled
  }
}

/** 切换素材多选状态，卡片其他区域仍保留点击预览的原有行为。 */
function toggleAssetSelection(assetId: number) {
  const selectedIndex = selectedAssetIds.value.indexOf(assetId)
  if (selectedIndex >= 0) {
    selectedAssetIds.value.splice(selectedIndex, 1)
  } else {
    selectedAssetIds.value.push(assetId)
  }
}

/**
 * 删除用户明确勾选的本地素材。
 *
 * 批量接口会对每个素材独立处理，失败项保留在选中状态，便于用户针对性重试。
 */
async function confirmBatchDelete() {
  if (selectedAssetIds.value.length === 0) return
  try {
    await ElMessageBox.confirm(
      `确定删除已选的 ${selectedAssetIds.value.length} 个素材吗？该操作会同时删除本地素材库和对象存储文件。`,
      '批量删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }

  deletingAssets.value = true
  try {
    const res = await client.post('/assets/bulk-delete', { asset_ids: selectedAssetIds.value })
    const result = res.data.data || res.data
    const failedIds: number[] = [...(result.failed_ids || []), ...(result.not_found_ids || [])]
    if (selectedAsset.value && !failedIds.includes(selectedAsset.value.id)) {
      selectedAsset.value = null
      showPreview.value = false
    }
    await load()
    // 列表刷新会清空旧勾选；仅恢复本页仍可见的失败项，避免误选其他筛选结果。
    selectedAssetIds.value = assets.value
      .filter(asset => failedIds.includes(asset.id))
      .map(asset => asset.id)
    if (failedIds.length > 0) {
      ElMessage.warning(`已删除 ${result.deleted_count} 个素材，${failedIds.length} 个未删除，请重试`)
    } else {
      ElMessage.success(`已删除 ${result.deleted_count} 个素材`)
    }
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '批量删除素材失败')
  } finally {
    deletingAssets.value = false
  }
}

function previewAsset(asset: Asset) {
  selectedAsset.value = asset
  showPreview.value = true
  docContent.value = ''
}

// ==================== Watermark ====================

async function loadWatermarkConfig() {
  watermarkLoading.value = true
  try {
    const res = await client.get('/watermark-config')
    watermarkConfig.value = res.data
    if (res.data.enabled) {
      if (res.data.watermark_type === 'logo' && res.data.logo_image_key) {
        wmForm.value.type = 'logo'
        wmForm.value.image_key = res.data.logo_image_key
        wmForm.value.scale = res.data.scale / 100
      } else if (res.data.watermark_type === 'text') {
        wmForm.value.type = 'text'
        wmForm.value.content = res.data.text_content || ''
        wmForm.value.font_size = res.data.font_size
        wmForm.value.color = res.data.color
      }
      wmForm.value.position = res.data.position
      wmForm.value.opacity = res.data.opacity / 100
      wmForm.value.margin = res.data.margin
    }
  } catch {
    // ignore
  } finally {
    watermarkLoading.value = false
  }
}

async function handleApplyWatermark() {
  if (!selectedAsset.value) return
  applyingWatermark.value = true
  try {
    const res = await client.post(`/assets/${selectedAsset.value.id}/watermark`, wmForm.value)
    Object.assign(selectedAsset.value, res.data)
    // Refresh asset in list
    const idx = assets.value.findIndex(a => a.id === selectedAsset.value!.id)
    if (idx >= 0) assets.value[idx] = { ...res.data, preview_url: res.data.preview_url }
    ElMessage.success('水印已应用')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '水印应用失败')
  } finally {
    applyingWatermark.value = false
  }
}

async function handleRemoveWatermark() {
  if (!selectedAsset.value) return
  try {
    await ElMessageBox.confirm('确定去除水印？将恢复到原始版本。', '确认', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'info',
    })
  } catch {
    return
  }
  removingWatermark.value = true
  try {
    const res = await client.delete(`/assets/${selectedAsset.value.id}/watermark`)
    Object.assign(selectedAsset.value, res.data)
    // Refresh asset in list
    const idx = assets.value.findIndex(a => a.id === selectedAsset.value!.id)
    if (idx >= 0) assets.value[idx] = { ...res.data, preview_url: res.data.preview_url }
    ElMessage.success('水印已去除')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '去除水印失败')
  } finally {
    removingWatermark.value = false
  }
}

function openWatermarkDialog() {
  showWatermarkDialog.value = true
  loadWatermarkConfig()
}

const isImageAsset = computed(() => selectedAsset.value?.asset_type === 'image')

onMounted(load)
</script>

<template>
  <div class="assets-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">ASSET LIBRARY</p>
        <h1>素材库</h1>
        <p class="lead">管理图片、视频、文档等素材，可在内容生成时直接引用。</p>
      </div>
      <div class="asset-actions">
        <el-button type="danger" plain :loading="deletingAssets" :disabled="selectedAssetCount === 0" @click="confirmBatchDelete">
          删除已选（{{ selectedAssetCount }}）
        </el-button>
        <el-button @click="openErpImport">从 ERP 产品库导入</el-button>
        <el-button type="primary" @click="showUpload = true">上传素材</el-button>
      </div>
    </div>

    <!-- Filter -->
    <div class="filter-bar">
      <el-radio-group v-model="filterSource" @change="load" style="margin-right: 16px">
        <el-radio-button value="all">全部</el-radio-button>
        <el-radio-button value="uploaded">上传的</el-radio-button>
        <el-radio-button value="generated">自动归档</el-radio-button>
        <el-radio-button value="erp">ERP 导入</el-radio-button>
      </el-radio-group>
      <el-select v-model="filterType" @change="load" style="width: 160px">
        <el-option v-for="opt in assetTypeOptions" :key="opt.value" :value="opt.value" :label="opt.label" />
      </el-select>
      <span class="asset-count">{{ total }} 个素材</span>
      <el-checkbox v-if="assets.length > 0" v-model="allVisibleAssetsSelected" class="select-all-assets">全选当前列表</el-checkbox>
    </div>

    <div v-if="loading" class="loading-section">
      <el-skeleton :rows="3" animated />
    </div>

    <div v-else-if="assets.length === 0" class="empty-state">
      <el-empty description="还没有素材">
        <el-button type="primary" @click="showUpload = true">上传第一个素材</el-button>
      </el-empty>
    </div>

    <div v-else class="asset-grid">
      <div v-for="asset in assets" :key="asset.id" class="asset-card" @click="previewAsset(asset)">
        <div class="asset-select" @click.stop>
          <el-checkbox :model-value="selectedAssetIds.includes(asset.id)" @change="toggleAssetSelection(asset.id)" />
        </div>
        <div v-if="asset.asset_type === 'image' && asset.preview_url" class="asset-thumb">
          <img :src="asset.preview_url" :alt="asset.filename" class="thumb-img" loading="lazy" />
        </div>
        <div v-else class="asset-thumb asset-thumb-doc">
          <span class="file-icon">{{ asset.asset_type === 'video' ? '🎬' : '📄' }}</span>
        </div>
        <div class="asset-info">
          <strong class="asset-name" :title="asset.original_filename || asset.filename">{{ asset.original_filename || asset.filename }}</strong>
          <div class="asset-meta">
            <span>{{ assetTypeNames[asset.asset_type] || asset.asset_type }}</span>
            <span>{{ formatSize(asset.file_size) }}</span>
            <span v-if="asset.width && asset.height">{{ asset.width }}×{{ asset.height }}</span>
          </div>
          <div v-if="asset.tags && asset.tags.length" class="asset-tags">
            <span v-for="tag in asset.tags.slice(0, 3)" :key="tag" class="tag">{{ tag }}</span>
            <span v-if="asset.tags.length > 3" class="tag-more">+{{ asset.tags.length - 3 }}</span>
          </div>
        </div>
        <button class="asset-delete" @click.stop="confirmDelete(asset)">×</button>
      </div>
    </div>

    <!-- Preview Dialog -->
    <el-dialog v-model="showPreview" title="素材预览" width="560px" top="5vh">
      <div v-if="selectedAsset">
        <div v-if="selectedAsset.preview_url && selectedAsset.asset_type === 'image'" class="preview-media">
          <img :src="selectedAsset.preview_url" class="preview-image" />
        </div>
        <div v-else-if="selectedAsset.preview_url && selectedAsset.asset_type === 'video'" class="preview-media">
          <video :src="selectedAsset.preview_url" controls class="preview-video"></video>
        </div>
        <div v-else class="preview-placeholder">
          <span style="font-size: 48px">📄</span>
          <p>暂不支持在线预览</p>
        </div>
        <el-descriptions :column="1" border size="small" class="preview-meta">
          <el-descriptions-item label="文件名">{{ selectedAsset.original_filename || selectedAsset.filename }}</el-descriptions-item>
          <el-descriptions-item label="类型">{{ selectedAsset.mime_type }}</el-descriptions-item>
          <el-descriptions-item label="大小">{{ formatSize(selectedAsset.file_size) }}</el-descriptions-item>
          <el-descriptions-item v-if="selectedAsset.width && selectedAsset.height" label="尺寸">
            {{ selectedAsset.width }}×{{ selectedAsset.height }}
          </el-descriptions-item>
          <el-descriptions-item label="上传时间">
            {{ selectedAsset.created_at ? new Date(selectedAsset.created_at).toLocaleString('zh-CN') : '-' }}
          </el-descriptions-item>
        </el-descriptions>
        <el-divider />
        <div class="preview-actions">
          <a v-if="selectedAsset.preview_url" :href="selectedAsset.preview_url" target="_blank">
            <el-button size="small">下载</el-button>
          </a>
          <template v-if="isImageAsset">
            <el-button
              v-if="!selectedAsset.is_watermarked"
              size="small"
              type="warning"
              plain
              @click="openWatermarkDialog"
            >
              添加水印
            </el-button>
            <el-button
              v-else
              size="small"
              type="danger"
              plain
              :loading="removingWatermark"
              @click="handleRemoveWatermark"
            >
              {{ removingWatermark ? '去除中...' : '去除水印' }}
            </el-button>
            <span v-if="selectedAsset.is_watermarked" class="watermark-badge">已添加水印</span>
          </template>
        </div>
      </div>
    </el-dialog>

    <!-- Watermark Config Dialog -->
    <el-dialog v-model="showWatermarkDialog" title="添加水印" width="520px" top="5vh">
      <div v-if="watermarkLoading" style="padding: 24px; text-align: center;">
        <el-skeleton :rows="3" animated />
      </div>
      <template v-else>
        <el-form label-position="top" size="small">
          <el-form-item label="水印类型">
            <el-radio-group v-model="wmForm.type">
              <el-radio value="logo">Logo 图片</el-radio>
              <el-radio value="text">文字</el-radio>
            </el-radio-group>
          </el-form-item>

          <template v-if="wmForm.type === 'logo'">
            <el-form-item label="Logo 图片 Key">
              <el-input v-model="wmForm.image_key" placeholder="MinIO 中的 Logo 图片 key" />
              <span class="form-hint">请先在「水印设置」中上传 Logo</span>
            </el-form-item>
            <el-form-item label="缩放比例">
              <el-slider v-model="wmForm.scale" :min="0.05" :max="0.4" :step="0.01" show-input />
            </el-form-item>
          </template>

          <template v-else>
            <el-form-item label="水印文字">
              <el-input v-model="wmForm.content" placeholder="输入水印文字" maxlength="100" />
            </el-form-item>
            <el-row :gutter="12">
              <el-col :span="12">
                <el-form-item label="字号">
                  <el-input-number v-model="wmForm.font_size" :min="12" :max="120" :step="4" />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="颜色">
                  <el-color-picker v-model="wmForm.color" />
                </el-form-item>
              </el-col>
            </el-row>
          </template>

          <el-row :gutter="12">
            <el-col :span="8">
              <el-form-item label="位置">
                <el-select v-model="wmForm.position" style="width:100%">
                  <el-option v-for="opt in wmPositionOptions" :key="opt.value" :value="opt.value" :label="opt.label" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="透明度">
                <el-slider v-model="wmForm.opacity" :min="0.1" :max="1" :step="0.05" show-input />
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="边距">
                <el-input-number v-model="wmForm.margin" :min="0" :max="100" :step="5" />
              </el-form-item>
            </el-col>
          </el-row>
        </el-form>
        <p style="color: #909399; font-size: 12px; margin-top: 8px;">
          此操作仅对当前素材生效。如需全局默认水印，请在「水印设置」中配置。
        </p>
      </template>
      <template #footer>
        <el-button @click="showWatermarkDialog = false">取消</el-button>
        <el-button type="primary" :loading="applyingWatermark" :disabled="watermarkLoading" @click="handleApplyWatermark">
          {{ applyingWatermark ? '应用中...' : '应用水印' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- Upload Dialog -->
    <el-dialog v-model="showUpload" title="上传素材" width="480px">
      <el-form label-position="top">
        <el-form-item label="选择文件">
          <input type="file" @change="handleFileChange" />
          <span v-if="uploadFile" class="file-hint">{{ uploadFile.name }} ({{ formatSize(uploadFile.size) }})</span>
        </el-form-item>
        <el-form-item label="标签（可选，逗号分隔）">
          <el-input v-model="uploadTags" placeholder="如：封面, banner, 产品图" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showUpload = false">取消</el-button>
        <el-button type="primary" :loading="uploading" :disabled="!uploadFile" @click="uploadAsset">
          {{ uploading ? '上传中...' : '上传' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ERP 导入是素材库管理动作，不与文章编辑时的配图选择混在一起。 -->
    <el-dialog v-model="showErpImport" title="从 ERP 产品库导入" width="900px" top="5vh">
      <el-form inline class="erp-import-filters">
        <el-form-item label="品牌"><el-select v-model="selectedErpSource" style="width:160px" @change="resetErpProductSearch"><el-option v-for="source in erpSources" :key="source.key" :label="source.name" :value="source.key" /></el-select></el-form-item>
        <el-form-item label="产品型号"><el-input v-model="erpModelFilter" clearable placeholder="可选" @keyup.enter="searchErpProductsForImport" /></el-form-item>
        <el-form-item label="系列"><el-input v-model="erpSeriesFilter" clearable placeholder="可选" @keyup.enter="searchErpProductsForImport" /></el-form-item>
        <el-form-item><el-button type="primary" :loading="loadingErpProducts" @click="resetErpProductSearch">查询</el-button></el-form-item>
      </el-form>
      <div class="erp-import-toolbar">
        <span>本次导入上限</span>
        <el-input-number v-model="erpImportLimit" :min="1" :max="20" :step="1" controls-position="right" />
        <span class="form-hint">已选 {{ selectedErpImageUrls.length }} / {{ erpImportLimit }}，系统最多允许 20 张</span>
      </div>
      <div v-if="loadingErpProducts" class="loading-section"><el-skeleton :rows="3" animated /></div>
      <div v-else-if="erpProducts.length === 0" class="empty-state"><el-empty description="未查询到带报价图片的产品" /></div>
      <div v-else class="erp-import-grid">
        <button v-for="product in erpProducts" :key="product.image_url" type="button" class="erp-import-card" :class="{ selected: selectedErpImageUrls.includes(product.image_url) }" @click="toggleErpProduct(product)">
          <img :src="product.image_url" :alt="product.name" loading="lazy" />
          <strong>{{ product.name }}</strong>
          <span>{{ product.series.join(' / ') || product.categories.join(' / ') || '未分类' }}</span>
          <i v-if="selectedErpImageUrls.includes(product.image_url)">已选择</i>
        </button>
      </div>
      <div v-if="erpProductTotal > erpPageSize" class="erp-pagination">
        <el-pagination
          v-model:current-page="erpPageNo"
          :page-size="erpPageSize"
          :total="erpProductTotal"
          layout="prev, pager, next, jumper, total"
          @current-change="searchErpProductsForImport"
        />
      </div>
      <template #footer>
        <el-button @click="showErpImport = false">取消</el-button>
        <el-button type="primary" :loading="importingErpProducts" :disabled="selectedErpProducts.length === 0" @click="importSelectedErpProducts">导入 {{ selectedErpProducts.length }} 张到本地素材库</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.assets-page {
  max-width: 1200px;
}

.page-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 28px;
}

.asset-actions { display: flex; gap: 8px; }

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

.filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}

.asset-count {
  color: #909399;
  font-size: 12px;
}

.select-all-assets {
  margin-left: auto;
  color: #606266;
}

.loading-section {
  padding: 40px 0;
}

.empty-state {
  padding: 60px 0;
}

.asset-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 16px;
}

.asset-card {
  position: relative;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow: hidden;
  cursor: pointer;
  transition: box-shadow 0.2s;
  background: #fff;
}

.asset-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}

.asset-select {
  position: absolute;
  z-index: 2;
  top: 8px;
  left: 8px;
  display: grid;
  width: 24px;
  height: 24px;
  place-items: center;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.9);
}

.asset-thumb {
  display: grid;
  height: 140px;
  place-items: center;
  overflow: hidden;
  background: #f5f7fa;
}

.asset-thumb-doc {
  background: #faf8f5;
}

.thumb-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.file-icon {
  font-size: 36px;
}

.asset-info {
  padding: 12px;
}

.asset-name {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
}

.asset-meta {
  display: flex;
  gap: 10px;
  margin-top: 4px;
  color: #909399;
  font-size: 11px;
}

.asset-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 8px;
}

.tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 4px;
  background: #ecf3f0;
  font-size: 10px;
  color: #606266;
}

.tag-more {
  font-size: 10px;
  color: #c0c4cc;
}

.asset-delete {
  position: absolute;
  top: 6px;
  right: 6px;
  display: none;
  width: 24px;
  height: 24px;
  border: 0;
  border-radius: 50%;
  color: #fff;
  background: rgba(0, 0, 0, 0.5);
  font-size: 14px;
  cursor: pointer;
}

.asset-card:hover .asset-delete {
  display: grid;
  place-items: center;
}

.asset-delete:hover {
  background: rgba(185, 28, 28, 0.8);
}

.preview-media {
  display: flex;
  justify-content: center;
  margin-bottom: 16px;
  padding: 8px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fafcfb;
}

.preview-image {
  max-width: 100%;
  max-height: 320px;
  object-fit: contain;
  border-radius: 4px;
}

.preview-video {
  width: 100%;
  max-height: 320px;
  border-radius: 4px;
}

.preview-placeholder {
  display: grid;
  place-items: center;
  padding: 40px 0;
  color: #909399;
}

.preview-placeholder p {
  margin-top: 8px;
  font-size: 13px;
}

.preview-meta {
  margin: 16px 0;
}

.preview-actions {
  display: flex;
  gap: 8px;
}

.file-hint {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}

.watermark-badge {
  font-size: 11px;
  color: #e6a23c;
  background: #fdf6ec;
  padding: 2px 8px;
  border-radius: 4px;
  margin-left: 8px;
}

.form-hint {
  display: block;
  font-size: 11px;
  color: #909399;
  margin-top: 2px;
}

.erp-import-filters { display: flex; flex-wrap: wrap; align-items: center; }
.erp-import-toolbar { display: flex; align-items: center; gap: 10px; margin: 8px 0 14px; }
.erp-import-toolbar .form-hint { margin: 0; }
.erp-import-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; max-height: 480px; overflow-y: auto; }
.erp-pagination { display: flex; justify-content: flex-end; margin-top: 16px; }
.erp-import-card { position: relative; display: grid; gap: 6px; padding: 8px; overflow: hidden; text-align: left; color: #606266; background: #fff; border: 1px solid #dcdfe6; border-radius: 6px; cursor: pointer; }
.erp-import-card:hover, .erp-import-card.selected { border-color: #409eff; background: #ecf5ff; }
.erp-import-card img { width: 100%; height: 125px; object-fit: cover; border-radius: 4px; background: #f5f7fa; }
.erp-import-card strong { overflow: hidden; color: #303133; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.erp-import-card span { overflow: hidden; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.erp-import-card i { position: absolute; top: 8px; right: 8px; padding: 3px 6px; color: #fff; font-size: 11px; font-style: normal; background: #409eff; border-radius: 3px; }
</style>

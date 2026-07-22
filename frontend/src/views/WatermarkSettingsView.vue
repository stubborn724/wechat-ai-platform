<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'

interface WatermarkConfig {
  enabled: boolean
  watermark_type: string
  logo_image_key: string | null
  logo_url: string | null
  scale: number
  text_content: string | null
  font_size: number
  position: string
  opacity: number
  color: string
  margin: number
  updated_at: string | null
}

const loading = ref(false)
const saving = ref(false)
const config = ref<WatermarkConfig>({
  enabled: false,
  watermark_type: 'logo',
  logo_image_key: null,
  logo_url: null,
  scale: 15,
  text_content: null,
  font_size: 36,
  position: 'bottom-right',
  opacity: 80,
  color: '#FFFFFF',
  margin: 20,
  updated_at: null,
})

const uploadingLogo = ref(false)
const logoInputRef = ref<HTMLInputElement | null>(null)

// Preview
const previewDialog = ref(false)
const previewLoading = ref(false)
const previewResultUrl = ref('')
const assets = ref<any[]>([])
const selectedAssetId = ref<number | null>(null)

const positionOptions = [
  { value: 'top-left', label: '左上角' },
  { value: 'top-right', label: '右上角' },
  { value: 'bottom-left', label: '左下角' },
  { value: 'bottom-right', label: '右下角' },
  { value: 'center', label: '居中' },
]

async function loadConfig() {
  loading.value = true
  try {
    const res = await client.get<WatermarkConfig>('/watermark-config')
    config.value = res.data
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '加载水印配置失败')
  } finally {
    loading.value = false
  }
}

async function saveConfig() {
  saving.value = true
  try {
    const res = await client.put<WatermarkConfig>('/watermark-config', config.value)
    config.value = res.data
    ElMessage.success(config.value.enabled ? '水印已开启' : '水印已关闭')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

function triggerLogoUpload() {
  logoInputRef.value?.click()
}

async function handleLogoUpload(event: Event) {
  const input = event.target as HTMLInputElement
  if (!input.files?.length) return
  uploadingLogo.value = true
  try {
    const formData = new FormData()
    formData.append('file', input.files[0])
    const res = await client.post('/watermark-config/upload-logo', formData)
    config.value.logo_image_key = res.data.image_key
    config.value.logo_url = res.data.url
    ElMessage.success('Logo 上传成功')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '上传失败')
  } finally {
    uploadingLogo.value = false
    input.value = '' as any
  }
}

function removeLogo() {
  config.value.logo_image_key = null
  config.value.logo_url = null
}

// ----- Preview -----

async function openPreview() {
  previewDialog.value = true
  previewResultUrl.value = ''
  selectedAssetId.value = null
  try {
    const res = await client.get('/assets', {
      params: { page: 1, page_size: 50, type: 'image' },
    })
    assets.value = res.data.items || []
  } catch {
    assets.value = []
  }
}

async function handlePreview() {
  if (!selectedAssetId.value) {
    ElMessage.warning('请选择一张图片')
    return
  }
  // Validate
  if (config.value.watermark_type === 'logo' && !config.value.logo_image_key) {
    ElMessage.warning('请先上传 Logo 图片')
    return
  }
  if (config.value.watermark_type === 'text' && !config.value.text_content?.trim()) {
    ElMessage.warning('请先输入水印文字')
    return
  }

  previewLoading.value = true
  previewResultUrl.value = ''
  try {
    const res = await client.post('/watermark-config/preview', {
      asset_id: selectedAssetId.value,
      watermark_type: config.value.watermark_type,
      logo_image_key: config.value.logo_image_key,
      text_content: config.value.text_content,
      scale: config.value.scale,
      font_size: config.value.font_size,
      position: config.value.position,
      opacity: config.value.opacity,
      color: config.value.color,
      margin: config.value.margin,
    })
    previewResultUrl.value = res.data.preview_url
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '预览生成失败')
  } finally {
    previewLoading.value = false
  }
}

const hasPreviewableConfig = computed(() => {
  if (config.value.watermark_type === 'logo') return !!config.value.logo_image_key
  return !!config.value.text_content?.trim()
})

onMounted(loadConfig)
</script>

<template>
  <div class="watermark-settings">
    <div class="page-heading">
      <div>
        <p class="eyebrow">WATERMARK</p>
        <h1>水印设置</h1>
        <p class="lead">配置图片水印全局默认样式，开启后自动为上传和生成的图片叠加水印。</p>
      </div>
    </div>

    <div v-if="loading" style="padding: 40px 0;">
      <el-skeleton :rows="5" animated />
    </div>

    <template v-else>
      <el-card class="config-card">
        <el-form label-position="top">
          <!-- Enable toggle -->
          <el-form-item>
            <el-switch
              v-model="config.enabled"
              active-text="开启水印"
              inactive-text="关闭水印"
              size="large"
            />
          </el-form-item>

          <template v-if="config.enabled">
            <el-divider />

            <!-- Watermark type -->
            <el-form-item label="水印类型">
              <el-radio-group v-model="config.watermark_type">
                <el-radio value="logo">Logo 图片水印</el-radio>
                <el-radio value="text">文字水印</el-radio>
              </el-radio-group>
            </el-form-item>

            <!-- Logo mode -->
            <template v-if="config.watermark_type === 'logo'">
              <el-form-item label="Logo 图片">
                <div class="logo-upload-area">
                  <input
                    ref="logoInputRef"
                    type="file"
                    accept="image/*"
                    style="display:none"
                    @change="handleLogoUpload"
                  />
                  <div v-if="config.logo_url" class="logo-preview">
                    <img :src="config.logo_url" class="logo-img" />
                    <el-button size="small" type="danger" plain @click="removeLogo">移除</el-button>
                  </div>
                  <el-button type="primary" plain :loading="uploadingLogo" @click="triggerLogoUpload">
                    {{ uploadingLogo ? '上传中...' : '上传 Logo' }}
                  </el-button>
                  <span class="form-hint">推荐透明背景 PNG，将自动缩放到水印尺寸</span>
                </div>
              </el-form-item>

              <el-form-item label="Logo 缩放比例">
                <div class="slider-wrapper">
                  <el-slider v-model="config.scale" :min="5" :max="40" :step="1" show-input style="width: 300px" />
                  <span class="slider-label">相对原图宽度的 {{ config.scale }}%</span>
                </div>
              </el-form-item>
            </template>

            <!-- Text mode -->
            <template v-if="config.watermark_type === 'text'">
              <el-form-item label="水印文字内容">
                <el-input
                  v-model="config.text_content"
                  placeholder="输入水印文字，如：© 公众号名称"
                  maxlength="100"
                  style="max-width: 400px"
                />
              </el-form-item>
              <el-row :gutter="20">
                <el-col :span="12">
                  <el-form-item label="字号">
                    <el-input-number v-model="config.font_size" :min="12" :max="120" :step="4" />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="文字颜色">
                    <el-color-picker v-model="config.color" show-alpha />
                    <span class="form-hint" style="margin-left:8px">{{ config.color }}</span>
                  </el-form-item>
                </el-col>
              </el-row>
            </template>

            <el-divider />

            <!-- Common settings -->
            <el-row :gutter="20">
              <el-col :span="8">
                <el-form-item label="位置">
                  <el-select v-model="config.position" style="width:100%">
                    <el-option v-for="opt in positionOptions" :key="opt.value" :value="opt.value" :label="opt.label" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="透明度">
                  <el-slider
                    v-model="config.opacity"
                    :min="10"
                    :max="100"
                    :step="5"
                    show-input
                  />
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="边距 (px)">
                  <el-input-number v-model="config.margin" :min="0" :max="100" :step="5" />
                </el-form-item>
              </el-col>
            </el-row>
          </template>

          <el-divider />
          <div class="form-actions">
            <el-button type="primary" size="large" :loading="saving" @click="saveConfig">
              {{ saving ? '保存中...' : '保存设置' }}
            </el-button>
            <el-button
              v-if="hasPreviewableConfig"
              size="large"
              @click="openPreview"
            >
              预览效果
            </el-button>
          </div>
        </el-form>
      </el-card>

      <!-- Preview Dialog -->
      <el-dialog v-model="previewDialog" title="水印效果预览" width="760px" top="5vh">
        <div style="margin-bottom: 16px;">
          <p style="color: #909399; font-size: 13px; margin-bottom: 12px;">
            从素材库中选择一张图片，预览水印叠加效果：
          </p>
          <div class="asset-selector">
            <el-select
              v-model="selectedAssetId"
              placeholder="选择一张图片"
              style="flex:1"
              @change="previewResultUrl = ''"
            >
              <el-option
                v-for="asset in assets"
                :key="asset.id"
                :value="asset.id"
                :label="asset.original_filename || asset.filename"
              >
                <span style="float:left">{{ asset.original_filename || asset.filename }}</span>
                <span style="float:right; font-size:12px; color:#909399">{{ asset.width }}×{{ asset.height }}</span>
              </el-option>
            </el-select>
            <el-button
              type="primary"
              :loading="previewLoading"
              :disabled="!selectedAssetId"
              @click="handlePreview"
            >
              {{ previewLoading ? '生成中...' : '生成预览' }}
            </el-button>
          </div>
        </div>

        <div v-if="previewResultUrl" class="preview-result">
          <div class="preview-image-wrapper">
            <img :src="previewResultUrl" class="preview-image" />
          </div>
          <div class="preview-config-summary">
            <el-tag size="small" type="info">{{ config.watermark_type === 'logo' ? 'Logo 水印' : '文字水印' }}</el-tag>
            <el-tag size="small">{{ positionOptions.find(o => o.value === config.position)?.label || config.position }}</el-tag>
            <el-tag size="small">透明度 {{ config.opacity }}%</el-tag>
          </div>
          <p style="color: #909399; font-size: 12px; text-align: center; margin-top: 12px;">
            预览为临时生成。满意后点击「保存设置」生效，之后新上传/生成的图片将自动添加水印。
          </p>
        </div>

        <div v-else-if="assets.length === 0" class="preview-empty">
          <p>素材库中没有图片，请先上传图片素材</p>
        </div>
        <div v-else class="preview-empty">
          <p>选择图片后点击「生成预览」查看水印效果</p>
        </div>
      </el-dialog>
    </template>
  </div>
</template>

<style scoped>
.watermark-settings {
  max-width: 800px;
}

.page-heading {
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

.config-card {
  margin-bottom: 24px;
}

.form-hint {
  font-size: 12px;
  color: #909399;
  margin-left: 8px;
}

.logo-upload-area {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.logo-preview {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo-img {
  max-width: 120px;
  max-height: 60px;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  padding: 4px;
  background: #fafcfb;
}

.slider-wrapper {
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
}

.slider-label {
  font-size: 12px;
  color: #909399;
  white-space: nowrap;
}

.form-actions {
  display: flex;
  gap: 12px;
}

/* Preview */
.asset-selector {
  display: flex;
  gap: 12px;
  align-items: center;
}

.preview-result {
  text-align: center;
}

.preview-image-wrapper {
  padding: 8px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fafcfb;
  margin-bottom: 12px;
}

.preview-image {
  max-width: 100%;
  max-height: 480px;
  border-radius: 4px;
}

.preview-config-summary {
  display: flex;
  gap: 8px;
  justify-content: center;
  flex-wrap: wrap;
}

.preview-empty {
  text-align: center;
  padding: 60px 0;
  color: #909399;
}
</style>

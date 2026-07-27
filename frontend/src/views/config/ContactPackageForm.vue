<template>
  <div class="config-page">
    <div class="page-header">
      <el-button text @click="$router.push('/leads/packages')">← 返回列表</el-button>
      <h3>{{ isEdit ? '编辑' : '新建' }}资料包</h3>
    </div>

    <el-form :model="form" label-width="100px" style="max-width:600px">
      <el-form-item label="公众号" required>
        <el-select v-model="form.account_id" placeholder="选择公众号" style="width:100%" :disabled="isEdit">
          <el-option v-for="a in accounts" :key="a.id" :label="a.name || a.app_id" :value="a.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="名称" required>
        <el-input v-model="form.name" placeholder="如：家具咨询默认资料" />
      </el-form-item>
      <el-form-item label="说明">
        <el-input v-model="form.description" type="textarea" :rows="2" placeholder="内部备注用途" />
      </el-form-item>
      <el-divider>联系方式</el-divider>
      <el-form-item label="联系人">
        <el-input v-model="form.contact_name" placeholder="如：小林" />
      </el-form-item>
      <el-form-item label="微信号">
        <el-input v-model="form.wechat_id" placeholder="如：ABC123456" />
      </el-form-item>
      <el-form-item label="电话">
        <el-input v-model="form.phone" placeholder="如：138****8888" />
      </el-form-item>
      <el-form-item label="欢迎语">
        <el-input v-model="form.text_content" type="textarea" :rows="3" placeholder="发送给用户的联系文案" />
      </el-form-item>
      <el-divider>二维码素材</el-divider>
      <el-form-item label="二维码图片">
        <el-select v-model="form.qr_asset_id" placeholder="选择素材" filterable clearable style="width:100%">
          <el-option v-for="a in imageAssets" :key="a.id" :label="a.original_filename || a.storage_key" :value="a.id" />
        </el-select>
        <div class="form-tip">选填，未配置二维码时无法启用资料包</div>
      </el-form-item>
      <el-form-item>
        <el-checkbox v-model="form.is_default">设为默认资料包</el-checkbox>
        <el-checkbox v-model="form.is_enabled" style="margin-left:16px">创建后启用</el-checkbox>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
        <el-button @click="$router.push('/leads/packages')">取消</el-button>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { createContactPackage, updateContactPackage, getContactPackage } from '@/api/wechat'
import client from '@/api/client'

const route = useRoute()
const router = useRouter()
const isEdit = computed(() => !!route.params.id)
const saving = ref(false)
const accounts = ref<any[]>([])
const imageAssets = ref<any[]>([])

const form = ref({
  account_id: undefined as number | undefined,
  name: '',
  description: '',
  contact_name: '',
  wechat_id: '',
  phone: '',
  text_content: '',
  qr_asset_id: undefined as number | undefined,
  is_default: false,
  is_enabled: false,
})

async function fetchAccounts() {
  try {
    const res = await client.get('/accounts')
    accounts.value = (res.data?.items || res.data || []).filter((a: any) => a.id != null)
  } catch { /* ignore */ }
}

async function fetchAssets() {
  try {
    const res = await client.get('/assets', { params: { type: 'image', page_size: 100 } })
    imageAssets.value = res.data.items || []
  } catch { /* ignore */ }
}

async function loadPackage() {
  if (!route.params.id) return
  try {
    const pkg = await getContactPackage(Number(route.params.id))
    form.value = {
      account_id: pkg.account_id,
      name: pkg.name,
      description: pkg.description || '',
      contact_name: pkg.contact_name || '',
      wechat_id: pkg.wechat_id || '',
      phone: pkg.phone || '',
      text_content: pkg.text_content || '',
      qr_asset_id: pkg.qr_asset_id,
      is_default: pkg.is_default,
      is_enabled: pkg.is_enabled,
    }
  } catch { ElMessage.error('加载资料包失败') }
}

async function handleSave() {
  if (!form.value.account_id) { ElMessage.warning('请选择公众号'); return }
  if (!form.value.name.trim()) { ElMessage.warning('请输入名称'); return }
  saving.value = true
  try {
    if (isEdit.value) {
      await updateContactPackage(Number(route.params.id), form.value)
      ElMessage.success('保存成功')
    } else {
      await createContactPackage(form.value)
      ElMessage.success('创建成功')
    }
    router.push('/leads/packages')
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || e.message || '')
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await Promise.all([fetchAccounts(), fetchAssets()])
  if (isEdit.value) await loadPackage()
})
</script>
<style scoped>
.config-page { padding: 16px; }
.page-header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.page-header h3 { margin: 0; }
.form-tip { font-size: 12px; color: #909399; margin-top: 4px; }
</style>

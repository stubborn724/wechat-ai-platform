<template>
  <div class="config-page">
    <div class="page-header">
      <div class="header-left">
        <el-button text @click="$router.push('/leads')">← 返回工作台</el-button>
        <h3>联系资料包</h3>
      </div>
      <el-button type="primary" @click="$router.push('/leads/packages/new')">新建资料包</el-button>
    </div>

    <div class="filter-bar">
      <el-select v-model="filterAccount" placeholder="公众号" clearable size="small" style="width:180px" @change="fetchData">
        <el-option v-for="a in accounts" :key="a.id" :label="a.name || a.app_id" :value="a.id" />
      </el-select>
      <el-checkbox v-model="filterEnabled" label="仅显示已启用" @change="fetchData" />
      <el-input v-model="filterKeyword" placeholder="搜索名称" clearable size="small" style="width:200px" @keyup.enter="fetchData" />
      <el-button size="small" @click="fetchData">搜索</el-button>
    </div>

    <el-table :data="packages" v-loading="loading" stripe size="small">
      <el-table-column label="公众号" width="120">
        <template #default="{ row }">{{ row.account_name }}</template>
      </el-table-column>
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column prop="contact_name" label="联系人" width="90" />
      <el-table-column prop="wechat_id" label="微信号" width="120" />
      <el-table-column prop="phone" label="电话" width="110" />
      <el-table-column label="二维码" width="60" align="center">
        <template #default="{ row }">
          <el-tag v-if="row.media_status === 'ready'" size="small" type="success">就绪</el-tag>
          <el-tag v-else-if="row.qr_asset_id" size="small" type="warning">{{ row.media_status || '待上传' }}</el-tag>
          <span v-else class="no-qr">-</span>
        </template>
      </el-table-column>
      <el-table-column label="默认" width="60" align="center">
        <template #default="{ row }">
          <el-tag v-if="row.is_default" size="small" type="success">默认</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="70" align="center">
        <template #default="{ row }">
          <el-tag v-if="row.is_enabled" size="small" type="success">启用</el-tag>
          <el-tag v-else size="small" type="info">停用</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="usage_count" label="使用" width="60" align="center" />
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text @click="$router.push(`/leads/packages/${row.id}/edit`)">编辑</el-button>
          <el-button v-if="!row.is_enabled" size="small" text type="success" @click="handleEnable(row)">启用</el-button>
          <el-button v-else size="small" text type="warning" @click="handleDisable(row)">停用</el-button>
          <el-button size="small" text type="danger" @click="handleDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pagination-wrap" v-if="total > pageSize">
      <el-pagination v-model:current-page="page" :page-size="pageSize" :total="total"
        layout="prev, pager, next" small @current-change="fetchData" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listContactPackages, enableContactPackage, disableContactPackage, deleteContactPackage } from '@/api/wechat'
import client from '@/api/client'

const packages = ref<any[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const accounts = ref<any[]>([])
const filterAccount = ref<number | undefined>()
const filterEnabled = ref(false)
const filterKeyword = ref('')

async function fetchAccounts() {
  try {
    const res = await client.get('/accounts')
    accounts.value = (res.data?.items || res.data || []).filter((a: any) => a.id != null)
  } catch { /* ignore */ }
}

async function fetchData() {
  loading.value = true
  try {
    const res = await listContactPackages({
      account_id: filterAccount.value,
      enabled: filterEnabled.value || undefined,
      keyword: filterKeyword.value || undefined,
      page: page.value, page_size: pageSize.value,
    })
    packages.value = res.items
    total.value = res.total
  } catch (e: any) {
    ElMessage.error('加载失败：' + (e.message || ''))
  } finally {
    loading.value = false
  }
}

async function handleEnable(row: any) {
  try { await enableContactPackage(row.id); ElMessage.success('已启用'); fetchData() }
  catch (e: any) { ElMessage.error(e.response?.data?.detail || e.message) }
}

async function handleDisable(row: any) {
  try { await disableContactPackage(row.id); ElMessage.success('已停用'); fetchData() }
  catch (e: any) { ElMessage.error(e.response?.data?.detail || e.message) }
}

async function handleDelete(row: any) {
  try {
    await ElMessageBox.confirm(`确认删除资料包「${row.name}」？`, '确认')
    await deleteContactPackage(row.id)
    ElMessage.success('已删除')
    fetchData()
  } catch { /* cancelled */ }
}

onMounted(async () => { await fetchAccounts(); await fetchData() })
</script>
<style scoped>
.config-page { padding: 16px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.header-left { display: flex; align-items: center; gap: 12px; }
.header-left h3 { margin: 0; }
.filter-bar { display: flex; gap: 8px; align-items: center; margin-bottom: 16px; flex-wrap: wrap; }
.pagination-wrap { margin-top: 16px; display: flex; justify-content: center; }
.no-qr { color: #c0c4cc; }
</style>

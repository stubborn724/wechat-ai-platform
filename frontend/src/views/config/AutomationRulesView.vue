<template>
  <div class="config-page">
    <div class="page-header">
      <el-button text @click="$router.push('/leads')">← 返回工作台</el-button>
      <h3>自动化规则</h3>
    </div>
    <p class="placeholder">P3 实现：自动化规则配置 + 意图触发 + 等级管理</p>
  </div>
</template>
<script setup lang="ts">
</script>
<style scoped>
.config-page { padding: 16px; }
.page-header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.page-header h3 { margin: 0; }
.placeholder { color: #909399; text-align: center; padding: 48px; }
</style>

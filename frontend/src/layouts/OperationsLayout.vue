<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import {
  Odometer, ChatDotSquare, Document, Edit, List, Picture,
  Connection, Collection, Calendar, Finished, Fold, Expand,
  ArrowDown, Key, CopyDocument,
} from '@element-plus/icons-vue'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const isCollapsed = ref(false)

const roleNames: Record<string, string> = {
  super_administrator: '超级管理员',
  enterprise_administrator: '企业管理员',
  content_operator: '内容运营',
  reviewer: '内容审核',
  analyst: '数据分析',
  read_only_visitor: '只读访客',
}

const canReview = computed(() =>
  ['super_administrator', 'enterprise_administrator', 'reviewer'].includes(auth.user?.role || '')
)

interface MenuItem {
  index: string
  route: string
  label: string
  icon: string
  show: boolean
}

const menuItems = computed<MenuItem[]>(() => [
  { index: '1', route: '/dashboard', label: '仪表盘', icon: 'Odometer', show: true },
  { index: '2', route: '/accounts', label: '公众号', icon: 'ChatDotSquare', show: true },
  { index: '3', route: '/articles/list', label: '文章管理', icon: 'Document', show: true },
  { index: '4', route: '/articles', label: '创建文章', icon: 'Edit', show: true },
  { index: '5', route: '/content', label: '内容任务', icon: 'List', show: true },
  { index: '6', route: '/assets', label: '素材库', icon: 'Picture', show: true },
  { index: '7', route: '/feed-sources', label: '投喂源', icon: 'Connection', show: true },
  { index: '8', route: '/knowledge', label: '知识库', icon: 'Collection', show: true },
  { index: '9', route: '/publish-plans', label: '发布计划', icon: 'Calendar', show: true },
  { index: '10', route: '/reviews', label: '审核台', icon: 'Finished', show: canReview.value },
  { index: '11', route: '/wechat-oauth', label: '扫码授权', icon: 'Key', show: true },
  { index: '12', route: '/imitation/pools', label: '仿写池', icon: 'CopyDocument', show: true },
  { index: '13', route: '/imitation/tasks', label: '仿写任务', icon: 'Calendar', show: true },
].filter(item => item.show))

const activeRoute = computed(() => route.path)

function handleSelect(index: string) {
  router.push(index)
}

async function signOut() {
  await auth.logout()
  router.replace('/login')
}

const displayName = computed(() => auth.user?.display_name || auth.user?.email || '用户')
const userAvatar = computed(() => displayName.value.slice(0, 1).toUpperCase())
</script>

<template>
  <el-container class="layout-container">
    <!-- Sidebar -->
    <el-aside :width="isCollapsed ? '64px' : '220px'" class="app-aside">
      <div class="aside-inner">
        <!-- Brand -->
        <div class="brand" :class="{ collapsed: isCollapsed }">
          <div class="brand-icon">微</div>
          <transition name="fade">
            <span v-show="!isCollapsed" class="brand-text">AI 运营平台</span>
          </transition>
        </div>

        <!-- Navigation Menu -->
        <el-menu
          :default-active="activeRoute"
          :collapse="isCollapsed"
          :collapse-transition="false"
          background-color="#1d1e1f"
          text-color="#bfcbd9"
          active-text-color="#409eff"
          @select="handleSelect"
        >
          <el-menu-item
            v-for="item in menuItems"
            :key="item.index"
            :index="item.route"
          >
            <el-icon>
              <component :is="item.icon" />
            </el-icon>
            <template #title>
              <span>{{ item.label }}</span>
            </template>
          </el-menu-item>
        </el-menu>

        <!-- Collapse Toggle at bottom -->
        <div class="collapse-toggle" @click="isCollapsed = !isCollapsed">
          <el-icon>
            <Fold v-if="!isCollapsed" />
            <Expand v-else />
          </el-icon>
        </div>
      </div>
    </el-aside>

    <!-- Main Area -->
    <el-container class="main-container">
      <!-- Header -->
      <el-header class="app-header">
        <div class="header-left">
          <el-breadcrumb>
            <el-breadcrumb-item :to="'/'">首页</el-breadcrumb-item>
            <el-breadcrumb-item v-if="route.name">
              {{ route.meta?.title || route.name }}
            </el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="header-right">
          <el-dropdown trigger="click" @command="signOut">
            <span class="user-dropdown">
              <el-avatar :size="32" class="user-avatar">{{ userAvatar }}</el-avatar>
              <span class="user-name">{{ displayName }}</span>
              <span class="user-role">{{ roleNames[auth.user?.role || ''] || auth.user?.role }}</span>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">个人信息</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- Main Content -->
      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout-container {
  height: 100vh;
  overflow: hidden;
}

.app-aside {
  background-color: #1d1e1f;
  overflow: hidden;
  transition: width 0.3s ease;
}

.aside-inner {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 60px;
  padding: 0 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  overflow: hidden;
  white-space: nowrap;
}

.brand.collapsed {
  justify-content: center;
  padding: 0;
}

.brand-icon {
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, 0.35);
  border-radius: 50%;
  color: #a8d6c9;
  font-size: 18px;
  font-weight: 700;
  flex-shrink: 0;
}

.brand-text {
  color: #e9f0ec;
  font-size: 15px;
  font-weight: 600;
  white-space: nowrap;
}

.el-menu {
  flex: 1;
  border-right: none;
  overflow-y: auto;
  overflow-x: hidden;
  padding-top: 4px;
}

.el-menu-item {
  display: flex;
  align-items: center;
  margin: 2px 6px;
  border-radius: 6px;
}

.el-menu-item.is-active {
  background-color: rgba(64, 158, 255, 0.15) !important;
}

.el-menu-item:hover {
  background-color: rgba(255, 255, 255, 0.08) !important;
}

.collapse-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 48px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  color: #bfcbd9;
  cursor: pointer;
  transition: color 0.2s;
  flex-shrink: 0;
}

.collapse-toggle:hover {
  color: #409eff;
}

.main-container {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 60px;
  padding: 0 24px;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
}

.header-right {
  display: flex;
  align-items: center;
}

.user-dropdown {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 6px;
  transition: background-color 0.2s;
}

.user-dropdown:hover {
  background-color: #f5f7fa;
}

.user-avatar {
  flex-shrink: 0;
  background-color: #409eff;
  color: #fff;
  font-weight: 600;
}

.user-name {
  font-size: 14px;
  color: #303133;
  font-weight: 500;
}

.user-role {
  font-size: 12px;
  color: #909399;
}

.app-main {
  flex: 1;
  padding: 24px;
  overflow-y: auto;
  background-color: #f5f7fa;
}

/* Transition */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>

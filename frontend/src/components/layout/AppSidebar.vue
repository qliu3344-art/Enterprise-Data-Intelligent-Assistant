<script setup lang="ts">
import { useRoute } from 'vue-router'
import { Folder, Download, Warning, DataAnalysis, ChatDotRound, SetUp, Collection } from '@element-plus/icons-vue'

const route = useRoute()

const menuItems = [
  { path: '/datasources', title: '数据源管理', icon: Folder },
  { path: '/collect', title: '采集历史', icon: Download },
  { path: '/clean/anomalies', title: '异常审核', icon: Warning },
  { path: '/clean', title: '清洗日志', icon: SetUp, exact: true },
  { path: '/analysis', title: '分析报表', icon: DataAnalysis },
  { path: '/query', title: '智能查询', icon: ChatDotRound },
  { path: '/rag', title: '制度文档库', icon: Collection },
]

function isActive(path: string, exact?: boolean) {
  return exact ? route.path === path : route.path.startsWith(path)
}
</script>

<template>
  <div class="sidebar-container">
    <!-- Logo -->
    <div class="sidebar-logo">
      <div class="logo-icon">
        <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M4 7v10c0 2 1 3 3 3h10c2 0 3-1 3-3V7"/>
          <path d="M8 3h8l3 4H5l3-4z"/>
          <circle cx="12" cy="13" r="2"/>
          <path d="M10 17h4"/>
        </svg>
      </div>
      <div class="logo-text">
        <div class="logo-title">数据处理平台</div>
        <div class="logo-subtitle">Data Pipeline</div>
      </div>
    </div>

    <!-- Menu -->
    <div class="sidebar-nav">
      <router-link
        v-for="item in menuItems"
        :key="item.path"
        :to="item.path"
        class="nav-item"
        :class="{ active: isActive(item.path, item.exact) }"
      >
        <el-icon class="nav-icon"><component :is="item.icon" /></el-icon>
        <span class="nav-label">{{ item.title }}</span>
      </router-link>
    </div>

    <!-- Footer -->
    <div class="sidebar-footer">
      <div class="text-xs text-gray-500 text-center">
        <div>v1.0</div>
        <div>通义千问 · LangChain</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sidebar-container {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #1a1a2e;
}

.sidebar-logo {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 20px 20px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.logo-icon {
  color: #667eea;
  flex-shrink: 0;
}

.logo-title {
  font-size: 16px;
  font-weight: 700;
  color: #fff;
  line-height: 1.2;
}

.logo-subtitle {
  font-size: 11px;
  color: #667eea;
  letter-spacing: 0.05em;
}

.sidebar-nav {
  flex: 1;
  padding: 12px 10px;
  overflow-y: auto;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  margin-bottom: 2px;
  border-radius: 8px;
  color: #a0aec0;
  text-decoration: none;
  font-size: 14px;
  transition: all 0.2s ease;
  cursor: pointer;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.06);
  color: #e2e8f0;
}

.nav-item.active {
  background: linear-gradient(135deg, #667eea, #764ba2);
  color: #fff;
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
}

.nav-icon {
  font-size: 18px;
  flex-shrink: 0;
}

.nav-label {
  white-space: nowrap;
}

.sidebar-footer {
  padding: 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}
</style>

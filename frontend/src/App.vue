<script setup lang="ts">
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AppHeader from '@/components/layout/AppHeader.vue'
</script>

<template>
  <el-container class="app-shell">
    <el-aside width="230px" class="app-aside">
      <AppSidebar />
    </el-aside>
    <el-container class="app-main">
      <el-header height="56px" class="app-header">
        <AppHeader />
      </el-header>
      <el-main class="app-content">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.app-shell {
  height: 100vh;
}

.app-aside {
  background: #1a1a2e !important;
  overflow: hidden;
}

.app-header {
  background: #fff;
  border-bottom: 1px solid #e8eaed;
  padding: 0;
}

.app-content {
  background: #f0f2f5;
  padding: 20px;
  overflow-y: auto;
}
</style>

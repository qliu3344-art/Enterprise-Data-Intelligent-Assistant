<script setup lang="ts">
import { useRoute } from 'vue-router'
import { computed } from 'vue'

const route = useRoute()

const titles: Record<string, string> = {
  DataSourceList: '数据源管理',
  DataSourceDetail: '数据源详情',
  SchemaMapping: '表头映射审核',
  CollectHistory: '采集历史',
  BatchDetail: '批次详情',
  CleanHistory: '清洗日志',
  AnomalyReview: '异常审核',
  AnalysisDashboard: '分析报表',
  Query: '智能查询',
}

const pageTitle = computed(() => titles[route.name as string] || '')
</script>

<template>
  <div class="flex items-center justify-between h-full px-6">
    <h1 class="text-lg font-semibold text-gray-800">{{ pageTitle }}</h1>
    <span class="text-sm text-gray-400">企业数据智能助手 v1.0</span>
  </div>
</template>

<script setup lang="ts">
import { shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import { cleanApi } from '@/api/clean'
import type { CleanLogItem } from '@/types/clean'
import { Search, Warning } from '@element-plus/icons-vue'

const router = useRouter()
const batchId = shallowRef('')
const logs = shallowRef<CleanLogItem[]>([])
const loading = shallowRef(false)
const searched = shallowRef(false)

const STEP_LABELS: Record<string, { label: string; type: string }> = {
  dedup: { label: '去重', type: 'primary' },
  fill_missing: { label: '填充缺失', type: 'success' },
  anomaly_detect: { label: '异常检测', type: 'danger' },
}

async function search() {
  const bid = batchId.value.trim()
  if (!bid) return
  loading.value = true
  searched.value = true
  try {
    const res = await cleanApi.logs(bid)
    logs.value = res.data.items
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="clean-page">
    <div class="page-toolbar">
      <h2 class="text-lg font-semibold text-gray-800">清洗日志</h2>
    </div>

    <!-- 搜索区 -->
    <el-card class="mb-4">
      <div class="flex gap-2">
        <el-input
          v-model="batchId" placeholder="输入批次号查询清洗日志"
          size="large" class="flex-1" clearable
          @keyup.enter="search"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button type="primary" size="large" @click="search" :loading="loading">查询</el-button>
      </div>
    </el-card>

    <!-- 结果 -->
    <el-card v-if="searched">
      <template #header>
        <span class="card-title">{{ batchId }}</span>
      </template>

      <el-table v-if="logs.length" :data="logs" stripe size="small">
        <el-table-column label="步骤" width="110">
          <template #default="{ row }">
            <el-tag :type="STEP_LABELS[row.step_name]?.type || 'info'" size="small">
              {{ STEP_LABELS[row.step_name]?.label || row.step_name }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="输入" width="80" align="right">
          <template #default="{ row }"><span class="font-semibold">{{ row.input_count }}</span></template>
        </el-table-column>
        <el-table-column label="输出" width="80" align="right">
          <template #default="{ row }"><span class="font-semibold">{{ row.output_count }}</span></template>
        </el-table-column>
        <el-table-column label="影响" width="80" align="right">
          <template #default="{ row }">
            <span :class="row.affected_count > 0 ? 'text-orange-500 font-semibold' : 'text-gray-400'">
              {{ row.affected_count }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" width="170" />
      </el-table>

      <el-empty v-else description="未找到该批次的清洗日志" :image-size="60" />
    </el-card>

    <!-- 快捷入口 -->
    <div class="mt-4 text-center">
      <el-button type="warning" @click="router.push('/clean/anomalies')">
        <el-icon><Warning /></el-icon>查看异常记录
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.clean-page { max-width: 1000px; margin: 0 auto; }
.card-title { font-size: 14px; font-weight: 600; font-family: monospace; color: #606266; }
</style>

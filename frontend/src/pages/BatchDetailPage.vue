<script setup lang="ts">
import { onMounted, shallowRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { collectApi } from '@/api/collect'
import { useClean } from '@/composables/useClean'
import type { BatchDetail } from '@/types/collect'
import { ArrowLeft, Refresh } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const batchId = route.params.batchId as string

const detail = shallowRef<BatchDetail | null>(null)
const loading = shallowRef(false)
const { cleaning, trigger } = useClean()

async function fetch() {
  loading.value = true
  try {
    const res = await collectApi.batchDetail(batchId)
    detail.value = res.data
  } finally {
    loading.value = false
  }
}

async function handleClean() {
  const result = await trigger(batchId)
  if (result) {
    ElMessage.success(`清洗完成：${result.total_input}→${result.total_output} 条，检出 ${result.anomaly_count} 条异常`)
    router.push('/clean/anomalies')
  }
}

onMounted(fetch)
</script>

<template>
  <div class="batch-page" v-loading="loading">
    <div class="page-toolbar">
      <div class="flex gap-2 items-center">
        <el-button text @click="router.back()">
          <el-icon><ArrowLeft /></el-icon>返回
        </el-button>
        <h2 class="text-lg font-semibold text-gray-800">批次详情</h2>
      </div>
      <div class="flex gap-2">
        <el-button @click="fetch"><el-icon><Refresh /></el-icon>刷新</el-button>
        <el-button type="primary" :loading="cleaning" @click="handleClean">执行清洗</el-button>
      </div>
    </div>

    <template v-if="detail">
      <!-- 批次信息 -->
      <el-card class="mb-4">
        <template #header><span class="card-title">📋 批次信息</span></template>
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="批次号" :span="2">{{ detail.batch_id }}</el-descriptions-item>
          <el-descriptions-item label="记录数">
            <span class="font-bold text-lg">{{ detail.total_records }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="数据源">{{ detail.source_name }}</el-descriptions-item>
          <el-descriptions-item label="类型">
            <el-tag size="small">{{ detail.source_type?.toUpperCase() }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ detail.created_at }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <!-- 原始数据预览 -->
      <el-card>
        <template #header>
          <span class="card-title">📄 原始数据预览（前 {{ detail.raw_records.length }} 条）</span>
        </template>
        <div v-if="detail.raw_records.length" class="raw-grid">
          <div v-for="r in detail.raw_records" :key="r.id" class="raw-item">
            <div class="raw-id">#{{ r.id }}</div>
            <pre class="raw-json">{{ JSON.stringify(r.raw_data, null, 2) }}</pre>
          </div>
        </div>
        <el-empty v-else description="无数据" :image-size="60" />
      </el-card>
    </template>

    <el-empty v-else-if="!loading" description="批次不存在" :image-size="80" />
  </div>
</template>

<style scoped>
.batch-page { max-width: 1400px; }
.card-title { font-size: 15px; font-weight: 600; }

.raw-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
}

.raw-item {
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  padding: 12px;
}

.raw-id {
  font-size: 12px;
  color: #909399;
  margin-bottom: 6px;
  font-weight: 600;
}

.raw-json {
  font-size: 12px;
  color: #606266;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
}
</style>

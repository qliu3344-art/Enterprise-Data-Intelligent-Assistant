<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useDataSourceDetail } from '@/composables/useDataSourceDetail'
import { useCollect } from '@/composables/useCollect'
import StatusTag from '@/components/common/StatusTag.vue'
import { ArrowLeft, Connection, Download, MagicStick } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const id = Number(route.params.id)

const { source, loading, testResult, testLoading, fetch, test } = useDataSourceDetail()
const { collecting, triggerSingle } = useCollect()

async function handleCollect() {
  if (!source.value) return
  const result = await triggerSingle(source.value.id)
  if (result) {
    ElMessage.success(`采集完成：${result.record_count} 条记录`)
    await fetch(id)
  }
}

async function handleTest() {
  await test(id)
  if (testResult.value?.success) {
    ElMessage.success(`连接成功！${testResult.value.row_count} 行, ${testResult.value.columns?.length} 列`)
  } else {
    ElMessage.error(testResult.value?.message || '连接失败')
  }
}

onMounted(() => fetch(id))
</script>

<template>
  <div class="detail-page" v-loading="loading">
    <div class="page-toolbar">
      <div class="flex gap-2 items-center">
        <el-button text @click="router.back()"><el-icon><ArrowLeft /></el-icon></el-button>
        <h2 class="text-lg font-semibold text-gray-800">数据源详情</h2>
      </div>
      <div class="flex gap-2">
        <el-button :loading="testLoading" @click="handleTest">
          <el-icon><Connection /></el-icon>测试连接
        </el-button>
        <el-button type="primary" :loading="collecting" @click="handleCollect">
          <el-icon><Download /></el-icon>立即采集
        </el-button>
        <el-button type="success" @click="router.push(`/datasources/${id}/mapping`)">
          <el-icon><MagicStick /></el-icon>表头映射
        </el-button>
      </div>
    </div>

    <template v-if="source">
      <!-- 状态卡片 -->
      <div class="flex gap-4 mb-5">
        <div class="info-stat">
          <div class="info-stat-label">状态</div>
          <div class="info-stat-value"><StatusTag :status="source.status" /></div>
        </div>
        <div class="info-stat">
          <div class="info-stat-label">最近采集</div>
          <div class="info-stat-value">
            <span class="text-xl font-bold mr-1">{{ source.last_collect_count }}</span>条
          </div>
        </div>
        <div class="info-stat">
          <div class="info-stat-label">采集时间</div>
          <div class="info-stat-value text-sm">{{ source.last_collect_at || '未采集' }}</div>
        </div>
      </div>

      <!-- 基本信息 -->
      <el-card>
        <template #header><span class="card-title">📋 基本信息</span></template>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="ID">{{ source.id }}</el-descriptions-item>
          <el-descriptions-item label="名称">
            <span class="font-semibold">{{ source.name }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="类型">
            <el-tag size="small">{{ source.source_type?.toUpperCase() }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <StatusTag :status="source.status" />
          </el-descriptions-item>
          <el-descriptions-item label="文件路径" :span="2">
            <code class="text-xs bg-gray-100 px-2 py-1 rounded">{{ source.file_path || '-' }}</code>
          </el-descriptions-item>
          <el-descriptions-item label="编码">{{ source.encoding || 'utf-8' }}</el-descriptions-item>
          <el-descriptions-item label="跳过行数">{{ source.skip_rows || 0 }}</el-descriptions-item>
          <el-descriptions-item v-if="source.delimiter" label="分隔符">{{ source.delimiter }}</el-descriptions-item>
          <el-descriptions-item v-if="source.sheet_name" label="Sheet">{{ source.sheet_name }}</el-descriptions-item>
        </el-descriptions>

        <!-- 错误信息 -->
        <div v-if="source.error_message" class="mt-4 p-3 bg-red-50 rounded-lg border border-red-200">
          <div class="text-sm font-semibold text-red-600 mb-1">⚠ 错误信息</div>
          <div class="text-sm text-red-500">{{ source.error_message }}</div>
        </div>

        <!-- 连接测试结果 -->
        <div v-if="testResult" class="mt-4 p-3 rounded-lg border"
          :class="testResult.success ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'">
          <div class="text-sm font-semibold mb-1" :class="testResult.success ? 'text-green-600' : 'text-red-600'">
            {{ testResult.success ? '✅ 连接正常' : '❌ 连接失败' }}
          </div>
          <div class="text-sm" :class="testResult.success ? 'text-green-600' : 'text-red-500'">
            {{ testResult.message }}
          </div>
          <div v-if="testResult.success && testResult.columns" class="mt-2 flex flex-wrap gap-1">
            <el-tag v-for="col in testResult.columns" :key="col" size="small" type="info">{{ col }}</el-tag>
          </div>
        </div>
      </el-card>
    </template>

    <el-empty v-else-if="!loading" description="数据源不存在" :image-size="80" />
  </div>
</template>

<style scoped>
.detail-page { max-width: 1000px; }
.card-title { font-size: 15px; font-weight: 600; }

.info-stat {
  background: #fff;
  border-radius: 8px;
  padding: 16px 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  flex: 1;
}

.info-stat-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 6px;
}

.info-stat-value {
  font-size: 15px;
  color: #303133;
}
</style>

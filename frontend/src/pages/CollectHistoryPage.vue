<script setup lang="ts">
import { onMounted, shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import { useCollect } from '@/composables/useCollect'
import StatusTag from '@/components/common/StatusTag.vue'
import { Refresh } from '@element-plus/icons-vue'

const router = useRouter()
const { history, total, loading, fetchHistory } = useCollect()
const page = shallowRef(1)
const pageSize = 20

function load() { fetchHistory(page.value, pageSize) }

onMounted(load)
</script>

<template>
  <div class="collect-page">
    <div class="page-toolbar">
      <h2 class="text-lg font-semibold text-gray-800">采集批次历史</h2>
      <el-button @click="load" :loading="loading">
        <el-icon><Refresh /></el-icon>刷新
      </el-button>
    </div>

    <el-card>
      <el-table :data="history" v-loading="loading" stripe empty-text="暂无采集记录，请在数据源页面触发采集">
        <el-table-column prop="batch_id" label="批次号" min-width="220" show-overflow-tooltip />
        <el-table-column prop="source_name" label="数据源" min-width="160" />
        <el-table-column label="类型" width="80" align="center">
          <template #default="{ row }">
            <el-tag size="small">{{ row.source_type?.toUpperCase() }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="记录数" width="90" align="right">
          <template #default="{ row }">
            <span class="font-semibold">{{ row.record_count }}</span>
            <span class="text-gray-400 text-xs ml-1">条</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <StatusTag :status="row.status" />
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="采集时间" width="170" />
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" text @click="router.push(`/collect/${row.batch_id}`)">
              查看数据
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="flex justify-end mt-4">
        <el-pagination
          v-model:current-page="page" :total="total" :page-size="pageSize"
          layout="total, prev, pager, next" @current-change="load"
        />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.collect-page { max-width: 1400px; }
</style>

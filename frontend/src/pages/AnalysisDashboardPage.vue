<script setup lang="ts">
import { onMounted, shallowRef, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { Download } from '@element-plus/icons-vue'
import { useAnalysis } from '@/composables/useAnalysis'
import { analysisApi } from '@/api/analysis'
import TrendChart from '@/components/analysis/TrendChart.vue'

const { summary, trend, quality, loading, fetchDashboard, fetchTrend } = useAnalysis()
const trendMetric = shallowRef('record_count')
const trendDataType = shallowRef('')

const TYPE_LABELS: Record<string, string> = {
  attendance: '考勤', sales: '销售', customer: '客户', operation: '运营',
}

async function loadAll() {
  await fetchDashboard()
}

async function loadTrend() {
  await fetchTrend(trendMetric.value, trendDataType.value)
}

async function handleExport() {
  try {
    const res = await analysisApi.exportData({})
    const baseUrl = import.meta.env.BASE_URL || '/'
    window.open(`${baseUrl}api/v1${res.data.download_url}`, '_blank')
    ElMessage.success(`导出成功: ${res.data.filename}`)
  } catch {}
}

const anomalyRate = computed(() => quality.value?.anomaly_rate ?? 0)
const anomalyColor = computed(() => anomalyRate.value > 5 ? 'orange' : 'green')

onMounted(() => { loadAll() })
</script>

<template>
  <div v-loading="loading" class="dashboard">
    <!-- 统计卡片 -->
    <div class="stats-row">
      <div class="stat-card">
        <div class="stat-label">清洗后总记录</div>
        <div class="stat-value">{{ summary?.total_records ?? 0 }}</div>
        <div class="stat-sub">{{ summary?.total_batches ?? 0 }} 个批次</div>
      </div>
      <div class="stat-card green">
        <div class="stat-label">平均质量分</div>
        <div class="stat-value">{{ quality?.avg_quality_score?.toFixed(2) ?? '-' }}</div>
        <div class="stat-sub">满分 1.0</div>
      </div>
      <div class="stat-card blue">
        <div class="stat-label">按类型分布</div>
        <div class="stat-value">{{ summary?.by_type?.length ?? 0 }}</div>
        <div class="stat-sub">种数据类型</div>
      </div>
      <div class="stat-card" :class="anomalyColor">
        <div class="stat-label">异常率</div>
        <div class="stat-value">{{ anomalyRate }}%</div>
        <div class="stat-sub">{{ anomalyRate > 5 ? '⚠ 需关注' : '✓ 数据质量良好' }}</div>
      </div>
    </div>

    <!-- 按类型汇总 + 按部门质量 -->
    <div class="grid grid-cols-2 gap-4 mb-5">
      <el-card>
        <template #header><span class="card-title">📊 按数据类型汇总</span></template>
        <el-table v-if="summary?.by_type?.length" :data="summary.by_type" stripe size="small">
          <el-table-column label="类型" width="80">
            <template #default="{ row }">
              <el-tag :class="`type-tag ${row.data_type}`" size="small" disable-transitions>
                {{ TYPE_LABELS[row.data_type] || row.data_type }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="record_count" label="记录数" width="90" align="right" />
          <el-table-column prop="anomaly_count" label="异常" width="70" align="right" />
          <el-table-column label="异常率" min-width="140">
            <template #default="{ row }">
              <div class="flex items-center gap-2">
                <el-progress
                  :percentage="row.anomaly_rate"
                  :stroke-width="14"
                  :color="row.anomaly_rate > 5 ? '#f56c6c' : '#67c23a'"
                  :show-text="false"
                  style="flex: 1"
                />
                <span class="text-xs" :class="row.anomaly_rate > 5 ? 'text-red-500' : 'text-green-500'">
                  {{ row.anomaly_rate }}%
                </span>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="avg_quality_score" label="质量分" width="80" align="right" />
        </el-table>
        <el-empty v-else description="暂无数据，请先采集并清洗" :image-size="60" />
      </el-card>

      <el-card>
        <template #header><span class="card-title">🏢 按部门质量分布</span></template>
        <el-table v-if="quality?.by_department?.length" :data="quality.by_department" stripe size="small">
          <el-table-column prop="department" label="部门" />
          <el-table-column prop="total_records" label="记录数" width="90" align="right" />
          <el-table-column label="异常率" min-width="140">
            <template #default="{ row }">
              <div class="flex items-center gap-2">
                <el-progress
                  :percentage="row.anomaly_rate"
                  :stroke-width="14"
                  :color="row.anomaly_rate > 5 ? '#f56c6c' : '#67c23a'"
                  :show-text="false"
                  style="flex: 1"
                />
                <span class="text-xs" :class="row.anomaly_rate > 5 ? 'text-red-500' : 'text-green-500'">
                  {{ row.anomaly_rate }}%
                </span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="质量分" width="80" align="right">
            <template #default="{ row }">
              {{ row.avg_quality_score?.toFixed(2) }}
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="暂无部门数据" :image-size="60" />
      </el-card>
    </div>

    <!-- 趋势图 -->
    <el-card class="mb-5">
      <template #header>
        <div class="flex justify-between items-center">
          <span class="card-title">📈 趋势分析</span>
          <div class="flex gap-2">
            <el-select v-model="trendMetric" @change="loadTrend" size="small" style="width: 110px">
              <el-option label="记录数" value="record_count" />
              <el-option label="异常率" value="anomaly_rate" />
            </el-select>
            <el-select v-model="trendDataType" @change="loadTrend" size="small" style="width: 110px" clearable placeholder="全部">
              <el-option label="考勤" value="attendance" />
              <el-option label="销售" value="sales" />
              <el-option label="客户" value="customer" />
              <el-option label="运营" value="operation" />
            </el-select>
          </div>
        </div>
      </template>
      <TrendChart :data="trend" :loading="loading" />
    </el-card>

    <!-- 导出 -->
    <div class="flex justify-end">
      <el-button type="primary" size="large" @click="handleExport">
        <el-icon class="mr-1"><Download /></el-icon>导出 Excel 报表
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.dashboard {
  max-width: 1400px;
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}

@media (max-width: 1200px) {
  .stats-row {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 768px) {
  .stats-row {
    grid-template-columns: 1fr;
  }
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}
</style>

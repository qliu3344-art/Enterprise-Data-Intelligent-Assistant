<script setup lang="ts">
import { onMounted, shallowRef, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useClean } from '@/composables/useClean'
import { Warning, CircleCheck } from '@element-plus/icons-vue'

const { anomalies, total, loading, fetchAnomalies, updateAnomaly } = useClean()
const page = shallowRef(1)
const pageSize = 20
const dataTypeFilter = shallowRef('')
const statsLoading = shallowRef(false)
const stats = shallowRef<{ by_type: Record<string, number>; total: number }>({ by_type: {}, total: 0 })

const TYPE_LABELS: Record<string, string> = {
  attendance: '考勤', sales: '销售', customer: '客户', operation: '运营',
}

function getField(row: any, field: string) {
  // 优先取顶层字段，为空则从 business_data 中尝试多种键名
  if (row[field]) return row[field]
  const bd = row.business_data || {}
  const aliases: Record<string, string[]> = {
    record_date: ['record_date', '考勤日期', '日期', '业务日期', 'date'],
    employee_name: ['employee_name', '员工姓名', '姓名', 'name'],
    department: ['department', '所属部门', '部门', 'dept'],
  }
  for (const key of aliases[field] || [field]) {
    if (bd[key]) {
      const val = bd[key]
      // 统一日期格式：2025/11/10 → 2025-11-10
      if (field === 'record_date' && typeof val === 'string') {
        return val.replace(/\//g, '-').slice(0, 10)
      }
      return val
    }
  }
  return '-'
}

const TYPE_COLORS: Record<string, string> = {
  attendance: '#1890ff', sales: '#52c41a', customer: '#fa8c16', operation: '#722ed1',
}

async function load() {
  await fetchAnomalies(page.value, pageSize, dataTypeFilter.value)
  // Update local stats
  stats.value.total = total.value
  const byType: Record<string, number> = {}
  for (const a of anomalies.value) {
    const t = a.data_type || 'unknown'
    byType[t] = (byType[t] || 0) + 1
  }
  stats.value.by_type = byType
}

async function handleMarkNormal(recordId: number, employeeName: string) {
  try {
    await ElMessageBox.confirm(
      `确认将「${employeeName}」的这条记录标记为正常？<br><span class="text-gray-400 text-sm">此操作将覆盖 LLM 的异常判定</span>`,
      '审核确认',
      { type: 'warning', dangerouslyUseHTMLString: true }
    )
    await updateAnomaly(recordId, false, '人工审核：业务合理波动')
    ElMessage.success('已标记为正常')
    load()
  } catch {}
}

async function handleConfirmAnomaly(recordId: number) {
  try {
    await updateAnomaly(recordId, true, '人工审核确认异常')
    ElMessage.success('已标记为异常')
    load()
  } catch {}
}

onMounted(load)
</script>

<template>
  <div class="anomaly-page">
    <!-- 统计条 -->
    <div class="anomaly-stats-bar">
      <div class="stat-item">
        <el-icon color="#f56c6c" :size="22"><Warning /></el-icon>
        <div>
          <div class="text-2xl font-bold text-red-500">{{ stats.total }}</div>
          <div class="text-xs text-gray-500">待审核异常</div>
        </div>
      </div>
      <div v-for="(count, dtype) in stats.by_type" :key="dtype" class="stat-item">
        <el-tag :class="`type-tag ${dtype}`" size="small" disable-transitions>
          {{ TYPE_LABELS[dtype] || dtype }}
        </el-tag>
        <div>
          <div class="text-xl font-bold">{{ count }}</div>
          <div class="text-xs text-gray-500">条</div>
        </div>
      </div>
    </div>

    <!-- 筛选 + 表格 -->
    <el-card>
      <template #header>
        <div class="flex justify-between items-center">
          <span class="card-title">🔍 异常记录详情</span>
          <el-select v-model="dataTypeFilter" placeholder="筛选类型" clearable size="small" style="width: 120px" @change="load">
            <el-option label="考勤" value="attendance" />
            <el-option label="销售" value="sales" />
            <el-option label="客户" value="customer" />
            <el-option label="运营" value="operation" />
          </el-select>
        </div>
      </template>

      <el-table :data="anomalies" v-loading="loading" stripe empty-text="🎉 暂无异常记录，数据质量良好" style="width: 100%">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column label="类型" width="70">
          <template #default="{ row }">
            <el-tag :class="`type-tag ${row.data_type}`" size="small" disable-transitions>
              {{ TYPE_LABELS[row.data_type] || row.data_type }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="员工" width="100" show-overflow-tooltip>
          <template #default="{ row }">{{ getField(row, 'employee_name') }}</template>
        </el-table-column>
        <el-table-column label="部门" width="100" show-overflow-tooltip>
          <template #default="{ row }">{{ getField(row, 'department') }}</template>
        </el-table-column>
        <el-table-column label="日期" width="115" show-overflow-tooltip>
          <template #default="{ row }">{{ getField(row, 'record_date') }}</template>
        </el-table-column>
        <el-table-column label="异常原因" min-width="280">
          <template #default="{ row }">
            <div class="anomaly-reason">
              <el-icon color="#f56c6c" :size="14" class="mr-1"><Warning /></el-icon>
              <span>{{ row.anomaly_reason || 'LLM 判定为异常' }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150">
          <template #default="{ row }">
            <el-button size="small" text type="success" @click="handleMarkNormal(row.id, getField(row, 'employee_name'))">
              标记正常
            </el-button>
            <el-button size="small" text type="danger" @click="handleConfirmAnomaly(row.id)">
              确认异常
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="flex justify-end mt-4">
        <el-pagination
          v-model:current-page="page"
          :total="total"
          :page-size="pageSize"
          layout="total, prev, pager, next"
          @current-change="load"
        />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.anomaly-page {
  max-width: 1400px;
}

.anomaly-stats-bar {
  display: flex;
  gap: 20px;
  margin-bottom: 16px;
  padding: 16px 20px;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}

.stat-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-right: 20px;
  border-right: 1px solid #f0f0f0;
}

.stat-item:last-child {
  border-right: none;
}

.anomaly-reason {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  font-size: 13px;
  color: #f56c6c;
  line-height: 1.4;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
}
</style>

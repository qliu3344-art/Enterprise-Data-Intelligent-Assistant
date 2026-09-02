<script setup lang="ts">
import { onMounted, shallowRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useDataSourceDetail } from '@/composables/useDataSourceDetail'
import { datasourceApi } from '@/api/datasource'
import { MagicStick, ArrowLeft, Check } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const id = Number(route.params.id)

const { source, loading, fetch } = useDataSourceDetail()
const mappingLoading = shallowRef(false)
const mappingResult = shallowRef<{
  mapping: Record<string, string>
  unmapped: string[]
  confidence: number
} | null>(null)
const headers = shallowRef<string[]>([])
const mappingId = shallowRef<number | null>(null)
const reviewed = shallowRef(false)
const reviewLoading = shallowRef(false)

const FIELD_LABELS: Record<string, string> = {
  employee_name: '员工姓名', employee_id: '工号', department: '部门',
  record_date: '业务日期', attendance_days: '出勤天数', leave_days: '请假天数',
  overtime_hours: '加班时长', late_count: '迟到次数', order_amount: '订单金额',
  order_count: '订单数量', product_name: '产品名称', customer_name: '客户名称',
  payment_method: '支付方式', customer_level: '客户等级', contract_amount: '合同金额',
  contact_phone: '联系电话', follow_up_date: '跟进日期', metric_name: '指标名称',
  metric_value: '指标数值', metric_unit: '单位', target_value: '目标值', completion_rate: '完成率',
}

const confidenceColor = (c: number) => c >= 0.9 ? '#67c23a' : c >= 0.7 ? '#e6a23c' : '#f56c6c'

async function handleAlign() {
  mappingLoading.value = true
  try {
    const res = await datasourceApi.align(id)
    const data = res.data
    if (data.success) {
      mappingResult.value = {
        mapping: data.mapping,
        unmapped: data.unmapped,
        confidence: data.confidence,
      }
      mappingId.value = data.mapping_id ?? null
      reviewed.value = false
      headers.value = data.headers || []
      ElMessage.success(`LLM 对齐完成！置信度 ${(data.confidence * 100).toFixed(0)}%`)
    } else {
      ElMessage.error(data.message || '对齐失败')
    }
  } catch (e: any) {
    ElMessage.error(e.message || '对齐失败')
  } finally {
    mappingLoading.value = false
  }
}

async function handleReview() {
  if (!mappingId.value) return
  reviewLoading.value = true
  try {
    await datasourceApi.review(id, mappingId.value)
    reviewed.value = true
    ElMessage.success('映射已人工确认')
  } catch (e: any) {
    ElMessage.error(e.message || '确认失败')
  } finally {
    reviewLoading.value = false
  }
}

onMounted(() => fetch(id))
</script>

<template>
  <div class="mapping-page" v-loading="loading">
    <div class="page-toolbar">
      <div class="flex gap-2 items-center">
        <el-button text @click="router.back()"><el-icon><ArrowLeft /></el-icon></el-button>
        <h2 class="text-lg font-semibold">表头映射审核</h2>
      </div>
      <el-button type="primary" :loading="mappingLoading" @click="handleAlign">
        <el-icon><MagicStick /></el-icon>执行 LLM 表头对齐
      </el-button>
    </div>

    <!-- 数据源信息 -->
    <el-card v-if="source" class="mb-4">
      <div class="flex items-center gap-3">
        <span class="text-gray-500">数据源：</span>
        <span class="font-semibold">{{ source.name }}</span>
        <el-tag size="small">{{ source.source_type?.toUpperCase() }}</el-tag>
      </div>
    </el-card>

    <!-- 未执行对齐 -->
    <el-card v-if="!mappingResult && !mappingLoading">
      <div class="text-center py-12">
        <div class="text-5xl mb-4">🧠</div>
        <h3 class="text-lg font-semibold text-gray-700 mb-2">LLM 语义表头对齐</h3>
        <p class="text-gray-400 mb-5">
          通义千问大模型将自动识别原始列名的语义含义，<br>
          并映射到标准字段名，如 "员工编号" → "employee_id"
        </p>
        <el-button type="primary" size="large" @click="handleAlign" :loading="mappingLoading">
          <el-icon><MagicStick /></el-icon>开始对齐
        </el-button>
      </div>
    </el-card>

    <!-- 对齐结果 -->
    <template v-if="mappingResult">
      <!-- 置信度 -->
      <el-card class="mb-4">
        <div class="flex items-center gap-4">
          <span class="text-gray-500">映射置信度：</span>
          <el-progress
            :percentage="mappingResult.confidence * 100"
            :stroke-width="16"
            :color="confidenceColor(mappingResult.confidence)"
            style="flex: 1; max-width: 300px;"
          />
          <span class="font-bold" :style="{ color: confidenceColor(mappingResult.confidence) }">
            {{ (mappingResult.confidence * 100).toFixed(0) }}%
          </span>
        </div>
      </el-card>

      <div class="grid grid-cols-2 gap-4">
        <!-- 已映射 -->
        <el-card>
          <template #header>
            <span class="card-title text-green-600">✅ 已映射 ({{ Object.keys(mappingResult.mapping).length }})</span>
          </template>
          <div v-if="Object.keys(mappingResult.mapping).length">
            <div
              v-for="(standardKey, originalCol) in mappingResult.mapping"
              :key="originalCol"
              class="mapping-row"
            >
              <div class="mapping-original">
                <el-tag size="small" type="info">{{ originalCol }}</el-tag>
              </div>
              <div class="mapping-arrow">→</div>
              <div class="mapping-standard">
                <el-tag size="small" type="success">{{ standardKey }}</el-tag>
                <span class="text-xs text-gray-400 ml-1">{{ FIELD_LABELS[standardKey] }}</span>
              </div>
            </div>
          </div>
          <el-empty v-else description="无已映射字段" :image-size="40" />
        </el-card>

        <!-- 未映射 -->
        <el-card>
          <template #header>
            <span class="card-title text-orange-600">⚠ 未映射 ({{ mappingResult.unmapped.length }})</span>
          </template>
          <div v-if="mappingResult.unmapped.length" class="flex flex-wrap gap-2">
            <el-tag
              v-for="col in mappingResult.unmapped" :key="col"
              type="warning" size="small"
            >{{ col }}</el-tag>
          </div>
          <el-empty v-else description="全部字段已成功映射 🎉" :image-size="40" />
        </el-card>
      </div>

      <!-- 人工确认 -->
      <el-card class="mt-4">
        <div class="flex items-center justify-between">
          <div v-if="reviewed" class="text-green-600 font-semibold">✅ 已人工确认</div>
          <div v-else class="text-gray-500 text-sm">映射结果仅供参考，请人工核对后确认</div>
          <el-button
            type="success"
            :loading="reviewLoading"
            :disabled="reviewed"
            @click="handleReview"
          >
            {{ reviewed ? '已确认' : '确认映射' }}
          </el-button>
        </div>
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.mapping-page { max-width: 1100px; }
.card-title { font-size: 14px; font-weight: 600; }

.mapping-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid #fafafa;
}

.mapping-row:last-child { border-bottom: none; }

.mapping-original { min-width: 100px; }
.mapping-arrow { color: #c0c4cc; font-weight: bold; }
.mapping-standard { flex: 1; }
</style>

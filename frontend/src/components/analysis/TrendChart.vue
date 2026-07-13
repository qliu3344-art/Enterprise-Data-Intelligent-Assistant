<script setup lang="ts">
import { computed, shallowRef } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, BarChart } from 'echarts/charts'
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  DataZoomComponent,
} from 'echarts/components'
import type { TrendData } from '@/api/analysis'

use([
  CanvasRenderer,
  LineChart,
  BarChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  DataZoomComponent,
])

const props = defineProps<{
  data: TrendData | null
  loading?: boolean
  chartType?: 'line' | 'bar'
}>()

const chartType = shallowRef(props.chartType || 'line')

const option = computed(() => {
  if (!props.data?.data_points?.length) return {}

  // 按 data_type 分组
  const points = props.data.data_points
  const periods = [...new Set(points.map((p) => p.period))].sort()
  const types = [...new Set(points.map((p) => p.data_type))]

  const typeLabelMap: Record<string, string> = {
    attendance: '考勤',
    sales: '销售',
    customer: '客户',
    operation: '运营',
  }

  const series = types.map((dt) => {
    const data = periods.map((period) => {
      const match = points.find((p) => p.period === period && p.data_type === dt)
      return match ? match.value : null
    })
    return {
      name: typeLabelMap[dt] || dt,
      type: chartType.value,
      data,
      smooth: true,
      connectNulls: true,
    }
  })

  return {
    tooltip: {
      trigger: 'axis',
    },
    legend: {
      data: types.map((dt) => typeLabelMap[dt] || dt),
      top: 0,
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '3%',
      top: 40,
      containLabel: true,
    },
    xAxis: {
      type: 'category',
      data: periods,
      boundaryGap: false,
    },
    yAxis: {
      type: 'value',
      name: props.data.metric === 'anomaly_rate' ? '异常率 (%)' : '记录数',
    },
    dataZoom: [
      {
        type: 'slider',
        show: periods.length > 12,
        start: 0,
        end: 100,
      },
    ],
    series,
  }
})

const isEmpty = computed(() => !props.data?.data_points?.length)
</script>

<template>
  <div class="trend-chart">
    <div v-if="!isEmpty" class="mb-3 flex gap-2">
      <el-radio-group v-model="chartType" size="small">
        <el-radio-button value="line">折线图</el-radio-button>
        <el-radio-button value="bar">柱状图</el-radio-button>
      </el-radio-group>
    </div>
    <div v-loading="loading" style="height: 360px">
      <v-chart v-if="!isEmpty" :option="option" autoresize style="height: 100%" />
      <el-empty v-else description="暂无趋势数据" :image-size="80" />
    </div>
  </div>
</template>

import { shallowRef } from 'vue'
import { analysisApi } from '@/api/analysis'
import type { SummaryData, TrendData, QualityData } from '@/api/analysis'

export function useAnalysis() {
  const summary = shallowRef<SummaryData | null>(null)
  const trend = shallowRef<TrendData | null>(null)
  const quality = shallowRef<QualityData | null>(null)
  const loading = shallowRef(false)

  async function fetchDashboard() {
    loading.value = true
    try {
      const res = await analysisApi.dashboard()
      summary.value = res.data.summary
      trend.value = res.data.trend
      quality.value = res.data.quality
    } finally {
      loading.value = false
    }
  }

  async function fetchSummary() {
    loading.value = true
    try {
      const res = await analysisApi.summary()
      summary.value = res.data
    } finally {
      loading.value = false
    }
  }

  async function fetchTrend(metric = 'record_count', dataType = '', granularity = 'month') {
    const res = await analysisApi.trend(metric, dataType, granularity)
    trend.value = res.data
  }

  async function fetchQuality() {
    const res = await analysisApi.quality()
    quality.value = res.data
  }

  return { summary, trend, quality, loading, fetchDashboard, fetchSummary, fetchTrend, fetchQuality }
}

import { shallowRef } from 'vue'
import { cleanApi } from '@/api/clean'
import type { AnomalyRecord, CleanResult } from '@/types/clean'

export function useClean() {
  const anomalies = shallowRef<AnomalyRecord[]>([])
  const total = shallowRef(0)
  const loading = shallowRef(false)
  const cleaning = shallowRef(false)
  const lastResult = shallowRef<CleanResult | null>(null)

  async function fetchAnomalies(page = 1, pageSize = 20, dataType = '') {
    loading.value = true
    try {
      const res = await cleanApi.anomalies(page, pageSize, dataType)
      anomalies.value = res.data.items
      total.value = res.data.total
    } finally {
      loading.value = false
    }
  }

  async function trigger(batchId: string) {
    cleaning.value = true
    try {
      const res = await cleanApi.trigger(batchId)
      lastResult.value = res.data
      return res.data
    } finally {
      cleaning.value = false
    }
  }

  async function updateAnomaly(id: number, isAnomaly: boolean, reason?: string) {
    await cleanApi.updateAnomaly(id, isAnomaly, reason)
    anomalies.value = anomalies.value.filter((a) => a.id !== id)
    total.value--
  }

  return { anomalies, total, loading, cleaning, lastResult, fetchAnomalies, trigger, updateAnomaly }
}

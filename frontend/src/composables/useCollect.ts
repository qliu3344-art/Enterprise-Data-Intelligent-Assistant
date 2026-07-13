import { shallowRef } from 'vue'
import { collectApi } from '@/api/collect'
import type { CollectHistoryItem, CollectResult } from '@/types/collect'

export function useCollect() {
  const history = shallowRef<CollectHistoryItem[]>([])
  const total = shallowRef(0)
  const loading = shallowRef(false)
  const collecting = shallowRef(false)
  const lastResult = shallowRef<CollectResult | null>(null)

  async function fetchHistory(page = 1, pageSize = 20, sourceId = 0) {
    loading.value = true
    try {
      const res = await collectApi.history(page, pageSize, sourceId)
      history.value = res.data.items
      total.value = res.data.total
    } finally {
      loading.value = false
    }
  }

  async function triggerSingle(sourceId: number) {
    collecting.value = true
    try {
      const res = await collectApi.triggerSingle(sourceId)
      lastResult.value = res.data
      return res.data
    } finally {
      collecting.value = false
    }
  }

  async function triggerBatch(sourceIds: number[]) {
    collecting.value = true
    try {
      const res = await collectApi.triggerBatch(sourceIds)
      const data = res.data as any
      return { success_count: data.results?.filter((r: any) => r.status === 'success').length ?? 0, failed_count: data.results?.filter((r: any) => r.status === 'failed').length ?? 0 }
    } finally {
      collecting.value = false
    }
  }

  return { history, total, loading, collecting, lastResult, fetchHistory, triggerSingle, triggerBatch }
}

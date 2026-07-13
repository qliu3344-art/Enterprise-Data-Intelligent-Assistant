import client from './client'
import type { APIResponse, PaginatedData } from '@/types/common'
import type { CollectResult, CollectHistoryItem, BatchDetail } from '@/types/collect'

export const collectApi = {
  triggerSingle(sourceId: number) {
    return client.post<APIResponse<CollectResult>>(`/collect/${sourceId}`)
  },

  triggerBatch(sourceIds: number[]) {
    return client.post<APIResponse<{ results: CollectResult[] }>>('/collect/batch', {
      source_ids: sourceIds,
    })
  },

  history(page = 1, pageSize = 20, sourceId = 0) {
    return client.get<APIResponse<PaginatedData<CollectHistoryItem>>>('/collect/history', {
      params: { page, page_size: pageSize, source_id: sourceId },
    })
  },

  batchDetail(batchId: string) {
    return client.get<APIResponse<BatchDetail>>(`/collect/${batchId}`)
  },
}

import client from './client'
import type { APIResponse, PaginatedData } from '@/types/common'
import type { CleanResult, CleanLogItem, AnomalyRecord } from '@/types/clean'

export const cleanApi = {
  trigger(batchId: string) {
    return client.post<APIResponse<CleanResult>>(`/clean/${batchId}`)
  },

  logs(batchId: string) {
    return client.get<APIResponse<{ items: CleanLogItem[] }>>(`/clean/${batchId}/logs`)
  },

  anomalies(page = 1, pageSize = 20, dataType = '') {
    return client.get<APIResponse<PaginatedData<AnomalyRecord>>>('/clean/anomalies', {
      params: { page, page_size: pageSize, data_type: dataType },
    })
  },

  updateAnomaly(recordId: number, isAnomaly: boolean, reason?: string) {
    return client.put<APIResponse<null>>(`/clean/anomalies/${recordId}`, {
      is_anomaly: isAnomaly,
      anomaly_reason: reason,
    })
  },
}

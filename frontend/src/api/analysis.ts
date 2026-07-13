import client from './client'
import type { APIResponse } from '@/types/common'

export interface SummaryData {
  total_records: number
  total_batches: number
  by_type: Array<{
    data_type: string
    record_count: number
    anomaly_count: number
    anomaly_rate: number
    avg_quality_score: number
  }>
}

export interface TrendData {
  metric: string
  data_points: Array<{
    period: string
    value: number
    data_type: string
  }>
}

export interface QualityData {
  total_records: number
  missing_rate: number
  anomaly_rate: number
  avg_quality_score: number
  by_department: Array<{
    department: string
    total_records: number
    anomaly_rate: number
    avg_quality_score: number
  }>
}

export interface DashboardData {
  summary: SummaryData
  trend: TrendData
  quality: QualityData
}

export const analysisApi = {
  dashboard() {
    return client.get<APIResponse<DashboardData>>('/analysis/dashboard')
  },

  summary() {
    return client.get<APIResponse<SummaryData>>('/analysis/summary')
  },

  trend(metric = 'record_count', dataType = '', granularity = 'month') {
    return client.get<APIResponse<TrendData>>('/analysis/trend', {
      params: { metric, data_type: dataType, granularity },
    })
  },

  quality() {
    return client.get<APIResponse<QualityData>>('/analysis/quality')
  },

  exportData(params: Record<string, any>) {
    return client.post<APIResponse<{ filename: string; download_url: string }>>('/analysis/export', params)
  },
}

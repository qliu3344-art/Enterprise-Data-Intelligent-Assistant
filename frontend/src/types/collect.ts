export interface CollectResult {
  batch_id: string
  source_id: number
  source_name: string
  record_count: number
  status: string
  message: string
}

export interface CollectHistoryItem {
  batch_id: string
  source_id: number
  source_name: string
  source_type: string
  record_count: number
  status: string
  created_at: string
}

export interface BatchDetail {
  batch_id: string
  source_id: number
  source_name: string
  source_type: string
  total_records: number
  status: string
  raw_records: Array<{
    id: number
    raw_data: Record<string, any>
    source_row_index: number
  }>
  created_at: string
}

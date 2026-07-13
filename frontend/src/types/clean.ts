export interface CleanResult {
  batch_id: string
  status: string
  total_input: number
  total_output: number
  anomaly_count: number
  steps: Array<{
    step: string
    input_count: number
    output_count: number
    details: Record<string, any>
  }>
  duration_seconds: number | null
}

export interface CleanLogItem {
  id: number
  batch_id: string
  step_name: string
  input_count: number
  output_count: number
  affected_count: number
  details: Record<string, any> | null
  duration_seconds: number | null
  created_at: string
}

export interface AnomalyRecord {
  id: number
  batch_id: string
  source_id: number
  employee_name: string | null
  employee_id: string | null
  department: string | null
  data_type: string | null
  record_date: string | null
  business_data: Record<string, any>
  quality_score: number
  is_anomaly: boolean
  anomaly_reason: string | null
  created_at: string
}

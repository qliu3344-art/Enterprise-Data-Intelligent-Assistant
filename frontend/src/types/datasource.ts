export interface DataSourceItem {
  id: number
  name: string
  source_type: 'excel' | 'csv' | 'mysql' | 'pdf'
  file_path: string | null
  db_host: string | null
  db_port: number | null
  db_name: string | null
  db_query: string | null
  sheet_name: string | null
  delimiter: string
  encoding: string
  skip_rows: number
  status: 'active' | 'inactive' | 'error'
  last_collect_at: string | null
  last_collect_count: number
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface DataSourceForm {
  name: string
  source_type: 'excel' | 'csv' | 'mysql' | 'pdf'
  file_path?: string
  db_host?: string
  db_port?: number
  db_name?: string
  db_user?: string
  db_password?: string
  db_query?: string
  sheet_name?: string
  delimiter?: string
  encoding?: string
  skip_rows?: number
}

export interface UploadResult {
  file_path: string
  file_name: string
  file_size: number
  inferred_type: string
}

export interface TestResult {
  success: boolean
  message: string
  row_count: number | null
  columns: string[] | null
}

export interface AlignResult {
  success: boolean
  source_id?: number
  headers?: string[]
  mapping: Record<string, string>
  unmapped: string[]
  confidence: number
  message?: string
  mapping_id?: number
}

export interface ReviewResult {
  mapping_id: number
  manual_reviewed: boolean
}

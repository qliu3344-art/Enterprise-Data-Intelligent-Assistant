import client from './client'
import type { APIResponse, PaginatedData } from '@/types/common'
import type { DataSourceItem, DataSourceForm, UploadResult, TestResult, AlignResult } from '@/types/datasource'

export const datasourceApi = {
  list(page = 1, pageSize = 20, sourceType = '') {
    return client.get<APIResponse<PaginatedData<DataSourceItem>>>('/datasources', {
      params: { page, page_size: pageSize, source_type: sourceType },
    })
  },

  detail(id: number) {
    return client.get<APIResponse<DataSourceItem>>(`/datasources/${id}`)
  },

  create(data: DataSourceForm) {
    return client.post<APIResponse<DataSourceItem>>('/datasources', data)
  },

  update(id: number, data: Partial<DataSourceForm>) {
    return client.put<APIResponse<DataSourceItem>>(`/datasources/${id}`, data)
  },

  remove(id: number) {
    return client.delete<APIResponse<null>>(`/datasources/${id}`)
  },

  test(id: number) {
    return client.post<APIResponse<TestResult>>(`/datasources/${id}/test`)
  },

  upload(file: File) {
    const form = new FormData()
    form.append('file', file)
    return client.post<APIResponse<UploadResult>>('/datasources/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  align(id: number) {
    return client.post<APIResponse<AlignResult>>(`/datasources/${id}/align`)
  },
}

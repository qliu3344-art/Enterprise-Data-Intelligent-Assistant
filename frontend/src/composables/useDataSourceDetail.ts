import { shallowRef } from 'vue'
import { datasourceApi } from '@/api/datasource'
import type { DataSourceItem, TestResult } from '@/types/datasource'

export function useDataSourceDetail() {
  const source = shallowRef<DataSourceItem | null>(null)
  const loading = shallowRef(false)
  const testResult = shallowRef<TestResult | null>(null)
  const testLoading = shallowRef(false)

  async function fetch(id: number) {
    loading.value = true
    try {
      const res = await datasourceApi.detail(id)
      source.value = res.data
    } finally {
      loading.value = false
    }
  }

  async function test(id: number) {
    testLoading.value = true
    try {
      const res = await datasourceApi.test(id)
      testResult.value = res.data
    } finally {
      testLoading.value = false
    }
  }

  return { source, loading, testResult, testLoading, fetch, test }
}

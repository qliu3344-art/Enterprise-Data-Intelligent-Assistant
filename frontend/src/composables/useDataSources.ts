import { shallowRef } from 'vue'
import { datasourceApi } from '@/api/datasource'
import type { DataSourceItem } from '@/types/datasource'

export function useDataSources() {
  const items = shallowRef<DataSourceItem[]>([])
  const total = shallowRef(0)
  const loading = shallowRef(false)

  async function fetch(page = 1, pageSize = 20, sourceType = '') {
    loading.value = true
    try {
      const res = await datasourceApi.list(page, pageSize, sourceType)
      items.value = res.data.items
      total.value = res.data.total
    } finally {
      loading.value = false
    }
  }

  async function remove(id: number) {
    await datasourceApi.remove(id)
    items.value = items.value.filter((item) => item.id !== id)
    total.value--
  }

  return { items, total, loading, fetch, remove }
}

import { shallowRef } from 'vue'
import { datasourceApi } from '@/api/datasource'
import type { UploadResult } from '@/types/datasource'

export function useUpload() {
  const uploading = shallowRef(false)
  const lastUpload = shallowRef<UploadResult | null>(null)

  async function upload(file: File) {
    uploading.value = true
    try {
      const res = await datasourceApi.upload(file)
      lastUpload.value = res.data
      return res.data
    } finally {
      uploading.value = false
    }
  }

  return { uploading, lastUpload, upload }
}

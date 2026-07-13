<script setup lang="ts">
defineProps<{ status: string }>()

const statusMap: Record<string, { type: 'success' | 'warning' | 'danger' | 'info'; label: string }> = {
  active: { type: 'success', label: '活跃' },
  inactive: { type: 'info', label: '停用' },
  error: { type: 'danger', label: '异常' },
  raw: { type: 'info', label: '原始' },
  aligned: { type: 'warning', label: '已对齐' },
  cleaned: { type: 'success', label: '已清洗' },
  success: { type: 'success', label: '成功' },
  failed: { type: 'danger', label: '失败' },
  completed: { type: 'success', label: '完成' },
}
</script>

<template>
  <el-tag :type="statusMap[status]?.type || 'info'" size="small">
    {{ statusMap[status]?.label || status }}
  </el-tag>
</template>

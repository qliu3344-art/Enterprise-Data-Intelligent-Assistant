<script setup lang="ts">
import { onMounted, shallowRef, ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useDataSources } from '@/composables/useDataSources'
import { useUpload } from '@/composables/useUpload'
import { useCollect } from '@/composables/useCollect'
import { datasourceApi } from '@/api/datasource'
import type { DataSourceItem, DataSourceForm } from '@/types/datasource'
import StatusTag from '@/components/common/StatusTag.vue'
import { Upload, UploadFilled, Plus, Refresh } from '@element-plus/icons-vue'

const router = useRouter()
const { items, total, loading, fetch, remove } = useDataSources()
const { upload } = useUpload()
const { collecting, triggerSingle, triggerBatch } = useCollect()

const page = shallowRef(1)
const pageSize = 20
const sourceTypeFilter = shallowRef('')
const dialogVisible = shallowRef(false)
const uploadDialogVisible = shallowRef(false)
const editDialogVisible = shallowRef(false)
const selectedRows = ref<DataSourceItem[]>([])
const batchCollecting = shallowRef(false)

const form = shallowRef<DataSourceForm>({
  name: '', source_type: 'excel', file_path: '', delimiter: ',', encoding: 'utf-8',
})
const editForm = shallowRef<DataSourceForm>({ name: '', source_type: 'excel' })
const editingId = shallowRef<number | null>(null)

// Quick stats
const activeCount = computed(() => items.value.filter(i => i.status === 'active').length)
const errorCount = computed(() => items.value.filter(i => i.status === 'error').length)

async function load() {
  await fetch(page.value, pageSize, sourceTypeFilter.value)
  selectedRows.value = []
}

async function handleCreate() {
  try {
    await datasourceApi.create(form.value)
    ElMessage.success('数据源创建成功')
    dialogVisible.value = false
    await load()
  } catch {}
}

async function handleUpload(file: File) {
  const result = await upload(file)
  if (result) {
    form.value.file_path = result.file_path
    form.value.source_type = result.inferred_type as any
    form.value.name = result.file_name
    uploadDialogVisible.value = false
    dialogVisible.value = true
  }
}

async function handleDelete(id: number, name: string) {
  try {
    await ElMessageBox.confirm(`确认删除数据源「${name}」？`, '删除确认', { type: 'warning' })
    await remove(id)
    ElMessage.success('已删除')
  } catch {}
}

async function handleCollect(sourceId: number) {
  const result = await triggerSingle(sourceId)
  if (result) {
    ElMessage.success(`采集完成：${result.record_count} 条记录`)
    await load()
  }
}

async function handleBatchCollect() {
  if (selectedRows.value.length === 0) {
    ElMessage.warning('请先选择数据源')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认对选中的 ${selectedRows.value.length} 个数据源执行批量采集？`, '批量采集', { type: 'info' }
    )
  } catch { return }

  batchCollecting.value = true
  try {
    const ids = selectedRows.value.map(r => r.id)
    const result = await triggerBatch(ids)
    if (result) {
      ElMessage.success(`批量采集完成：${result.success_count} 成功, ${result.failed_count} 失败`)
      await load()
    }
  } finally {
    batchCollecting.value = false
  }
}

function openEditDialog(row: DataSourceItem) {
  editingId.value = row.id
  editForm.value = {
    name: row.name, source_type: row.source_type,
    file_path: row.file_path || undefined,
    delimiter: row.delimiter || ',', encoding: row.encoding || 'utf-8',
    skip_rows: row.skip_rows || 0, sheet_name: row.sheet_name || undefined,
  }
  editDialogVisible.value = true
}

async function handleUpdate() {
  if (editingId.value === null) return
  try {
    await datasourceApi.update(editingId.value, editForm.value)
    ElMessage.success('已更新')
    editDialogVisible.value = false
    await load()
  } catch {}
}

function resetForm() {
  form.value = { name: '', source_type: 'excel', file_path: '', delimiter: ',', encoding: 'utf-8' }
}

onMounted(load)
</script>

<template>
  <div class="ds-page">
    <!-- 统计条 -->
    <div class="quick-stats">
      <div class="qs-item">
        <div class="qs-value">{{ total }}</div>
        <div class="qs-label">数据源总数</div>
      </div>
      <div class="qs-item">
        <div class="qs-value text-green-500">{{ activeCount }}</div>
        <div class="qs-label">正常</div>
      </div>
      <div class="qs-item">
        <div class="qs-value text-red-500">{{ errorCount }}</div>
        <div class="qs-label">异常</div>
      </div>
    </div>

    <!-- 工具栏 -->
    <div class="page-toolbar">
      <div class="flex gap-2 items-center">
        <el-select v-model="sourceTypeFilter" placeholder="全部类型" clearable size="default" style="width: 120px" @change="load">
          <el-option label="Excel" value="excel" />
          <el-option label="CSV" value="csv" />
          <el-option label="MySQL" value="mysql" />
          <el-option label="PDF" value="pdf" />
        </el-select>
        <el-button
          type="warning" plain
          :disabled="selectedRows.length === 0"
          :loading="batchCollecting"
          @click="handleBatchCollect"
        >
          批量采集 {{ selectedRows.length ? `(${selectedRows.length})` : '' }}
        </el-button>
      </div>
      <div class="flex gap-2">
        <el-button @click="load" :loading="loading">
          <el-icon><Refresh /></el-icon>
        </el-button>
        <el-button type="primary" @click="uploadDialogVisible = true">
          <el-icon class="mr-1"><Upload /></el-icon>上传文件
        </el-button>
        <el-button @click="resetForm(); dialogVisible = true">
          <el-icon class="mr-1"><Plus /></el-icon>注册数据源
        </el-button>
      </div>
    </div>

    <!-- 表格 -->
    <el-card class="ds-table-card">
      <el-table
        :data="items" v-loading="loading" stripe
        empty-text="暂无数据源，请上传文件或注册数据源"
        @selection-change="(rows: DataSourceItem[]) => selectedRows = rows"
      >
        <el-table-column type="selection" width="42" />
        <el-table-column prop="id" label="ID" width="60" align="center" />
        <el-table-column prop="name" label="名称" min-width="200" show-overflow-tooltip />
        <el-table-column label="类型" width="80" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.source_type === 'pdf' ? 'danger' : row.source_type === 'mysql' ? '' : ''">
              {{ row.source_type.toUpperCase() }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <StatusTag :status="row.status" />
          </template>
        </el-table-column>
        <el-table-column label="最近采集" width="100" align="right">
          <template #default="{ row }">
            <span class="font-semibold">{{ row.last_collect_count || 0 }}</span>
            <span class="text-gray-400 text-xs ml-1">条</span>
          </template>
        </el-table-column>
        <el-table-column prop="last_collect_at" label="采集时间" width="160" />
        <el-table-column label="操作" width="340" fixed="right">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="router.push(`/datasources/${row.id}`)">详情</el-button>
            <el-button size="small" text @click="openEditDialog(row)">编辑</el-button>
            <el-button size="small" text type="success" :loading="collecting" @click="handleCollect(row.id)">采集</el-button>
            <el-button size="small" text type="danger" @click="handleDelete(row.id, row.name)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="flex justify-end mt-4">
        <el-pagination
          v-model:current-page="page" :total="total" :page-size="pageSize"
          layout="total, prev, pager, next" @current-change="load"
        />
      </div>
    </el-card>

    <!-- 上传对话框 -->
    <el-dialog v-model="uploadDialogVisible" title="上传数据文件" width="480px" center>
      <el-upload drag :http-request="({ file }: any) => handleUpload(file)" :show-file-list="false"
        accept=".xlsx,.xls,.csv,.pdf">
        <el-icon class="text-5xl text-gray-300 mb-3"><UploadFilled /></el-icon>
        <div class="text-gray-500">将文件拖到此处，或<em>点击上传</em></div>
        <template #tip>
          <div class="text-xs text-gray-400 mt-3">支持 Excel (.xlsx/.xls)、CSV (.csv)、PDF (.pdf)</div>
        </template>
      </el-upload>
    </el-dialog>

    <!-- 创建对话框 -->
    <el-dialog v-model="dialogVisible" title="注册数据源" width="520px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如：考勤部-2025.11考勤表" />
        </el-form-item>
        <el-form-item label="类型" required>
          <el-select v-model="form.source_type" style="width: 100%">
            <el-option label="Excel" value="excel" />
            <el-option label="CSV" value="csv" />
            <el-option label="MySQL" value="mysql" />
            <el-option label="PDF" value="pdf" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.source_type !== 'mysql'" label="文件路径">
          <el-input v-model="form.file_path" placeholder="上传后自动填充" />
        </el-form-item>
        <el-form-item label="编码">
          <el-input v-model="form.encoding" placeholder="utf-8" />
        </el-form-item>
        <el-form-item label="分隔符" v-if="form.source_type === 'csv'">
          <el-input v-model="form.delimiter" placeholder="," />
        </el-form-item>
        <el-form-item label="跳过行数">
          <el-input-number v-model="form.skip_rows" :min="0" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 编辑对话框 -->
    <el-dialog v-model="editDialogVisible" title="编辑数据源" width="520px">
      <el-form :model="editForm" label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="类型">
          <el-input :model-value="editForm.source_type?.toUpperCase()" disabled />
        </el-form-item>
        <el-form-item v-if="editForm.source_type !== 'mysql'" label="文件路径">
          <el-input v-model="editForm.file_path" />
        </el-form-item>
        <el-form-item label="编码">
          <el-input v-model="editForm.encoding" />
        </el-form-item>
        <el-form-item label="分隔符" v-if="editForm.source_type === 'csv'">
          <el-input v-model="editForm.delimiter" />
        </el-form-item>
        <el-form-item label="跳过行数">
          <el-input-number v-model="editForm.skip_rows" :min="0" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleUpdate">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.ds-page {
  max-width: 1400px;
}

.quick-stats {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
}

.qs-item {
  background: #fff;
  border-radius: 8px;
  padding: 12px 24px;
  text-align: center;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  min-width: 100px;
}

.qs-value {
  font-size: 24px;
  font-weight: 700;
}

.qs-label {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}

.ds-table-card {
  border: none;
}
</style>

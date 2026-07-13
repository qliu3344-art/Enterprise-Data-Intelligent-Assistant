<script setup lang="ts">
import { shallowRef, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { ragApi } from '@/api/rag'
import type { RAGDocument } from '@/api/rag'

const documents = shallowRef<RAGDocument[]>([])
const totalChunks = shallowRef(0)
const needsReindex = shallowRef(false)
const loading = shallowRef(false)
const reindexing = shallowRef(false)

async function fetchDocuments() {
  loading.value = true
  try {
    const res = await ragApi.documents()
    documents.value = res.data.documents
    totalChunks.value = res.data.documents.reduce((s, d) => s + d.chunks, 0)
    needsReindex.value = res.data.needs_reindex
  } catch (e: any) {
    ElMessage.error('获取文档列表失败')
  } finally {
    loading.value = false
  }
}

async function handleReindex() {
  try {
    await ElMessageBox.confirm(
      '重新索引将重新解析所有文档、向量化并存入数据库，大约需要30秒。是否继续？',
      '确认重建索引',
      { type: 'warning' }
    )
  } catch { return }

  reindexing.value = true
  try {
    const res = await ragApi.reindex()
    ElMessage.success(`索引完成：${res.data.documents} 份文档，${res.data.chunks} 个分块`)
    await fetchDocuments()
  } catch (e: any) {
    ElMessage.error('重建索引失败：' + (e.message || '未知错误'))
  } finally {
    reindexing.value = false
  }
}

function chunkBarWidth(chunks: number): string {
  const max = Math.max(...documents.value.map(d => d.chunks), 1)
  return `${Math.round((chunks / max) * 100)}%`
}

onMounted(() => { fetchDocuments() })
</script>

<template>
  <div class="rag-page">
    <!-- 头部 -->
    <div class="page-toolbar">
      <div>
        <h2 class="text-lg font-bold text-gray-800">RAG 制度文档库</h2>
        <p class="text-sm text-gray-400 mt-0.5">企业制度文档索引管理 · {{ documents.length }} 份文档 · {{ totalChunks }} 个语义分块</p>
      </div>
      <el-button
        type="primary"
        :icon="Refresh"
        :loading="reindexing"
        @click="handleReindex"
      >
        {{ reindexing ? '索引中...' : '重建索引' }}
      </el-button>
    </div>

    <!-- 状态卡片 -->
    <div class="stat-row">
      <div class="stat-card">
        <div class="stat-value">{{ documents.length }}</div>
        <div class="stat-label">制度文档</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ totalChunks }}</div>
        <div class="stat-label">语义分块</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">
          <span :class="needsReindex ? 'text-orange-500' : 'text-green-500'">
            {{ needsReindex ? '待更新' : '已同步' }}
          </span>
        </div>
        <div class="stat-label">索引状态</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ Math.round(totalChunks * 768 * 4 / 1024) }} KB</div>
        <div class="stat-label">向量存储</div>
      </div>
    </div>

    <!-- 文档列表 -->
    <div class="doc-table-card">
      <el-table :data="documents" v-loading="loading" stripe empty-text="暂无已索引文档，请先执行重建索引">
        <el-table-column label="文档名称" min-width="280">
          <template #default="{ row }">
            <div class="doc-name-cell">
              <span class="doc-icon">📄</span>
              <div>
                <div class="doc-title">{{ row.document }}</div>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="分块数" width="120" align="center">
          <template #default="{ row }">
            <span class="chunk-count">{{ row.chunks }} 块</span>
          </template>
        </el-table-column>

        <el-table-column label="分块分布" min-width="200">
          <template #default="{ row }">
            <div class="chunk-bar-wrap">
              <div
                class="chunk-bar"
                :style="{ width: chunkBarWidth(row.chunks) }"
              ></div>
              <span class="chunk-bar-label">{{ row.chunks }}</span>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 技术说明 -->
    <div class="tech-note">
      <div class="tech-note-title">🔧 技术栈</div>
      <div class="tech-tags">
        <span class="tech-tag">BGE-small-zh-v1.5</span>
        <span class="tech-tag-arrow">→</span>
        <span class="tech-tag">768 维向量</span>
        <span class="tech-tag-arrow">→</span>
        <span class="tech-tag">ChromaDB</span>
        <span class="tech-tag-arrow">→</span>
        <span class="tech-tag">BM25 + 向量混合检索</span>
        <span class="tech-tag-arrow">→</span>
        <span class="tech-tag">RRF 融合</span>
      </div>
      <div class="tech-note-text">
        文档按"章-节"语义边界分块（800字/块），相邻块保留条款重叠。查询时 BM25 关键词匹配 + 向量语义匹配并行检索，RRF 融合排序。
      </div>
    </div>
  </div>
</template>

<style scoped>
.rag-page {
  max-width: 960px;
  margin: 0 auto;
}

.page-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}

.stat-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  margin-bottom: 20px;
}

.stat-card {
  background: #fff;
  border-radius: 10px;
  padding: 16px 18px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}

.stat-value { font-size: 24px; font-weight: 700; color: #303133; }
.stat-label { font-size: 12px; color: #909399; margin-top: 4px; }

.doc-table-card {
  background: #fff;
  border-radius: 10px;
  padding: 4px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  margin-bottom: 20px;
}

.doc-name-cell { display: flex; align-items: center; gap: 10px; }
.doc-icon { font-size: 20px; flex-shrink: 0; }
.doc-title { font-size: 14px; font-weight: 600; color: #303133; }

.chunk-count {
  font-weight: 600;
  color: #667eea;
}

.chunk-bar-wrap {
  display: flex;
  align-items: center;
  gap: 10px;
}

.chunk-bar {
  height: 6px;
  border-radius: 3px;
  background: linear-gradient(90deg, #667eea, #764ba2);
  min-width: 4px;
  transition: width 0.3s;
}

.chunk-bar-label {
  font-size: 12px;
  color: #909399;
  flex-shrink: 0;
}

.tech-note {
  background: #f8f9fb;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 14px 18px;
}

.tech-note-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 8px;
}

.tech-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.tech-tag {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
  background: #667eea15;
  color: #667eea;
  font-weight: 600;
}

.tech-tag-arrow {
  font-size: 10px;
  color: #c0c4cc;
}

.tech-note-text {
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}
</style>

<script setup lang="ts">
import { shallowRef, nextTick, ref } from 'vue'
import { ElMessage, ElTag } from 'element-plus'
import { queryApi } from '@/api/query'
import type { QueryResult, RAGSource } from '@/api/query'
import { Promotion } from '@element-plus/icons-vue'

const question = shallowRef('')
const asking = shallowRef(false)
const history = shallowRef<Array<{ q: string; result: QueryResult }>>([])
const chatRef = ref<HTMLElement | null>(null)

const EXAMPLE_QUESTIONS = [
  { q: '总体数据质量怎么样？', type: 'data' },
  { q: '加班超过多少小时算异常？', type: 'doc' },
  { q: '郑十为什么被标记为异常？', type: 'hybrid' },
  { q: 'A级客户的合同金额标准是多少？', type: 'doc' },
  { q: '销售部有多少条异常记录？', type: 'data' },
  { q: '双11期间的订单异常怎么判定？', type: 'doc' },
]

const MODE_CONFIG: Record<string, { label: string; color: string; icon: string }> = {
  agent: { label: 'Agent 查库', color: '#67c23a', icon: '📊' },
  rag: { label: 'RAG 查制度', color: '#409eff', icon: '📋' },
  hybrid: { label: '混合查询', color: '#e6a23c', icon: '🔗' },
}

const INTENT_LABELS: Record<string, string> = {
  data_query: '数据查询',
  doc_query: '制度查询',
  hybrid: '综合分析',
}

const TOOL_LABELS: Record<string, string> = {
  get_summary_stats: '汇总统计',
  query_cleaned_records: '查询记录',
  get_anomaly_details: '异常详情',
}

function scrollToBottom() {
  nextTick(() => {
    chatRef.value?.scrollTo({ top: chatRef.value.scrollHeight, behavior: 'smooth' })
  })
}

async function handleAsk() {
  const q = question.value.trim()
  if (!q || asking.value) return

  asking.value = true
  try {
    const res = await queryApi.ask(q)
    history.value.unshift({ q, result: res.data })
    question.value = ''
    scrollToBottom()
  } catch (e: any) {
    ElMessage.error(e.message || '查询失败')
  } finally {
    asking.value = false
  }
}

function useExample(q: string) {
  question.value = q
  handleAsk()
}

function getSources(result: QueryResult): RAGSource[] {
  if (result.sources?.length) return result.sources
  if (result.rag_data?.sources?.length) return result.rag_data.sources
  return []
}

function getToolNames(tools: string[]): string {
  return tools.map(t => TOOL_LABELS[t] || t).join(' → ')
}

function formatAnswer(text: string): string {
  // Clean markdown code blocks
  return text.replace(/```json|```/g, '').trim()
}
</script>

<template>
  <div class="query-page">
    <!-- 头部说明 -->
    <div class="query-hero">
      <div class="hero-icon">🧠</div>
      <div class="hero-text">
        <h2>企业数据智能助手 <span class="badge-new">RAG+Agent</span></h2>
        <p>基于通义千问，智能路由自动判断意图 —— 查数据库走 Agent，查制度走 RAG，综合分析双引擎融合</p>
      </div>
    </div>

    <div class="query-layout">
      <!-- 对话区 -->
      <div class="chat-panel" ref="chatRef">
        <!-- 欢迎提示 -->
        <div v-if="history.length === 0 && !asking" class="welcome-area">
          <div class="welcome-icon">💬</div>
          <h3 class="welcome-title">开始与数据对话</h3>
          <p class="welcome-desc">问数据、问制度、问综合分析，AI 自动判断走哪条路</p>
          <div class="example-section">
            <div class="example-label">数据查询</div>
            <div class="example-row">
              <div
                v-for="eq in EXAMPLE_QUESTIONS.filter(e => e.type === 'data')" :key="eq.q"
                class="example-chip"
                @click="useExample(eq.q)"
              >{{ eq.q }}</div>
            </div>
            <div class="example-label">制度查询</div>
            <div class="example-row">
              <div
                v-for="eq in EXAMPLE_QUESTIONS.filter(e => e.type === 'doc')" :key="eq.q"
                class="example-chip example-chip--doc"
                @click="useExample(eq.q)"
              >{{ eq.q }}</div>
            </div>
            <div class="example-label">综合分析</div>
            <div class="example-row">
              <div
                v-for="eq in EXAMPLE_QUESTIONS.filter(e => e.type === 'hybrid')" :key="eq.q"
                class="example-chip example-chip--hybrid"
                @click="useExample(eq.q)"
              >{{ eq.q }}</div>
            </div>
          </div>
        </div>

        <!-- 对话历史 -->
        <div v-for="(item, idx) in [...history].reverse()" :key="idx" class="chat-turn">
          <!-- 用户 -->
          <div class="user-msg">
            <div class="msg-bubble user-bubble">{{ item.q }}</div>
            <div class="msg-avatar user-avatar">👤</div>
          </div>

          <!-- Agent -->
          <div class="agent-msg">
            <div class="msg-avatar agent-avatar">🤖</div>
            <div class="msg-content">
              <!-- 模式标签 -->
              <div class="mode-badge" v-if="item.result.mode">
                <span
                  class="mode-tag"
                  :style="{ background: MODE_CONFIG[item.result.mode]?.color + '18', color: MODE_CONFIG[item.result.mode]?.color, borderColor: MODE_CONFIG[item.result.mode]?.color + '40' }"
                >
                  {{ MODE_CONFIG[item.result.mode]?.icon }} {{ MODE_CONFIG[item.result.mode]?.label }}
                </span>
                <span class="intent-tag" v-if="item.result.intent">
                  {{ INTENT_LABELS[item.result.intent] || item.result.intent }}
                </span>
              </div>

              <!-- 回答内容 -->
              <div class="msg-bubble agent-bubble">
                <pre class="answer-text">{{ formatAnswer(item.result.answer) }}</pre>
              </div>

              <!-- 引用来源（RAG / Hybrid） -->
              <div v-if="getSources(item.result).length" class="sources-card">
                <div class="sources-title">📎 引用来源</div>
                <div
                  v-for="(src, si) in getSources(item.result).slice(0, 4)" :key="si"
                  class="source-item"
                >
                  <span class="source-doc">{{ src.doc_title }}</span>
                  <span v-if="src.chapter" class="source-chapter">{{ src.chapter }}</span>
                  <span v-if="src.section" class="source-section">{{ src.section }}</span>
                </div>
              </div>

              <!-- 元信息 -->
              <div class="msg-meta">
                <!-- Agent 模式 -->
                <template v-if="item.result.mode === 'agent' || item.result.agent_data">
                  <span>迭代 {{ item.result.agent_data?.iterations ?? item.result.iterations ?? 0 }} 次</span>
                  <span v-if="(item.result.agent_data?.tools_used ?? item.result.tools_used)?.length">
                    · {{ getToolNames(item.result.agent_data?.tools_used ?? item.result.tools_used ?? []) }}
                  </span>
                </template>
                <!-- RAG 模式 -->
                <template v-if="item.result.mode === 'rag' || item.result.rag_data">
                  <span>检索到 {{ item.result.rag_data?.chunks_count ?? item.result.chunks_count ?? 0 }} 个相关条款</span>
                </template>
              </div>
            </div>
          </div>
        </div>

        <!-- 加载 -->
        <div v-if="asking" class="agent-msg">
          <div class="msg-avatar agent-avatar">🤖</div>
          <div class="msg-content">
            <div class="msg-bubble agent-bubble thinking">
              <span class="thinking-text">
                <span class="dot-pulse">AI 正在分析</span>
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- 输入区 -->
      <div class="input-panel">
        <div class="input-row">
          <el-input
            v-model="question"
            placeholder="问数据或问制度，比如「加班超过多少小时算异常？」"
            size="large"
            :disabled="asking"
            @keyup.enter="handleAsk"
            class="query-input"
          />
          <el-button
            type="primary" size="large"
            :loading="asking" :disabled="!question.trim()"
            @click="handleAsk"
          >
            <el-icon class="mr-1"><Promotion /></el-icon>
            发送
          </el-button>
        </div>
        <p class="input-hint">
          按 Enter 发送 ·
          <span class="hint-data">数据问题走 Agent</span> ·
          <span class="hint-doc">制度问题走 RAG</span> ·
          <span class="hint-hybrid">综合分析双引擎</span>
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.query-page {
  max-width: 960px;
  margin: 0 auto;
  height: calc(100vh - 120px);
  display: flex;
  flex-direction: column;
}

.query-hero {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 20px;
  background: linear-gradient(135deg, #667eea0f, #764ba215);
  border-radius: 12px;
  margin-bottom: 14px;
  flex-shrink: 0;
}

.hero-icon { font-size: 32px; }
.hero-text h2 { font-size: 16px; font-weight: 700; color: #303133; margin: 0 0 3px; display: flex; align-items: center; gap: 8px; }
.hero-text p { font-size: 12px; color: #909399; margin: 0; }

.badge-new {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 10px;
  background: linear-gradient(135deg, #667eea, #764ba2);
  color: #fff;
  letter-spacing: 0.5px;
}

.query-layout {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  overflow: hidden;
}

.chat-panel {
  flex: 1;
  overflow-y: auto;
  padding: 18px 20px;
}

.welcome-area {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 280px;
}

.welcome-icon { font-size: 56px; margin-bottom: 12px; }
.welcome-title { font-size: 18px; font-weight: 700; color: #303133; margin: 0 0 4px; }
.welcome-desc { font-size: 13px; color: #909399; margin: 0 0 20px; }

.example-section {
  width: 100%;
  max-width: 600px;
}

.example-label {
  font-size: 11px;
  color: #c0c4cc;
  margin: 10px 0 6px;
  padding-left: 2px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.example-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.example-chip {
  padding: 7px 14px;
  background: #f0f2f5;
  border-radius: 18px;
  font-size: 12px;
  color: #606266;
  cursor: pointer;
  transition: all 0.15s;
  border: 1px solid transparent;
}

.example-chip:hover {
  background: #667eea;
  color: #fff;
}

.example-chip--doc {
  border-color: #409eff30;
  background: #ecf5ff;
}

.example-chip--doc:hover {
  background: #409eff;
}

.example-chip--hybrid {
  border-color: #e6a23c30;
  background: #fdf6ec;
}

.example-chip--hybrid:hover {
  background: #e6a23c;
  color: #fff;
}

.chat-turn { margin-bottom: 22px; }

.user-msg, .agent-msg {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.user-msg {
  justify-content: flex-end;
  margin-bottom: 10px;
}

.msg-avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}

.user-avatar { background: #e6f7ff; }
.agent-avatar { background: #f0f2f5; }

.msg-content {
  flex: 1;
  min-width: 0;
}

/* 模式标签 */
.mode-badge {
  display: flex;
  gap: 6px;
  margin-bottom: 6px;
  padding-left: 2px;
}

.mode-tag {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid;
  letter-spacing: 0.3px;
}

.intent-tag {
  font-size: 11px;
  color: #909399;
  padding: 2px 6px;
  background: #f5f7fa;
  border-radius: 4px;
}

.msg-bubble {
  padding: 10px 16px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
}

.user-bubble {
  background: #667eea;
  color: #fff;
  border-bottom-right-radius: 4px;
  max-width: 70%;
  display: inline-block;
}

.agent-bubble {
  background: #f5f7fa;
  color: #303133;
  border-bottom-left-radius: 4px;
}

.agent-bubble.thinking { color: #909399; padding: 12px 20px; }

.thinking-text { font-size: 13px; }

.answer-text {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Microsoft YaHei", sans-serif;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  max-height: 500px;
  overflow-y: auto;
}

/* 引用来源卡片 */
.sources-card {
  margin-top: 8px;
  padding: 10px 14px;
  background: #fafbfc;
  border: 1px solid #ebeef5;
  border-radius: 8px;
}

.sources-title {
  font-size: 12px;
  font-weight: 600;
  color: #606266;
  margin-bottom: 6px;
}

.source-item {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 4px 0;
  font-size: 12px;
}

.source-doc {
  font-weight: 600;
  color: #409eff;
}

.source-chapter {
  color: #606266;
  background: #f0f2f5;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 11px;
}

.source-section {
  color: #909399;
  font-size: 11px;
}

.msg-meta {
  margin-top: 5px;
  font-size: 11px;
  color: #c0c4cc;
  padding-left: 4px;
}

.dot-pulse::after {
  content: '';
  animation: dots 1.5s steps(4, end) infinite;
}

@keyframes dots {
  0% { content: ''; }
  25% { content: '.'; }
  50% { content: '..'; }
  75% { content: '...'; }
}

.input-panel {
  border-top: 1px solid #f0f0f0;
  padding: 14px 20px;
  flex-shrink: 0;
}

.input-row {
  display: flex;
  gap: 10px;
}

.query-input {
  flex: 1;
}

.input-hint {
  margin: 6px 0 0;
  font-size: 11px;
  color: #c0c4cc;
  text-align: center;
}

.hint-data { color: #67c23a; }
.hint-doc { color: #409eff; }
.hint-hybrid { color: #e6a23c; }
</style>

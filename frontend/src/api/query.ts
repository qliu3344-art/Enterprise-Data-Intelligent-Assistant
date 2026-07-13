import client from './client'
import type { APIResponse } from '@/types/common'

export interface RAGSource {
  doc_title: string
  file_name: string
  chapter: string
  section: string
  score: number
}

export interface AgentData {
  iterations: number
  tools_used: string[]
}

export interface RAGData {
  chunks_count: number
  sources: RAGSource[]
}

export interface QueryResult {
  question: string
  answer: string
  /** 查询模式：agent / rag / hybrid */
  mode: string
  /** 意图分类：data_query / doc_query / hybrid */
  intent: string
  // Agent 模式字段
  iterations?: number
  tools_used?: string[]
  // RAG 模式字段
  chunks_count?: number
  sources?: RAGSource[]
  // Hybrid 模式嵌套数据
  agent_data?: AgentData
  rag_data?: RAGData
}

export const queryApi = {
  ask(question: string) {
    return client.post<APIResponse<QueryResult>>('/query', { question })
  },
}

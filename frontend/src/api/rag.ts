import client from './client'
import type { APIResponse } from '@/types/common'
import type { RAGSource } from './query'

export interface RAGAskResult {
  answer: string
  sources: RAGSource[]
  chunks_count: number
}

export interface RAGDocument {
  document: string
  chunks: number
}

export interface RAGDocumentsResult {
  documents: RAGDocument[]
  total_documents: number
  needs_reindex: boolean
}

export interface RAGHealthResult {
  status: 'ready' | 'not_indexed'
  indexed_documents: number
}

export const ragApi = {
  ask(question: string) {
    return client.post<APIResponse<RAGAskResult>>('/rag/ask', { question })
  },

  reindex() {
    return client.post<APIResponse<{ documents: number; chunks: number; reindexed: boolean }>>('/rag/reindex')
  },

  documents() {
    return client.get<APIResponse<RAGDocumentsResult>>('/rag/documents')
  },

  health() {
    return client.get<APIResponse<RAGHealthResult>>('/rag/health')
  },
}

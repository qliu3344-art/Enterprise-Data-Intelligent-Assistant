import client from './client'
import type { APIResponse } from '@/types/common'

export interface FeedbackPayload {
  session_id?: string
  question: string
  rating: 1 | -1
  comment?: string
  source?: 'explicit' | 'implicit'
}

export const feedbackApi = {
  submit(data: FeedbackPayload) {
    return client.post<APIResponse<{ id: number; trace_id: number | null }>>('/feedback', data)
  },
}

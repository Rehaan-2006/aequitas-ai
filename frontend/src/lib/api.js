const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

async function request(path, { method = 'GET', accessToken, body } = {}) {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  let data = null
  try {
    data = await res.json()
  } catch {
    data = null
  }

  if (!res.ok) {
    const detail = data?.detail || res.statusText
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }

  return data
}

export const api = {
  research: (accessToken, payload) =>
    request('/api/research', { method: 'POST', accessToken, body: payload }),

  listThreads: (accessToken) =>
    request('/api/threads', { accessToken }),

  getThread: (accessToken, threadId) =>
    request(`/api/threads/${threadId}`, { accessToken }),

  setFeedback: (accessToken, threadId, feedback) =>
    request(`/api/threads/${threadId}/feedback`, { method: 'POST', accessToken, body: { feedback } }),

  exportThread: (accessToken, threadId) =>
    request(`/api/threads/${threadId}/export`, { accessToken }),

  listTemplates: (accessToken) =>
    request('/api/templates', { accessToken }),

  createDraft: (accessToken, threadId, templateId) =>
    request('/api/draft', { method: 'POST', accessToken, body: { thread_id: threadId, template_id: templateId } }),

  approveDraft: (accessToken, draftId) =>
    request(`/api/draft/${draftId}/approve`, { method: 'POST', accessToken }),

  rejectDraft: (accessToken, draftId) =>
    request(`/api/draft/${draftId}/reject`, { method: 'POST', accessToken }),
}

import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 60000,
})

export default {
  // System
  health: () => axios.get('/health'),
  getMethods: () => api.get('/methods'),

  // Typhoon
  typhoonPredict: (payload) => api.post('/typhoon/predict', payload),
  typhoonRuns: () => api.get('/typhoon/runs'),
  typhoonRunDetail: (runId) => api.get(`/typhoon/runs/${runId}`),
  typhoonCategories: () => api.get('/typhoon/categories'),
  typhoonAnalysis: () => api.get('/typhoon/analysis'),
}

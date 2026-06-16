import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: () => import('../views/HomeView.vue'),
  },
  {
    path: '/typhoon/analysis',
    name: 'TyphoonAnalysis',
    component: () => import('../views/typhoon/AnalysisView.vue'),
  },
  {
    path: '/typhoon/methods',
    name: 'TyphoonMethods',
    component: () => import('../views/typhoon/MethodsView.vue'),
  },
  {
    path: '/typhoon/experiments',
    name: 'TyphoonExperiments',
    component: () => import('../views/typhoon/ExperimentsView.vue'),
  },
  {
    path: '/typhoon/experiments/:runId',
    name: 'TyphoonExperimentDetail',
    component: () => import('../views/typhoon/ExperimentDetailView.vue'),
    props: true,
  },
  {
    path: '/typhoon/predict',
    name: 'TyphoonPredict',
    component: () => import('../views/typhoon/PredictView.vue'),
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router

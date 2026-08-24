import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

/**
 * Routes mirror the platform's abstraction, not its implementation:
 * Data -> Analysis -> Models -> Execution -> Results -> Applications.
 */
const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/dashboard' },
  {
    //  The only route somebody without an account can open. `public` keeps the
    //  guard off it and `bare` keeps the app chrome off it, because a reader
    //  arriving from a link has no navigation to use.
    path: '/shared/:token',
    name: 'shared-application',
    component: () => import('@/pages/SharedApplicationPage.vue'),
    meta: { title: 'Shared', public: true, bare: true },
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/pages/LoginPage.vue'),
    meta: { title: 'Sign in', public: true, bare: true },
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: () => import('@/pages/DashboardPage.vue'),
    meta: { title: 'Dashboard' },
  },

  // -- data ----------------------------------------------------------------
  { path: '/sources', name: 'sources', component: () => import('@/pages/data/SourcesPage.vue'), meta: { title: 'Sources' } },
  { path: '/datasets', name: 'datasets', component: () => import('@/pages/data/DatasetsPage.vue'), meta: { title: 'Datasets' } },
  {
    path: '/datasets/:id',
    name: 'dataset-detail',
    component: () => import('@/pages/data/DatasetDetailPage.vue'),
    meta: { title: 'Dataset' },
  },
  { path: '/pipelines', name: 'pipelines', component: () => import('@/pages/data/PipelinesPage.vue'), meta: { title: 'Pipelines' } },

  // -- analysis ------------------------------------------------------------
  { path: '/explore', name: 'explore', component: () => import('@/pages/analysis/ExplorePage.vue'), meta: { title: 'Explore' } },
  {
    path: '/visualizations',
    name: 'visualizations',
    component: () => import('@/pages/analysis/VisualizationsPage.vue'),
    meta: { title: 'Visualizations' },
  },
  {
    path: '/dashboards',
    name: 'dashboards',
    component: () => import('@/pages/analysis/DashboardsPage.vue'),
    meta: { title: 'Dashboards' },
  },

  // -- models --------------------------------------------------------------
  {
    path: '/models',
    name: 'models',
    component: () => import('@/pages/models/ModelLibraryPage.vue'),
    meta: { title: 'Model library' },
  },
  {
    path: '/models/:id',
    name: 'model-detail',
    component: () => import('@/pages/models/ModelDetailPage.vue'),
    meta: { title: 'Model' },
  },
  {
    path: '/experiments',
    name: 'experiments',
    component: () => import('@/pages/models/ExperimentsPage.vue'),
    meta: { title: 'Experiments' },
  },
  {
    path: '/executions',
    name: 'executions',
    component: () => import('@/pages/models/ExecutionsPage.vue'),
    meta: { title: 'Executions' },
  },
  {
    path: '/executions/:id',
    name: 'execution-detail',
    component: () => import('@/pages/models/ExecutionDetailPage.vue'),
    meta: { title: 'Execution' },
  },
  {
    path: '/evaluation',
    name: 'evaluation',
    component: () => import('@/pages/models/EvaluationPage.vue'),
    meta: { title: 'Evaluation' },
  },

  // -- results -------------------------------------------------------------
  { path: '/results', name: 'results', component: () => import('@/pages/results/ResultsPage.vue'), meta: { title: 'Results' } },
  { path: '/reports', name: 'reports', component: () => import('@/pages/results/ReportsPage.vue'), meta: { title: 'Reports' } },

  // -- applications --------------------------------------------------------
  {
    path: '/applications',
    name: 'applications',
    component: () => import('@/pages/applications/ApplicationsPage.vue'),
    meta: { title: 'Applications' },
  },
  {
    path: '/applications/typhoon',
    name: 'typhoon',
    component: () => import('@/pages/applications/TyphoonPage.vue'),
    meta: { title: 'Typhoon analog forecast' },
  },
  {
    //  A composed application's own page. Declared after the built-in route
    //  for readability only - the router ranks a static segment above a
    //  dynamic one, so `/applications/typhoon` wins either way.
    path: '/applications/:id',
    name: 'application-view',
    component: () => import('@/pages/applications/ApplicationViewPage.vue'),
    meta: { title: 'Application' },
  },

  // -- system --------------------------------------------------------------
  { path: '/schedules', name: 'schedules', component: () => import('@/pages/system/SchedulesPage.vue'), meta: { title: 'Schedules' } },
  {
    path: '/users',
    name: 'users',
    component: () => import('@/pages/system/UsersPage.vue'),
    meta: { title: 'Users', permission: 'platform:admin' },
  },
  {
    path: '/audit',
    name: 'audit',
    component: () => import('@/pages/system/AuditPage.vue'),
    meta: { title: 'Audit' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('@/pages/system/SettingsPage.vue'),
    meta: { title: 'Settings' },
  },

  //  Jobs was Executions filtered by status; Schemas is shown on the dataset
  //  it belongs to. Both were removed, so their URLs redirect rather than 404.
  //  Deployment was a second name for an application's published state.
  { path: '/deployments', redirect: '/applications' },
  { path: '/jobs', redirect: '/executions' },
  { path: '/schemas', redirect: '/datasets' },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

/**
 * One guard for the whole app: resolve the session first, then send anyone
 * unauthenticated to the sign-in page, and anyone without the route's
 * permission back to the dashboard.
 */
router.beforeEach(async (to) => {
  const store = useAuthStore()
  if (!store.ready) await store.initialise()

  if (to.meta.public) {
    return store.isSignedIn && to.name === 'login' ? { name: 'dashboard' } : true
  }
  if (!store.isSignedIn) {
    return { name: 'login', query: to.fullPath === '/' ? {} : { redirect: to.fullPath } }
  }
  const required = to.meta.permission as string | undefined
  if (required && !store.may(required)) {
    return { name: 'dashboard' }
  }
  return true
})

router.afterEach((to) => {
  const title = (to.meta.title as string) ?? ''
  document.title = title ? `${title} · flux-data-engine` : 'flux-data-engine'
})

export default router

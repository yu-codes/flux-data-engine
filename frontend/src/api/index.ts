/** One typed function per endpoint, grouped by domain. */

import { cached } from './cache'
import { api, requestText } from './client'
import type {
  Application,
  ApplicationView,
  AuditEntry,
  ChartData,
  ColumnProfile,
  Dashboard,
  DataSchema,
  Dataset,
  DatasetDetail,
  DatasetVersion,
  Evaluation,
  Execution,
  Experiment,
  ExperimentCheck,
  ExperimentComparison,
  InvokeAnswer,
  Leaderboard,
  LineageGraphData,
  ModelDefinition,
  ModelVersion,
  Overview,
  Pipeline,
  PipelineRun,
  PlatformInfo,
  Preview,
  ProviderDescriptor,
  RenderedDashboard,
  RenderedReport,
  Report,
  ResultRecord,
  Schedule,
  Source,
  TransformSpec,
  TyphoonPrediction,
  TyphoonSummary,
  UserAccount,
  Visualization,
  Workspace,
  WorkspaceMember,
  Job,
} from '@/types'

/**
 * An endpoint whose answer cannot change while the tab is open.
 *
 * The provider catalogue, the transform vocabulary, the chart types: every
 * page that renders a form asks for these, and every page used to ask again.
 * They are the only things cached, because they are the only things where
 * "the answer from a minute ago" is certainly still the answer.
 */
const reference = <T>(path: string) => () => cached(path, () => api.get<T>(path))

// -- platform --------------------------------------------------------------
export const platform = {
  info: reference<PlatformInfo>('/info'),
  overview: () => api.get<Overview>('/overview'),
  executionQueue: () =>
    api.get<{ mode: string; pending: unknown[]; running: unknown[]; failed: unknown[] }>(
      '/execution-queue',
    ),
  metricsSummary: () =>
    api.get<{ uptime_seconds: number; requests_total: number; requests_failed_total: number }>(
      '/metrics/summary',
    ),
}

// -- authentication --------------------------------------------------------
export const jobs = {
  list: (query = '') => api.get<Job[]>(`/jobs${query}`),
  get: (id: string) => api.get<Job>(`/jobs/${id}`),
  cancel: (id: string) => api.post<Job>(`/jobs/${id}/cancel`),
  retry: (id: string) => api.post<Job>(`/jobs/${id}/retry`),
  kinds: reference<{ kinds: string[] }>('/job-kinds'),
}

export const workspaces = {
  list: () => api.get<Workspace[]>('/workspaces'),
  create: (body: { name: string; description?: string }) =>
    api.post<Workspace>('/workspaces', body),
  update: (id: string, body: { name?: string; description?: string }) =>
    api.patch<Workspace>(`/workspaces/${id}`, body),
  remove: (id: string) => api.del(`/workspaces/${id}`),
  members: (id: string) => api.get<WorkspaceMember[]>(`/workspaces/${id}/members`),
  addMember: (id: string, body: { user_id: string; role: string }) =>
    api.post<WorkspaceMember>(`/workspaces/${id}/members`, body),
  removeMember: (id: string, userId: string) =>
    api.del(`/workspaces/${id}/members/${userId}`),
}

export const auth = {
  config: reference<{ auth_enabled: boolean; roles: string[]; permissions: string[] }>(
    '/auth/config',
  ),
  login: (email: string, password: string) =>
    api.post<{ access_token: string; token_type: string; expires_in: number; user: UserAccount }>(
      '/auth/login',
      { email, password },
    ),
  me: () => api.get<UserAccount>('/auth/me'),
  changePassword: (currentPassword: string, newPassword: string) =>
    api.post<UserAccount>('/auth/password', {
      current_password: currentPassword,
      new_password: newPassword,
    }),

  listUsers: () => api.get<UserAccount[]>('/users'),
  createUser: (body: Record<string, unknown>) => api.post<UserAccount>('/users', body),
  updateUser: (id: string, body: Record<string, unknown>) =>
    api.patch<UserAccount>(`/users/${id}`, body),
  deleteUser: (id: string) => api.del(`/users/${id}`),

  audit: (query = '') => api.get<AuditEntry[]>(`/audit${query}`),
}

// -- pipelines -------------------------------------------------------------
export const pipelines = {
  list: () => api.get<Pipeline[]>('/pipelines'),
  get: (id: string) => api.get<Pipeline>(`/pipelines/${id}`),
  /** Keep what Explore is showing, as a pipeline that can be re-run. */
  fromQuery: (body: Record<string, unknown>) =>
    api.post<Pipeline>('/pipelines/from-query', body),
  graph: (id: string) =>
    api.get<{
      nodes: { id: string; label: string; type: string; model_name?: string; kind?: string }[]
      edges: { from: string; to: string }[]
      terminal_steps: string[]
    }>(`/pipelines/${id}/graph`),
  create: (body: Record<string, unknown>) => api.post<Pipeline>('/pipelines', body),
  update: (id: string, body: Record<string, unknown>) => api.patch<Pipeline>(`/pipelines/${id}`, body),
  remove: (id: string) => api.del(`/pipelines/${id}`),
  run: (id: string, body: Record<string, unknown> = {}) =>
    api.post<PipelineRun>(`/pipelines/${id}/run`, body),
  /** Submit the run as a job and return immediately. */
  runInBackground: (id: string, body: Record<string, unknown> = {}) =>
    api.post<{ job_id: string; status: string }>(
      `/pipelines/${id}/run?background=true`,
      body,
    ),
  runs: (pipelineId?: string) =>
    api.get<PipelineRun[]>(`/pipeline-runs${pipelineId ? `?pipeline_id=${pipelineId}` : ''}`),
  run_detail: (runId: string) => api.get<PipelineRun>(`/pipeline-runs/${runId}`),
}

// -- reports ---------------------------------------------------------------
export const reports = {
  sectionKinds: reference<{ kinds: string[]; formats: string[] }>('/report-sections'),
  list: () => api.get<Report[]>('/reports'),
  get: (id: string) => api.get<Report>(`/reports/${id}`),
  create: (body: Record<string, unknown>) => api.post<Report>('/reports', body),
  update: (id: string, body: Record<string, unknown>) => api.patch<Report>(`/reports/${id}`, body),
  remove: (id: string) => api.del(`/reports/${id}`),
  render: (id: string) => api.get<RenderedReport>(`/reports/${id}/render`),
  exportAs: (id: string, format: string) =>
    requestText(`/reports/${id}/export?format=${format}&download=false`),
}

// -- schedules -------------------------------------------------------------
export const schedules = {
  statuses: () =>
    api.get<{ statuses: string[]; min_interval_seconds: number }>('/schedule-statuses'),
  list: (status?: string) => api.get<Schedule[]>(`/schedules${status ? `?status=${status}` : ''}`),
  get: (id: string) => api.get<Schedule>(`/schedules/${id}`),
  create: (body: Record<string, unknown>) => api.post<Schedule>('/schedules', body),
  update: (id: string, body: Record<string, unknown>) => api.patch<Schedule>(`/schedules/${id}`, body),
  remove: (id: string) => api.del(`/schedules/${id}`),
  pause: (id: string) => api.post<Schedule>(`/schedules/${id}/pause`),
  resume: (id: string) => api.post<Schedule>(`/schedules/${id}/resume`),
  runNow: (id: string) => api.post<Schedule>(`/schedules/${id}/run`),
  preview: (body: Record<string, unknown>) =>
    api.post<{ next_runs: string[] }>('/schedules/preview', body),
}

// -- data ------------------------------------------------------------------
export const data = {
  sourceTypes: reference<{ types: string[] }>('/sources/types'),
  listSources: () => api.get<Source[]>('/sources'),
  getSource: (id: string) => api.get<Source>(`/sources/${id}`),
  createSource: (body: { name: string; type: string; connection: Record<string, unknown>; description?: string }) =>
    api.post<Source>('/sources', body),
  previewSource: (id: string, limit = 50) => api.get<Preview>(`/sources/${id}/preview?limit=${limit}`),
  deleteSource: (id: string) => api.del(`/sources/${id}`),

  /** Curated by default; pass include=intermediate|all for pipeline working state. */
  listDatasets: (query = '') => api.get<Dataset[]>(`/datasets${query}`),
  getDataset: (id: string) => api.get<DatasetDetail>(`/datasets/${id}`),
  createDataset: (body: { name: string; source_id: string; description?: string; tags?: string[] }) =>
    api.post<DatasetDetail>('/datasets', body),
  uploadDataset: (form: FormData) => api.upload<DatasetDetail>('/datasets/upload', form),
  refreshDataset: (id: string) => api.post<DatasetVersion>(`/datasets/${id}/refresh`),
  previewDataset: (id: string, limit = 50) => api.get<Preview>(`/datasets/${id}/preview?limit=${limit}`),
  deleteDataset: (id: string) => api.del(`/datasets/${id}`),
  previewVersion: (versionId: string, limit = 50, offset = 0) =>
    api.get<Preview>(`/dataset-versions/${versionId}/preview?limit=${limit}&offset=${offset}`),

  listSchemas: () => api.get<DataSchema[]>('/schemas'),
  getSchema: (id: string) => api.get<DataSchema>(`/schemas/${id}`),
}

// -- analysis --------------------------------------------------------------
export const analysis = {
  chartOptions: reference<{ chart_types: string[]; aggregations: string[] }>('/chart-options'),
  profile: (versionId: string) =>
    api.get<{ version_id: string; row_count: number; column_count: number; columns: ColumnProfile[] }>(
      `/explore/${versionId}/profile`,
    ),
  query: (versionId: string, body: Record<string, unknown>) =>
    api.post<{ rows: Record<string, unknown>[]; total: number; columns: unknown[] }>(
      `/explore/${versionId}/query`,
      body,
    ),
  series: (versionId: string, spec: Record<string, unknown>) =>
    api.post<ChartData>(`/explore/${versionId}/series`, spec),

  listVisualizations: () => api.get<Visualization[]>('/visualizations'),
  createVisualization: (body: Record<string, unknown>) => api.post<Visualization>('/visualizations', body),
  renderVisualization: (id: string) => api.get<ChartData>(`/visualizations/${id}/render`),
  deleteVisualization: (id: string) => api.del(`/visualizations/${id}`),

  listDashboards: () => api.get<Dashboard[]>('/dashboards'),
  createDashboard: (body: Record<string, unknown>) => api.post<Dashboard>('/dashboards', body),
  renderDashboard: (id: string) => api.get<RenderedDashboard>(`/dashboards/${id}/render`),
  updateDashboard: (id: string, body: Record<string, unknown>) => api.patch<Dashboard>(`/dashboards/${id}`, body),
  deleteDashboard: (id: string) => api.del(`/dashboards/${id}`),

  addTile: (id: string, body: Record<string, unknown>) =>
    api.post<Dashboard>(`/dashboards/${id}/tiles`, body),
  updateTile: (id: string, visualizationId: string, body: Record<string, unknown>) =>
    api.patch<Dashboard>(`/dashboards/${id}/tiles/${visualizationId}`, body),
  removeTile: (id: string, visualizationId: string) =>
    api.del(`/dashboards/${id}/tiles/${visualizationId}`),
}

// -- models ----------------------------------------------------------------
export const models = {
  providers: reference<{ providers: ProviderDescriptor[] }>('/model-providers'),
  /** Run a model and get the answer back. Nothing is recorded. */
  invoke: (id: string, body: Record<string, unknown>) =>
    api.post<InvokeAnswer>(`/models/${id}/invoke`, body),
  types: reference<{ types: { type: string; providers: { key: string; name: string; trainable: boolean }[] }[] }>('/model-types'),
  /** The curated library: what a person browses and picks from. */
  list: (query = '') => api.get<ModelDefinition[]>(`/models${query}`),
  /**
   * Library plus pipeline-owned step models. Needed wherever a model id has to
   * be resolved to a name — an execution or a schedule may point at either, and
   * showing a raw id because the library did not contain it is worse than the
   * clutter the library filter exists to prevent.
   */
  all: () => api.get<ModelDefinition[]>('/models'),
  setStatus: (id: string, status: 'active' | 'deprecated') =>
    api.post<ModelDefinition>(`/models/${id}/status`, { status }),
  get: (id: string) => api.get<ModelDefinition>(`/models/${id}`),
  create: (body: Record<string, unknown>) => api.post<ModelDefinition>('/models', body),
  update: (id: string, body: Record<string, unknown>) => api.patch<ModelDefinition>(`/models/${id}`, body),
  remove: (id: string) => api.del(`/models/${id}`),
  validate: (id: string) => api.get<{ valid: boolean; errors: string[]; warnings: string[] }>(`/models/${id}/validate`),
  versions: (id: string) => api.get<ModelVersion[]>(`/models/${id}/versions`),
  publishVersion: (id: string, notes = '') =>
    api.post<ModelVersion>(`/models/${id}/versions?notes=${encodeURIComponent(notes)}`),

  transforms: reference<{ transforms: TransformSpec[] }>('/transforms'),

  listExperiments: () => api.get<Experiment[]>('/experiments'),
  createExperiment: (body: Record<string, unknown>) => api.post<Experiment>('/experiments', body),
  updateExperiment: (id: string, body: Record<string, unknown>) =>
    api.patch<Experiment>(`/experiments/${id}`, body),
  deleteExperiment: (id: string) => api.del(`/experiments/${id}`),
  /** Whether every trial can run, checked before anything executes. */
  checkExperiment: (id: string) => api.get<ExperimentCheck>(`/experiments/${id}/check`),
  /** Run every trial as one act — the unit of execution is the experiment. */
  runExperiment: (id: string) => api.post<Experiment>(`/experiments/${id}/run`),
  compareExperiments: (experimentIds: string[], metric?: string, includeHistory = false) =>
    api.post<ExperimentComparison>('/experiments/compare', {
      experiment_ids: experimentIds,
      metric: metric ?? null,
      include_history: includeHistory,
    }),
  leaderboard: (id: string) => api.get<Leaderboard>(`/experiments/${id}/leaderboard`),

  listEvaluations: (query = '') => api.get<Evaluation[]>(`/evaluations${query}`),
  createEvaluation: (body: Record<string, unknown>) => api.post<Evaluation>('/evaluations', body),
}

// -- executions and results ------------------------------------------------
export const executions = {
  kinds: reference<{ kinds: string[]; statuses: string[] }>('/execution-kinds'),
  list: (query = '') => api.get<Execution[]>(`/executions${query}`),
  get: (id: string) => api.get<Execution>(`/executions/${id}`),
  submit: (body: Record<string, unknown>) => api.post<Execution>('/executions', body),
  cancel: (id: string) => api.post<Execution>(`/executions/${id}/cancel`),
}

export const results = {
  kinds: reference<{ kinds: string[] }>('/result-kinds'),
  list: (query = '') => api.get<ResultRecord[]>(`/results${query}`),
  get: (id: string) => api.get<ResultRecord>(`/results/${id}`),
  payload: (id: string, limit = 200) => api.get<{ result_id: string; payload: unknown }>(`/results/${id}/payload?limit=${limit}`),
  forExecution: (executionId: string) => api.get<ResultRecord | null>(`/executions/${executionId}/result`),
  materialise: (id: string, datasetName: string) =>
    api.post<{ dataset_id: string; dataset_version_id: string }>(`/results/${id}/materialise`, {
      dataset_name: datasetName,
    }),
  remove: (id: string) => api.del(`/results/${id}`),
}

// -- applications ----------------------------------------------------------
export const lineage = {
  /** Where something came from, or what depends on it. */
  trace: (
    kind: string,
    id: string,
    params: { direction: 'up' | 'down'; depth?: number },
  ) =>
    api.get<LineageGraphData>(
      `/lineage/${kind}/${id}?direction=${params.direction}&depth=${params.depth ?? 4}`,
    ),
  kinds: reference<{ kinds: string[] }>('/lineage-kinds'),
}

export const applications = {
  list: () => api.get<Application[]>('/applications'),
  get: (id: string) => api.get<Application>(`/applications/${id}`),
  //  The application as a page rather than as three lists of ids.
  view: (id: string) => api.get<ApplicationView>(`/applications/${id}/view`),
  create: (body: Record<string, unknown>) => api.post<Application>('/applications', body),
  publish: (id: string) => api.post<Application>(`/applications/${id}/publish`),
  share: (id: string) =>
    api.post<{ share_url: string; token: string; visibility: string }>(
      `/applications/${id}/share`,
    ),
  unshare: (id: string) => api.del(`/applications/${id}/share`),
  unpublish: (id: string) => api.post<Application>(`/applications/${id}/unpublish`),
  remove: (id: string) => api.del(`/applications/${id}`),
}

// -- the built-in typhoon application --------------------------------------
export const typhoon = {
  methods: () =>
    api.get<{
      default: string
      methods: { key: string; description: string }[]
      rainfall_regions: { code: string; label: string }[]
      precipitation_available: boolean
    }>('/applications/typhoon/methods'),
  categories: () => api.get<{ categories: { category: string; description: string }[] }>('/applications/typhoon/categories'),
  coastline: (bufferKm: number) =>
    api.get<{ buffer_km: number; coastline: { lat: number; lon: number }[]; buffer: { lat: number; lon: number }[] }>(
      `/applications/typhoon/coastline?buffer_km=${bufferKm}`,
    ),
  list: (params: { search?: string; category?: string; limit?: number } = {}) => {
    const query = new URLSearchParams()
    if (params.search) query.set('search', params.search)
    if (params.category) query.set('category', params.category)
    query.set('limit', String(params.limit ?? 200))
    return api.get<{ total: number; typhoons: TyphoonSummary[] }>(`/applications/typhoon/typhoons?${query}`)
  },
  track: (typhoonId: string, bufferKm: number) =>
    api.get<{ typhoon_id: string; name_zh: string; track: { lat: number; lon: number; in_range: boolean }[] }>(
      `/applications/typhoon/typhoons/${typhoonId}/track?buffer_km=${bufferKm}`,
    ),
  predict: (body: Record<string, unknown>) => api.post<TyphoonPrediction>('/applications/typhoon/predict', body),
  precipitation: (body: Record<string, unknown>) =>
    api.post<Record<string, unknown>>('/applications/typhoon/precipitation', body),
}

/** Shapes returned by the flux-data-engine API. */

/** A field only applies when another field has a particular value. */
export interface VisibleWhen {
  field: string
  equals: unknown
  in: unknown[] | null
}

export interface FieldSpec {
  name: string
  type: string
  required: boolean
  nullable: boolean
  description: string
  default: unknown
  enum: unknown[] | null
  unit: string | null
  /** A nested object with a known shape. */
  fields?: FieldSpec[] | null
  /** A list; every element looks like this. */
  item?: FieldSpec | null
  /** A mapping with user-chosen keys; every value looks like this. */
  values?: FieldSpec | null
  visible_when?: VisibleWhen | null
}

export interface Contract {
  shape: string
  description: string
  fields: FieldSpec[]
}

/** One entry in the standard transform vocabulary a pipeline is built from. */
export interface TransformSpec {
  key: string
  name: string
  description: string
  parameters: Contract
}

export interface Source {
  id: string
  name: string
  type: string
  connection: Record<string, unknown>
  description: string
  created_at: string
}

export interface DatasetVersion {
  id: string
  dataset_id: string
  version: number
  row_count: number
  column_count: number
  schema_id: string | null
  storage_uri: string
  lineage: Record<string, unknown>
  created_at: string
}

export interface Dataset {
  id: string
  name: string
  origin: string
  source_id: string | null
  description: string
  tags: string[]
  current_version_id: string | null
  created_at: string
  updated_at: string
}

export interface DatasetDetail extends Dataset {
  versions: DatasetVersion[]
  schema_fields: FieldSpec[]
}

export interface DataSchema {
  id: string
  name: string
  description: string
  fields: FieldSpec[]
  created_at: string
}

export interface Preview {
  columns: FieldSpec[]
  rows: Record<string, unknown>[]
  row_count: number
  version_id?: string | null
  version?: number | null
}

export interface ProviderDescriptor {
  key: string
  //  What answered, and how long it may take. Both are read by the platform:
  //  the version travels into an execution's lineage, the timeout becomes its
  //  deadline.
  version: string
  timeout_seconds: number | null
  name: string
  model_type: string
  runtime: string
  description: string
  trainable: boolean
  supported_kinds: string[]
  parameter_contract: Contract
  input_contract: Contract
  output_contract: Contract
  configuration_contract: Contract
  examples: { name: string; configuration?: Record<string, unknown>; parameters?: Record<string, unknown> }[]
}

/**
 * What a model can do. The UI renders from this rather than switching on
 * `type`, so a new category needs no new branch.
 */
export interface ModelCapabilities {
  executable: boolean
  execution_kinds: string[]
  trainable: boolean
  versionable: boolean
  configurable: boolean
  open_input: boolean
  open_output: boolean
}

export interface ModelDefinition {
  id: string
  name: string
  slug: string
  description: string
  type: string
  status: 'active' | 'deprecated'
  /** Computed: the working definition differs from the version that executes. */
  has_unpublished_changes: boolean
  capabilities: ModelCapabilities
  provider: string
  runtime: string
  trainable: boolean
  tags: string[]
  configuration: Record<string, unknown>
  input_contract: Contract
  parameter_contract: Contract
  output_contract: Contract
  metadata: Record<string, unknown>
  current_version_id: string | null
  created_at: string
  updated_at: string
}

export interface ModelVersion {
  id: string
  model_id: string
  version: number
  parameters: Record<string, unknown>
  metrics: Record<string, unknown>
  artifact_uri: string | null
  created_by_execution_id: string | null
  notes: string
  created_at: string
}

export interface Execution {
  id: string
  //  What ran, and what kind of runnable it was. `model_id` is null for
  //  anything that is not a model, rather than borrowed from `target_id`:
  //  a caller filtering by model must not match a pipeline.
  target_id: string | null
  target_type: string
  model_id: string | null
  model_version_id: string | null
  kind: string
  status: string
  runtime: string
  dataset_version_id: string | null
  parameters: Record<string, unknown>
  context: Record<string, unknown>
  metrics: Record<string, unknown>
  lineage: Record<string, unknown>
  logs: string[]
  error: string | null
  result_id: string | null
  produced_model_version_id: string | null
  experiment_id: string | null
  duration_seconds: number | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface ResultRecord {
  id: string
  execution_id: string
  kind: string
  summary: Record<string, unknown>
  metrics: Record<string, unknown>
  dataset_id: string | null
  dataset_version_id: string | null
  artifact_uri: string | null
  row_count: number | null
  is_materialised: boolean
  created_at: string
}

/** One thing being compared: a runnable, configured a particular way. */
export interface ExperimentTrial {
  target_id: string
  target_type: string
  //  Empty for a pipeline trial: "which model" has an honest answer either way.
  model_id: string
  label: string
  parameters: Record<string, unknown>
  model_version_id: string | null
  kind: string | null
}

export interface TrialCheck {
  label: string
  target_id?: string
  target_type?: string
  model_id: string
  model_name: string | null
  runnable: boolean
  errors: string[]
  warnings: string[]
  kinds?: string[]
}

export interface ExperimentCheck {
  experiment_id: string
  runnable: boolean
  errors: string[]
  warnings: string[]
  trials: TrialCheck[]
}

export interface ExperimentComparison {
  experiments: { id: string; name: string; primary_metric: string }[]
  metric_names: string[]
  ranked_by: string | null
  rows: {
    experiment_id: string
    experiment: string
    trial: string
    model_id: string
    model: string
    execution_id: string
    finished_at: string | null
    metrics: Record<string, number>
  }[]
}

export interface Experiment {
  id: string
  name: string
  description: string
  objective: string
  primary_metric: string
  //  Accuracy is higher, RMSE is lower. Declared, because a metric's name does
  //  not say which.
  primary_direction: 'higher' | 'lower'
  dataset_version_id: string | null
  trials: ExperimentTrial[]
  model_ids: string[]
  execution_ids: string[]
  created_at: string
}

export interface LeaderboardRow {
  trial: string
  target_id: string
  target_type: string
  model_id: string
  model_name: string
  model_type: string
  provider: string
  evaluation_id: string | null
  execution_id: string | null
  metrics: Record<string, unknown>
  primary_value: number | null
  passed: boolean | null
  evaluated_at: string | null
}

export interface Leaderboard {
  experiment_id: string
  name: string
  objective: string
  primary_metric: string
  primary_direction: 'higher' | 'lower'
  metric_names: string[]
  rows: LeaderboardRow[]
}

export interface Evaluation {
  id: string
  execution_id: string
  model_id: string | null
  experiment_id: string | null
  metrics: Record<string, unknown>
  target: Record<string, unknown>
  passed: boolean | null
  notes: string
  created_at: string
}

export interface ChartSpec {
  chart_type: string
  x: string | null
  y: string[]
  series: string | null
  aggregation: string
  limit: number
  sort_by: string | null
  sort_desc: boolean
  x_title: string
  y_title: string
  unit: string
  subtitle: string
  value_labels: boolean
  options: Record<string, unknown>
}

export interface Visualization {
  id: string
  name: string
  description: string
  spec: ChartSpec
  dataset_id: string | null
  dataset_version_id: string | null
  result_id: string | null
  created_at: string
}

export interface ChartData {
  categories: (string | number | null)[]
  series: { name: string; data: (number | null)[] }[]
  chart_type: string
  /** Presentation metadata the renderer needs to label the chart properly. */
  x_title?: string
  y_title?: string
  unit?: string
  subtitle?: string
  value_labels?: boolean
  aggregation?: string
  row_count?: number
  name?: string
  error?: string
  visualization_id?: string
  /** Heatmap and cohort charts: what the bands along the side are. */
  band_title?: string
  /** Histogram: the shape of the column, summarised. */
  distribution?: {
    column: string
    min: number
    max: number
    mean: number
    median: number
    p90: number
    bins: number
    counted: number
  }
  /** Box plot: points outside the whiskers, and how many rows each box holds. */
  outliers?: { category: string | number | null; value: number }[]
  group_sizes?: number[]
}

export interface Dashboard {
  id: string
  name: string
  description: string
  tiles: { visualization_id: string; x: number; y: number; width: number; height: number }[]
  created_at: string
}

export interface RenderedDashboard {
  id: string
  name: string
  description: string
  tiles: {
    visualization_id: string
    x: number
    y: number
    width: number
    height: number
    chart: ChartData
  }[]
}

export interface ApplicationView {
  name: string
  description: string
  slug: string
  dashboards: RenderedDashboard[]
  //  The models it bundles, described well enough to build a form from. Empty
  //  in the shared view: a link holder reads, and does not spend compute.
  tools: ApplicationTool[]
  //  What the tools may be run against: the datasets this application bundles.
  datasets: { id: string; name: string }[]
  built_from: { models: number; datasets: number; dashboards: number }
}

export interface Application {
  id: string
  name: string
  slug: string
  kind: string
  description: string
  status: string
  model_ids: string[]
  dataset_ids: string[]
  dashboard_ids: string[]
  configuration: Record<string, unknown>
  entrypoint: string | null
  created_at: string
  updated_at: string
  visibility: string
  is_shared: boolean
}

export interface PlatformInfo {
  name: string
  abstraction: string
  execution_mode: string
  storage_backend: string
  auth_enabled: boolean
  scheduler_enabled: boolean
  source_types: string[]
  model_types: string[]
  runtimes: string[]
  execution_kinds: string[]
  execution_statuses: string[]
  result_kinds: string[]
  providers: { key: string; name: string; model_type: string; trainable: boolean }[]
}

export interface Overview {
  counts: Record<string, number>
  models_by_type: Record<string, number>
  recent_executions: {
    id: string
    target_id: string | null
    target_type: string
    model_id: string | null
    kind: string
    status: string
    duration_seconds: number | null
    created_at: string
    result_id: string | null
  }[]
}

export interface ColumnProfile {
  name: string
  type: string
  count: number
  null_count: number
  null_ratio: number
  distinct_count: number
  min?: number
  max?: number
  mean?: number
  median?: number
  stddev?: number
  top_values?: { value: unknown; count: number }[]
}

// -- typhoon application ---------------------------------------------------
export interface TrackCoord {
  lat: number
  lon: number
  in_range?: boolean
}

export interface TyphoonAnalog {
  typhoon_id: string
  name_zh: string
  name_en: string
  year: number
  category: string
  category_label: string
  offset_km: number
  score: number
  landfall_location: string | null
  event_rain: Record<string, number | null>
  track: TrackCoord[]
}

export interface TyphoonPrediction {
  execution_id: string
  result_id: string
  status: string
  metrics: Record<string, unknown>
  duration_seconds: number | null
  method: string
  method_description: string
  predicted_category: string | null
  confidence: number
  category_votes: Record<string, number>
  analogs: TyphoonAnalog[]
  rainfall: { stations: Record<string, RainfallStation> } | null
  buffer_km: number
  distance_unit: string
  query: { typhoon_id: string | null; track: TrackCoord[] }
  geometry: { buffer_km: number; coastline: TrackCoord[]; buffer: TrackCoord[] }
}

export interface RainfallStation {
  region: string
  label: string
  mean: number
  median: number
  min: number
  max: number
  count: number
}

export interface TyphoonSummary {
  typhoon_id: string
  year: number
  name_zh: string
  name_en: string
  category: string
  landfall_location: string | null
  track_points: number
  event_rain: Record<string, number | null>
}

// -- platform: identity, audit and scheduling -------------------------------
export interface UserAccount {
  id: string
  email: string
  display_name: string
  role: string
  is_active: boolean
  permissions: string[]
  last_login_at: string | null
  created_at: string
}

export interface AuditEntry {
  id: string
  action: string
  resource_type: string
  resource_id: string | null
  actor_id: string | null
  actor_email: string | null
  detail: Record<string, unknown>
  outcome: string
  created_at: string
}

export interface ApplicationTool {
  model_id: string
  name: string
  description: string
  provider: string
  kinds: string[]
  parameter_contract: Contract
  input_contract: Contract
}

/** What `POST /models/{id}/invoke` answers: the answer, and nothing else. */
export interface InvokeAnswer {
  model_id: string
  model_version_id: string | null
  kind: string
  result_kind: string
  value: unknown
  rows: Record<string, unknown>[] | null
  row_count: number | null
  truncated: boolean
  summary: Record<string, unknown> | null
  metrics: Record<string, unknown>
  logs: string[]
  duration_seconds: number
}

export interface LineageNode {
  key: string
  kind: string
  id: string
  label: string
  detail: Record<string, unknown>
}

export interface LineageGraphData {
  root: string
  direction: 'up' | 'down'
  nodes: LineageNode[]
  edges: { from: string; to: string; relation: string }[]
  //  True when the walk stopped at the depth limit rather than at the end.
  truncated: boolean
}

export interface Schedule {
  id: string
  name: string
  description: string
  //  A schedule fires a model (one execution) or a pipeline (one job).
  target_id: string
  target_type: 'model' | 'pipeline'
  kind: string
  interval_seconds: number | null
  cron: string | null
  dataset_id: string | null
  dataset_version_id: string | null
  input_payload: Record<string, unknown>
  parameters: Record<string, unknown>
  status: string
  last_run_at: string | null
  last_execution_id: string | null
  last_status: string | null
  last_error: string | null
  next_run_at: string | null
  run_count: number
  failure_count: number
  created_at: string
}

// -- pipelines --------------------------------------------------------------
export interface PipelineStep {
  name: string
  //  What the step runs: a library model, another pipeline, or - the usual
  //  case - a provider configured inline, which carries neither id.
  model_id: string | null
  pipeline_id: string | null
  kind: string | null
  parameters: Record<string, unknown>
  input_from: string | null
  materialise: boolean
  description: string
}

export interface Pipeline {
  id: string
  name: string
  description: string
  input_dataset_id: string
  steps: PipelineStep[]
  status: string
  tags: string[]
  last_run_id: string | null
  last_run_status: string | null
  created_at: string
  updated_at: string
}

export interface StepRun {
  step_name: string
  model_id: string
  order: number
  status: string
  execution_id: string | null
  //  The run this step delegated to, when the step is another pipeline.
  pipeline_run_id: string | null
  result_id: string | null
  dataset_id: string | null
  dataset_version_id: string | null
  row_count: number | null
  metrics: Record<string, unknown>
  error: string | null
  duration_seconds: number | null
}

export interface PipelineRun {
  id: string
  pipeline_id: string
  status: string
  input_dataset_version_id: string | null
  step_runs: StepRun[]
  output_dataset_ids: string[]
  error: string | null
  duration_seconds: number | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

// -- reports ----------------------------------------------------------------
export interface ReportSection {
  kind: string
  title: string
  body: string
  execution_id: string | null
  result_id: string | null
  dataset_version_id: string | null
  visualization_id: string | null
  model_id: string | null
  options: Record<string, unknown>
}

export interface Report {
  id: string
  name: string
  description: string
  sections: ReportSection[]
  status: string
  tags: string[]
  last_export_uri: string | null
  last_export_format: string | null
  last_exported_at: string | null
  created_at: string
  updated_at: string
}

export interface RenderedReport {
  id: string
  name: string
  description: string
  status: string
  generated_at: string
  sections: Record<string, any>[]
}

/** A namespace for resources, and the people who may act in it. */
export interface Workspace {
  id: string
  name: string
  slug: string
  description: string
  is_default: boolean
  created_by: string | null
  created_at: string
  updated_at: string
}

export interface WorkspaceMember {
  id: string
  workspace_id: string
  user_id: string
  role: string
  created_at: string
}

/** Work that outlives the request that asked for it. */
export interface Job {
  id: string
  kind: string
  target_id: string
  parameters: Record<string, unknown>
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  outcome: Record<string, unknown>
  error: string | null
  attempts: number
  duration_seconds: number | null
  requested_by: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
}

/**
 * Generated from the backend's OpenAPI schema. Do not edit.
 *
 * Regenerate with `npm run types:api` while the backend is running. The
 * hand-written types in `index.ts` are checked against this by
 * `npm run types:check`, so a field renamed on the server fails there rather
 * than at runtime in front of somebody.
 */

export interface ApiKeyCreate {
  name: string
  /** Whether this key may change anything. Off by default: a key usually exists to call a model, and most never need more. */
  can_write?: boolean
  expires_in_days?: number | null
}

export interface ApiKeyIssued {
  id: string
  name: string
  workspace_id: string
  hint: string
  can_write: boolean
  is_active: boolean
  created_by: string | null
  last_used_at: string | null
  expires_at: string | null
  revoked_at: string | null
  created_at: string
  secret: string
}

export interface ApiKeyOut {
  id: string
  name: string
  workspace_id: string
  hint: string
  can_write: boolean
  is_active: boolean
  created_by: string | null
  last_used_at: string | null
  expires_at: string | null
  revoked_at: string | null
  created_at: string
}

export interface ApplicationCreate {
  name: string
  description?: string
  kind?: string
  model_ids?: string[]
  dataset_ids?: string[]
  dashboard_ids?: string[]
  configuration?: Record<string, unknown>
  entrypoint?: string | null
}

export interface ApplicationOut {
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
  visibility?: string
  is_shared?: boolean
  created_at: string
  updated_at: string
}

export interface ApplicationUpdate {
  description?: string | null
  status?: string | null
  model_ids?: string[] | null
  dataset_ids?: string[] | null
  dashboard_ids?: string[] | null
  configuration?: Record<string, unknown> | null
  entrypoint?: string | null
}

export interface AuditOut {
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

export interface Body_upload_dataset_api_v1_datasets_upload_post {
  file: string
  name: string
  description?: string
}

export interface CompareIn {
  experiment_ids: string[]
  metric?: string | null
  include_history?: boolean
}

export interface DashboardCreate {
  name: string
  description?: string
  tiles?: Record<string, unknown>[]
}

export interface DashboardOut {
  id: string
  name: string
  description: string
  tiles: Record<string, unknown>[]
  created_at: string
}

export interface DashboardUpdate {
  name?: string | null
  description?: string | null
  tiles?: Record<string, unknown>[] | null
}

export interface DatasetCreate {
  name: string
  source_id: string
  description?: string
  options?: Record<string, unknown>
  tags?: string[]
}

export interface DatasetDetailOut {
  id: string
  name: string
  origin: string
  source_id: string | null
  description: string
  tags: string[]
  current_version_id: string | null
  created_at: string
  updated_at: string
  versions?: DatasetVersionOut[]
  schema_fields?: Record<string, unknown>[]
}

export interface DatasetOut {
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

export interface DatasetVersionOut {
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

export interface EvaluationCreate {
  execution_id: string
  metrics: Record<string, unknown>
  target?: Record<string, unknown>
  model_id?: string | null
  experiment_id?: string | null
  notes?: string
}

export interface EvaluationOut {
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

export interface ExecutionCreate {
  model_id: string
  /** training | prediction | simulation | optimization | calculation | evaluation | transformation */
  kind?: string | null
  dataset_id?: string | null
  dataset_version_id?: string | null
  input?: Record<string, unknown>
  parameters?: Record<string, unknown>
  context?: Record<string, unknown>
  model_version_id?: string | null
  experiment_id?: string | null
}

export interface ExecutionOut {
  id: string
  model_id: string | null
  definition_snapshot?: Record<string, unknown>
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

export interface ExperimentCreate {
  name: string
  description?: string
  objective?: string
  primary_metric?: string
  dataset_version_id?: string | null
  trials?: TrialIn[] | null
  model_ids?: string[]
}

export interface ExperimentOut {
  id: string
  name: string
  description: string
  objective: string
  primary_metric: string
  dataset_version_id: string | null
  trials: TrialOut[]
  model_ids: string[]
  execution_ids: string[]
  created_at: string
}

export interface ExperimentUpdate {
  name?: string | null
  description?: string | null
  objective?: string | null
  primary_metric?: string | null
  dataset_version_id?: string | null
  trials?: TrialIn[] | null
}

export interface HTTPValidationError {
  detail?: ValidationError[]
}

export interface InvokeIn {
  kind?: string | null
  input?: Record<string, unknown>
  parameters?: Record<string, unknown>
  dataset_version_id?: string | null
}

export interface JobOut {
  id: string
  kind: string
  target_id: string
  parameters: Record<string, unknown>
  status: string
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

export interface LeaderboardOut {
  experiment_id: string
  name: string
  objective: string
  primary_metric: string
  metric_names: string[]
  rows: LeaderboardRow[]
}

export interface LeaderboardRow {
  trial: string
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

export interface LoginRequest {
  email: string
  password: string
}

export interface MaterialiseRequest {
  dataset_name: string
}

export interface MemberIn {
  user_id: string
  role?: string
}

export interface MemberOut {
  id: string
  workspace_id: string
  user_id: string
  role: string
  created_at: string
}

export interface ModelCapabilities {
  executable: boolean
  execution_kinds: string[]
  trainable: boolean
  versionable: boolean
  configurable: boolean
  open_input: boolean
  open_output: boolean
}

export interface ModelCreate {
  name: string
  provider: string
  description?: string
  type?: string | null
  runtime?: string | null
  configuration?: Record<string, unknown>
  input_contract?: Record<string, unknown> | null
  parameter_contract?: Record<string, unknown> | null
  output_contract?: Record<string, unknown> | null
  tags?: string[]
  metadata?: Record<string, unknown>
}

export interface ModelOut {
  id: string
  name: string
  slug: string
  description: string
  type: string
  status: string
  has_unpublished_changes: boolean
  capabilities: ModelCapabilities
  provider: string
  runtime: string
  trainable: boolean
  tags: string[]
  configuration: Record<string, unknown>
  input_contract: Record<string, unknown>
  parameter_contract: Record<string, unknown>
  output_contract: Record<string, unknown>
  metadata: Record<string, unknown>
  current_version_id: string | null
  created_by?: string | null
  created_at: string
  updated_at: string
}

export interface ModelStatusIn {
  status: string
}

export interface ModelUpdate {
  description?: string | null
  tags?: string[] | null
  configuration?: Record<string, unknown> | null
  input_contract?: Record<string, unknown> | null
  parameter_contract?: Record<string, unknown> | null
  output_contract?: Record<string, unknown> | null
}

export interface ModelVersionOut {
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

export interface PasswordChange {
  current_password: string
  new_password: string
}

export interface PipelineCreate {
  name: string
  input_dataset_id: string
  steps: StepIn[]
  description?: string
  tags?: string[]
}

export interface PipelineOut {
  id: string
  name: string
  description: string
  input_dataset_id: string
  steps: Record<string, unknown>[]
  status: string
  tags: string[]
  last_run_id: string | null
  last_run_status: string | null
  created_at: string
  updated_at: string
}

export interface PipelineRunOut {
  id: string
  pipeline_id: string
  status: string
  input_dataset_version_id: string | null
  step_runs: Record<string, unknown>[]
  output_dataset_ids: string[]
  error: string | null
  duration_seconds: number | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export interface PipelineUpdate {
  description?: string | null
  tags?: string[] | null
  input_dataset_id?: string | null
  steps?: StepIn[] | null
  status?: string | null
}

export interface PrecipRequest {
  track: TrackPoint[]
  frames?: number
  bandwidth_km?: number
  thresholds?: number[] | null
  use_wind?: boolean
}

export interface PredictRequest {
  track?: TrackPoint[]
  typhoon_id?: string | null
  method?: string
  k?: number
  buffer_km?: number
  use_rainfall?: boolean
  rainfall_region?: string
  rainfall_weight?: number | null
  expected_rainfall?: number | null
}

export interface PreviewOut {
  columns: Record<string, unknown>[]
  rows: Record<string, unknown>[]
  row_count?: number
  version_id?: string | null
  version?: number | null
}

export interface PreviewRequest {
  interval_seconds?: number | null
  cron?: string | null
  count?: number
}

export interface QueryRequest {
  columns?: string[] | null
  filters?: Record<string, unknown>[]
  sort_by?: string | null
  sort_desc?: boolean
  limit?: number
  offset?: number
}

export interface ReportCreate {
  name: string
  description?: string
  sections?: SectionIn[]
  tags?: string[]
}

export interface ReportOut {
  id: string
  name: string
  description: string
  sections: Record<string, unknown>[]
  status: string
  tags: string[]
  last_export_uri: string | null
  last_export_format: string | null
  last_exported_at: string | null
  created_at: string
  updated_at: string
}

export interface ReportUpdate {
  name?: string | null
  description?: string | null
  sections?: SectionIn[] | null
  tags?: string[] | null
  status?: string | null
}

export interface ResultOut {
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

export interface RunRequest {
  dataset_version_id?: string | null
}

export interface ScheduleCreate {
  name: string
  model_id: string
  kind?: string
  interval_seconds?: number | null
  cron?: string | null
  dataset_id?: string | null
  dataset_version_id?: string | null
  input?: Record<string, unknown>
  parameters?: Record<string, unknown>
  description?: string
}

export interface ScheduleOut {
  id: string
  name: string
  description: string
  model_id: string
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

export interface ScheduleUpdate {
  description?: string | null
  kind?: string | null
  interval_seconds?: number | null
  cron?: string | null
  dataset_id?: string | null
  dataset_version_id?: string | null
  input_payload?: Record<string, unknown> | null
  parameters?: Record<string, unknown> | null
  status?: string | null
}

export interface SchemaOut {
  id: string
  name: string
  description: string
  fields: Record<string, unknown>[]
  created_at: string
}

export interface SectionIn {
  kind: string
  title?: string
  body?: string
  execution_id?: string | null
  result_id?: string | null
  dataset_version_id?: string | null
  visualization_id?: string | null
  model_id?: string | null
  options?: Record<string, unknown>
}

export interface ShareOut {
  share_url: string
  token: string
  visibility: string
  shared_at: string | null
}

export interface SourceCreate {
  name: string
  type: string
  connection?: Record<string, unknown>
  description?: string
}

export interface SourceOut {
  id: string
  name: string
  type: string
  connection: Record<string, unknown>
  description: string
  created_at: string
}

export interface StepIn {
  name: string
  model_id?: string | null
  /** run this provider with the configuration below */
  provider?: string | null
  configuration?: Record<string, unknown>
  kind?: string | null
  parameters?: Record<string, unknown>
  /** a previous step's name; omit to read the pipeline's input dataset */
  input_from?: string | null
  /** extra inputs wired by name to an earlier step, e.g. {"right": "load prices"}. A step with these is where the graph merges. */
  inputs?: Record<string, string>
  /** extra inputs that are datasets, e.g. {"right": "ds_..."} - for joining against a reference table the pipeline does not derive. */
  input_datasets?: Record<string, string>
  /** keep this step's output as a Dataset of its own. Off by default: a step in the middle of a run is working state, and publishing every one of them is what fills the catalogue with noise. */
  materialise?: boolean
  description?: string
}

export interface TileCreate {
  visualization_id: string
  width?: number
  height?: number
}

export interface TileUpdate {
  width?: number | null
  height?: number | null
  move?: number | null
}

export interface TokenOut {
  access_token: string
  token_type: string
  expires_in: number
  user: UserOut
}

export interface TrackPoint {
  latitude: number
  longitude: number
  wind_kt?: number | null
  pressure_mb?: number | null
  timestamp_utc?: string | null
}

export interface TrialIn {
  model_id: string
  label?: string
  parameters?: Record<string, unknown>
  model_version_id?: string | null
  kind?: string | null
}

export interface TrialOut {
  model_id: string
  label: string
  parameters: Record<string, unknown>
  model_version_id: string | null
  kind: string | null
}

export interface UserCreate {
  email: string
  password: string
  role?: string
  display_name?: string
}

export interface UserOut {
  id: string
  email: string
  display_name: string
  role: string
  is_active: boolean
  permissions: string[]
  last_login_at: string | null
  created_at: string
}

export interface UserUpdate {
  display_name?: string | null
  role?: string | null
  is_active?: boolean | null
  password?: string | null
}

export interface ValidationError {
  loc: (string | number)[]
  msg: string
  type: string
  input?: unknown
  ctx?: Record<string, unknown>
}

export interface VisualizationCreate {
  name: string
  spec: Record<string, unknown>
  dataset_version_id?: string | null
  dataset_id?: string | null
  result_id?: string | null
  description?: string
}

export interface VisualizationOut {
  id: string
  name: string
  description: string
  spec: Record<string, unknown>
  dataset_id: string | null
  dataset_version_id: string | null
  result_id: string | null
  created_at: string
}

export interface VisualizationUpdate {
  name?: string | null
  description?: string | null
  spec?: Record<string, unknown> | null
}

export interface WorkspaceCreate {
  name: string
  description?: string
}

export interface WorkspaceOut {
  id: string
  name: string
  slug: string
  description: string
  is_default: boolean
  created_by: string | null
  created_at: string
  updated_at: string
}

export interface WorkspaceUpdate {
  name?: string | null
  description?: string | null
}

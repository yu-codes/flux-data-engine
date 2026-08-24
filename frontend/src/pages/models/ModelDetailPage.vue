<template>
  <q-page v-if="model" class="page-shell">
    <PageHeader :title="model.name" :subtitle="model.description">
      <template #actions>
        <q-btn
          flat
          dense
          no-caps
          icon="fact_check"
          label="Validate"
          :loading="validating"
          @click="validate"
        />
        <q-btn
          color="primary"
          unelevated
          no-caps
          icon="play_arrow"
          label="Run"
          class="fx-btn"
          :disable="!model.capabilities.executable"
          @click="openRun"
        >
          <q-tooltip v-if="!model.capabilities.executable">
            This provider declares no execution kinds
          </q-tooltip>
        </q-btn>
        <q-btn-dropdown flat dense round no-caps>
          <q-list dense>
            <q-item clickable v-close-popup @click="toggleStatus">
              <q-item-section avatar>
                <q-icon :name="model.status === 'active' ? 'archive' : 'unarchive'" size="20px" />
              </q-item-section>
              <q-item-section>
                {{ model.status === 'active' ? 'Deprecate' : 'Reactivate' }}
                <q-item-label caption class="fx-meta">
                  {{
                    model.status === 'active'
                      ? 'Keeps running; stops being offered for new work'
                      : 'Offer it in pickers again'
                  }}
                </q-item-label>
              </q-item-section>
            </q-item>
          </q-list>
        </q-btn-dropdown>
      </template>
    </PageHeader>

    <!--
      Which definition is about to run.

      The model row is what you edit; the current version is what executes.
      Once those differ, pressing Run does something other than what the
      configuration on screen describes — so the difference is stated, with the
      action that resolves it attached.
    -->
    <SectionCard
      v-if="model.has_unpublished_changes"
      class="q-mb-md"
      title="Unpublished changes"
      subtitle="Runs still use the current version. Publish to make these changes execute."
      icon="edit_note"
      tight
    >
      <template #actions>
        <q-btn
          no-caps
          unelevated
          color="primary"
          icon="add"
          label="Publish"
          class="fx-btn"
          :loading="publishing"
          @click="publish"
        />
      </template>
      <p class="fx-meta q-mb-none">
        The working definition has been edited since version
        {{ currentVersionNumber ?? '—' }} was published.
      </p>
    </SectionCard>

    <!--
      Validation output stays until it is superseded. Errors are the reason to
      run a check, and a notification is the wrong container for something you
      need to read while editing the thing it describes.
    -->
    <SectionCard
      v-if="validation"
      class="q-mb-md"
      :title="validation.valid ? 'Definition is valid' : 'Definition has problems'"
      :subtitle="validationSubtitle"
      :icon="validation.valid ? 'check_circle' : 'error_outline'"
      tight
    >
      <template #actions>
        <q-btn flat dense round icon="close" @click="validation = null">
          <q-tooltip>Dismiss</q-tooltip>
        </q-btn>
      </template>
      <ul v-if="validation.errors.length || validation.warnings.length" class="check">
        <li v-for="message in validation.errors" :key="`e-${message}`" class="check__error">
          {{ message }}
        </li>
        <li v-for="message in validation.warnings" :key="`w-${message}`" class="check__warning">
          {{ message }}
        </li>
      </ul>
      <p v-else class="fx-meta q-mb-none">
        The configuration matches this provider's contract, and every expression
        it contains parses.
      </p>
    </SectionCard>

    <!--
      Identity answers "what is this and what can it do", in that order.

      Capabilities come first because they are what a reader can act on; the
      provider and runtime are implementation and sit under them. A category
      name alone ("statistical") tells nobody whether the thing trains, what it
      accepts, or how it can be run.
    -->
    <SectionCard class="q-mb-md" tight plain>
      <div class="row q-col-gutter-lg">
        <div class="col-12 col-md-6">
          <FactList :facts="capabilityFacts" />
        </div>
        <div class="col-12 col-md-6">
          <div class="flow">
            <span class="flow__node">Input</span>
            <span class="flow__arrow">→</span>
            <span class="flow__node">Validate against contract</span>
            <span class="flow__arrow">→</span>
            <span class="flow__node">{{ model.provider }}</span>
            <span class="flow__arrow">→</span>
            <span class="flow__node">Result</span>
          </div>
          <div class="fx-meta q-mt-sm">
            {{ label(model.type) }} · {{ model.provider }} on {{ model.runtime }}
          </div>
        </div>
      </div>
    </SectionCard>

    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-7">
        <SectionCard title="Contracts" subtitle="What this model accepts and what it returns" class="q-mb-md">
          <template #actions>
            <q-btn-toggle
              v-model="contractTab"
              dense
              unelevated
              no-caps
              toggle-color="primary"
              :options="[
                { label: 'Input', value: 'input' },
                { label: 'Parameters', value: 'parameter' },
                { label: 'Output', value: 'output' },
              ]"
            />
          </template>

          <p class="fx-meta q-mb-md">
            {{ activeContract.description || `Shape: ${activeContract.shape}` }}
          </p>

          <q-markup-table v-if="activeContract.fields.length" flat dense class="scroll-x">
            <thead>
              <tr>
                <th class="text-left">Field</th>
                <th class="text-left">Type</th>
                <th class="text-left">Required</th>
                <th class="text-left">Notes</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="field in activeContract.fields" :key="field.name">
                <td class="mono">{{ field.name }}</td>
                <td>
                  {{ field.type }}<span v-if="field.unit" class="fx-meta"> ({{ field.unit }})</span>
                </td>
                <td>{{ field.required ? 'yes' : 'no' }}</td>
                <td class="fx-meta">
                  {{ field.description }}
                  <template v-if="field.default !== null && field.default !== undefined">
                    <span class="fx-meta__sep">·</span>default {{ field.default }}
                  </template>
                  <template v-if="field.enum?.length">
                    <span class="fx-meta__sep">·</span>one of {{ field.enum.join(', ') }}
                  </template>
                </td>
              </tr>
            </tbody>
          </q-markup-table>
          <div v-else class="fx-meta">
            This contract is open — the provider validates the payload itself.
          </div>
        </SectionCard>

        <!--
          Parameters are what changes between runs; configuration is what the
          model is. Splitting them is what lets somebody see, in one place, the
          knobs they are allowed to turn.
        -->
        <SectionCard
          title="Parameters"
          :subtitle="
            parameterFields.length
              ? `${parameterFields.length} you can set per run`
              : 'This model takes no per-run parameters'
          "
        >
          <div v-if="parameterFields.length" class="fx-scroll-x">
            <table class="fx-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Required</th>
                  <th>Default</th>
                  <th>What it does</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="field in parameterFields" :key="field.name">
                  <td class="mono">{{ field.name }}</td>
                  <td>{{ field.type }}</td>
                  <td>{{ field.required ? 'yes' : 'no' }}</td>
                  <td class="mono">{{ field.default === null ? '—' : String(field.default) }}</td>
                  <td class="wrap">
                    {{ field.description || '—' }}
                    <span v-if="field.enum && field.enum.length" class="fx-meta">
                      one of {{ field.enum.join(', ') }}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="fx-meta q-mb-none">
            Everything it needs is fixed in its configuration below.
          </p>
        </SectionCard>

        <SectionCard
          title="Configuration"
          subtitle="Frozen into every version of this model"
          class="q-mt-md"
        >
          <JsonBlock :value="model.configuration" />
        </SectionCard>
      </div>

      <div class="col-12 col-md-5">
        <SectionCard
          title="Versions"
          subtitle="Immutable. Changing behaviour publishes the next one."
          class="q-mb-md"
          flush
        >
          <template #actions>
            <q-btn
              flat
              dense
              no-caps
              icon="add"
              label="Publish"
              :loading="publishing"
              @click="publish"
            />
          </template>
          <q-list separator class="fx-list">
            <q-item v-for="version in versions" :key="version.id">
              <q-item-section>
                <q-item-label>
                  v{{ version.version }}
                  <span v-if="version.id === model.current_version_id" class="fx-tag"> · current</span>
                </q-item-label>
                <q-item-label caption class="fx-meta">
                  {{ version.notes || 'no notes' }}
                  <template v-if="version.artifact_uri">
                    <span class="fx-meta__sep">·</span>artifact stored
                  </template>
                </q-item-label>
                <q-item-label v-if="metricsLine(version.metrics)" caption class="fx-meta mono">
                  {{ metricsLine(version.metrics) }}
                </q-item-label>
              </q-item-section>
            </q-item>
          </q-list>
        </SectionCard>

        <!--
          What to send it, in the shape the API expects. A contract table tells
          you the field names; it does not tell you what a request looks like,
          and that is the thing somebody is actually looking for.
        -->
        <SectionCard title="How to run it" subtitle="A request in the shape this model accepts">
          <div class="usage">
            <div class="usage__step">
              <div class="usage__label">1 · From this page</div>
              <div class="fx-meta">
                Press Run and choose an execution kind and an input.
                <template v-if="parameterFields.length">
                  The parameters listed opposite are rendered as a form.
                </template>
              </div>
            </div>
            <div class="usage__step">
              <div class="usage__label">2 · In an experiment</div>
              <div class="fx-meta">
                Add it as a trial to compare it against other models on one dataset.
                Parameters and dataset are checked before anything runs.
              </div>
            </div>
            <div class="usage__step">
              <div class="usage__label">3 · Over the API</div>
              <pre class="usage__code">{{ exampleRequest }}</pre>
            </div>
          </div>
        </SectionCard>
      </div>
    </div>

    <!-- run -->
    <q-dialog v-model="runDialog">
      <q-card style="min-width: 580px">
        <q-card-section class="fx-dialog__title">Run “{{ model.name }}”</q-card-section>
        <q-card-section class="q-pt-none">
          <div class="fx-form">
            <q-select v-model="run.kind" :options="supportedKinds" label="Execution kind" dense outlined />
            <q-select
              v-model="run.dataset_id"
              :options="datasetOptions"
              label="Input dataset"
              dense
              outlined
              clearable
              emit-value
              map-options
              hint="leave empty to pass an inline input instead"
            />
            <q-input
              v-model="inputText"
              label="Inline input (JSON)"
              type="textarea"
              rows="3"
              dense
              outlined
              class="mono"
              :hint="inputHint"
            />
            <q-input
              v-model="parametersText"
              label="Parameters (JSON)"
              type="textarea"
              rows="5"
              dense
              outlined
              class="mono"
            />
          </div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat no-caps label="Cancel" v-close-popup />
          <q-btn unelevated no-caps color="primary" label="Execute" :loading="running" @click="execute" />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { data as dataApi, executions as executionsApi, models as modelsApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import FactList, { type Fact } from '@/components/FactList.vue'
import JsonBlock from '@/components/JsonBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import StatusText from '@/components/StatusText.vue'
import type { Contract, Dataset, Execution, ModelDefinition, ModelVersion, FieldSpec } from '@/types'

const $q = useQuasar()
const route = useRoute()
const router = useRouter()

const model = ref<ModelDefinition | null>(null)
const versions = ref<ModelVersion[]>([])
const datasets = ref<Dataset[]>([])
const contractTab = ref<'input' | 'parameter' | 'output'>('input')
const runDialog = ref(false)
const running = ref(false)
const validating = ref(false)
const publishing = ref(false)
const validation = ref<{ valid: boolean; errors: string[]; warnings: string[] } | null>(null)

const currentVersionNumber = computed(
  () => versions.value.find((v) => v.id === model.value?.current_version_id)?.version ?? null,
)

/**
 * What this model can do, phrased for someone who does not know the category.
 * Read from capabilities so a provider added tomorrow needs no branch here.
 */
const capabilityFacts = computed<Fact[]>(() => {
  const can = model.value?.capabilities
  if (!can) return []
  return [
    { label: 'Runs as', value: can.execution_kinds.join(', ') || 'not executable' },
    { label: 'Needs training', value: can.trainable ? 'yes, before it can predict' : 'no' },
    { label: 'Configurable', value: can.configurable ? 'yes' : 'no parameters' },
    {
      label: 'Input',
      value: can.open_input ? 'validated by the provider' : 'declared field set',
    },
    {
      label: 'Output',
      value: can.open_output ? 'validated by the provider' : 'declared field set',
    },
  ]
})

const parameterFields = computed(() => model.value?.parameter_contract?.fields ?? [])

/**
 * A worked request, built from this model's own contract.
 *
 * Generated rather than written: a hand-kept example goes stale the moment a
 * provider changes its parameters, and a stale example is worse than none.
 */
const exampleRequest = computed(() => {
  const current = model.value
  if (!current) return ''
  const body: Record<string, unknown> = {
    model_id: current.id,
    kind: current.capabilities.execution_kinds[0] ?? 'calculation',
  }
  if (current.input_contract.fields.length) {
    body.input = Object.fromEntries(
      current.input_contract.fields.slice(0, 3).map((field) => [field.name, sample(field)]),
    )
  } else {
    body.dataset_version_id = 'dsv_…'
  }
  const required = parameterFields.value.filter((field) => field.required)
  if (required.length) {
    body.parameters = Object.fromEntries(required.map((field) => [field.name, sample(field)]))
  }
  return `POST /api/v1/executions\n${JSON.stringify(body, null, 2)}`
})

function sample(field: FieldSpec): unknown {
  if (field.default !== null && field.default !== undefined) return field.default
  if (field.enum && field.enum.length) return field.enum[0]
  switch (field.type) {
    case 'integer':
      return 1
    case 'float':
      return 1.0
    case 'boolean':
      return true
    case 'array':
      return []
    case 'json':
      return {}
    default:
      return `<${field.name}>`
  }
}

const validationSubtitle = computed(() => {
  const check = validation.value
  if (!check) return undefined
  const parts: string[] = []
  if (check.errors.length) parts.push(`${check.errors.length} error${check.errors.length > 1 ? 's' : ''}`)
  if (check.warnings.length) {
    parts.push(`${check.warnings.length} warning${check.warnings.length > 1 ? 's' : ''}`)
  }
  return parts.join(' · ') || 'Checked against the provider contract'
})

const run = ref<{ kind: string; dataset_id: string | null }>({
  kind: 'calculation',
  dataset_id: null,
})
const inputText = ref('')
const parametersText = ref('{}')

const activeContract = computed<Contract>(() => {
  const target = model.value
  if (!target) return { shape: 'object', description: '', fields: [] }
  return {
    input: target.input_contract,
    parameter: target.parameter_contract,
    output: target.output_contract,
  }[contractTab.value]
})

const supportedKinds = computed(
  () => (model.value?.metadata.supported_kinds as string[]) ?? ['calculation'],
)
const datasetOptions = computed(() => datasets.value.map((d) => ({ label: d.name, value: d.id })))

const inputHint = computed(() =>
  model.value?.input_contract.description
    ? model.value.input_contract.description.slice(0, 120)
    : 'e.g. {"price": 10, "quantity": 3} or {"rows": [...]}',
)

function label(type: string) {
  return type.replace(/_/g, ' ').replace(/\b\w/g, (character) => character.toUpperCase())
}

function metricsLine(metrics: Record<string, unknown>) {
  return Object.entries(metrics)
    .filter(([, value]) => typeof value !== 'object')
    .slice(0, 4)
    .map(([key, value]) => `${key}=${value}`)
    .join('  ')
}

/**
 * Other pages link here with `?run=1` rather than duplicating this dialog.
 *
 * Configuring a run means choosing an execution kind, an input and the model's
 * own parameters — a second copy of that on the Executions page would be a
 * second thing to keep correct for no gain.
 */
function openRunFromQuery() {
  if (route.query.run) {
    openRun()
    void router.replace({ query: {} })
  }
}

function openRun() {
  run.value.kind = supportedKinds.value[0] ?? 'calculation'
  runDialog.value = true
}

async function load() {
  const id = String(route.params.id)
  try {
    model.value = await modelsApi.get(id)
    run.value.kind = supportedKinds.value[0] ?? 'calculation'
    parametersText.value = JSON.stringify(model.value.configuration ?? {}, null, 2)
    //  Runs are no longer fetched here: this page describes what the model is
    //  and how to use it. Its executions are on the Executions page, filtered.
    const [versionList, datasetList] = await Promise.all([
      modelsApi.versions(model.value.id),
      dataApi.listDatasets('?include=all'),
    ])
    versions.value = versionList
    datasets.value = datasetList
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

/**
 * Validation writes to the page, not to a toast.
 *
 * A toast that says "Definition is valid" and then disappears leaves the screen
 * exactly as it was, which is indistinguishable from the button doing nothing.
 * Worse, when validation fails the errors are the reason to open the page at
 * all, and they were being shown for four seconds in a corner.
 */
async function validate() {
  if (!model.value) return
  validating.value = true
  try {
    validation.value = await modelsApi.validate(model.value.id)
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    validating.value = false
  }
}

async function toggleStatus() {
  if (!model.value) return
  const next = model.value.status === 'active' ? 'deprecated' : 'active'
  try {
    await modelsApi.setStatus(model.value.id, next)
    await load()
    $q.notify({
      type: 'positive',
      message: next === 'deprecated' ? 'Deprecated — existing runs are unaffected' : 'Reactivated',
    })
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

async function publish() {
  if (!model.value) return
  publishing.value = true
  try {
    const version = await modelsApi.publishVersion(model.value.id, 'published from the UI')
    await load()
    //  Name the version that now exists: "New version published" left a person
    //  to go and check which one, and whether the list had refreshed at all.
    $q.notify({ type: 'positive', message: `Published version ${version.version}` })
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    publishing.value = false
  }
}

async function execute() {
  if (!model.value) return
  running.value = true
  try {
    const body: Record<string, unknown> = {
      model_id: model.value.id,
      kind: run.value.kind,
      parameters: JSON.parse(parametersText.value || '{}'),
    }
    if (run.value.dataset_id) body.dataset_id = run.value.dataset_id
    if (inputText.value.trim()) body.input = JSON.parse(inputText.value)

    const execution = await executionsApi.submit(body)
    runDialog.value = false
    $q.notify({ type: 'positive', message: `Execution ${execution.status}` })
    await router.push({ name: 'execution-detail', params: { id: execution.id } })
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    running.value = false
  }
}

onMounted(async () => {
  await load()
  openRunFromQuery()
})
</script>

<style scoped>
.usage {
  display: grid;
  gap: var(--fx-space-4);
}

.usage__label {
  font-size: var(--fx-text-xs);
  font-weight: 600;
  letter-spacing: 0.03em;
  margin-bottom: 2px;
}

.usage__code {
  margin: var(--fx-space-2) 0 0;
  padding: var(--fx-space-3);
  background: var(--fx-surface-muted);
  border-radius: var(--fx-radius-sm);
  font-family: var(--fx-mono);
  font-size: var(--fx-text-xs);
  overflow-x: auto;
  white-space: pre;
}

.check {
  margin: 0;
  padding-left: var(--fx-space-5);
  display: grid;
  gap: var(--fx-space-2);
  font-size: var(--fx-text-sm);
}

.check__error {
  color: var(--fx-bad);
}

.check__warning {
  color: var(--fx-wait);
}
</style>

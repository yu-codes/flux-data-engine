<template>
  <q-page v-if="execution" class="page-shell">
    <PageHeader :title="`${execution.kind} execution`" :subtitle="targetName">
      <template #actions>
        <StatusText :status="execution.status" class="q-mr-sm" />
        <!--
          A run is a run of a runnable, and the button has to lead where the
          run actually came from: sending a pipeline run to a model page that
          does not exist was the old shape of this button.
        -->
        <q-btn
          v-if="targetRoute"
          flat
          dense
          no-caps
          :icon="isPipeline ? 'account_tree' : 'category'"
          :label="isPipeline ? 'Pipeline' : 'Model'"
          :to="targetRoute"
        />
      </template>
    </PageHeader>

    <!-- provenance, as a line you can read left to right -->
    <SectionCard class="q-mb-md" tight plain>
      <div class="flow">
        <span class="flow__node">{{ inputLabel }}</span>
        <span class="flow__arrow">→</span>
        <span class="flow__node">{{ targetName }}</span>
        <span class="flow__arrow">→</span>
        <span class="flow__node">{{ execution.kind }}</span>
        <span class="flow__arrow">→</span>
        <span class="flow__node">{{ result ? result.kind : 'no result' }}</span>
      </div>
    </SectionCard>

    <q-banner v-if="execution.error" dense class="banner-error q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      {{ execution.error }}
    </q-banner>

    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-5">
        <SectionCard title="Metrics" subtitle="What this run measured" class="q-mb-md">
          <FactList v-if="metricFacts.length" :facts="metricFacts" />
          <div v-else class="fx-meta">This kind of run reports no metrics.</div>
        </SectionCard>

        <SectionCard title="Run" class="q-mb-md">
          <FactList :facts="runFacts" />
        </SectionCard>

        <SectionCard v-if="parameterFacts.length" title="Parameters" class="q-mb-md">
          <FactList :facts="parameterFacts" />
        </SectionCard>

        <SectionCard v-if="lineageFacts.length" title="Lineage" class="q-mb-md">
          <FactList :facts="lineageFacts" />
        </SectionCard>

        <SectionCard v-if="execution.logs.length" title="Logs" tight>
          <pre class="log-block mono">{{ execution.logs.join('\n') }}</pre>
        </SectionCard>
      </div>

      <div class="col-12 col-md-7">
        <SectionCard
          title="Result"
          :subtitle="result ? `${result.kind}${result.row_count ? ` · ${result.row_count.toLocaleString()} rows` : ''}` : undefined"
          :flush="tableRows.length > 0"
        >
          <template v-if="result" #actions>
            <q-btn
              v-if="result.dataset_id"
              flat
              dense
              no-caps
              icon="table_chart"
              label="Dataset"
              :to="{ name: 'dataset-detail', params: { id: result.dataset_id } }"
            />
            <q-btn
              flat
              dense
              no-caps
              icon="output"
              label="Result"
              :to="{ name: 'results' }"
            />
          </template>

          <DataTable v-if="tableRows.length" :rows="tableRows" :rows-per-page="20" />
          <JsonBlock v-else-if="payload !== null" :value="payload" />
          <EmptyState v-else message="This run produced no result payload" icon="output" />
        </SectionCard>
      </div>
    </div>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import {
  executions as executionsApi,
  models as modelsApi,
  pipelines as pipelinesApi,
  results as resultsApi,
} from '@/api'
import DataTable from '@/components/DataTable.vue'
import EmptyState from '@/components/EmptyState.vue'
import FactList, { type Fact } from '@/components/FactList.vue'
import JsonBlock from '@/components/JsonBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import StatusText from '@/components/StatusText.vue'
import type { Execution, ResultRecord } from '@/types'

const $q = useQuasar()
const route = useRoute()

const execution = ref<Execution | null>(null)
const result = ref<ResultRecord | null>(null)
const payload = ref<unknown>(null)
const targetName = ref('model')

//  Which kind of runnable this was. A pipeline run has no model to link to,
//  and linking to one anyway is how a page ends up at a 404.
const isPipeline = computed(() => execution.value?.target_type === 'pipeline')

const targetRoute = computed(() => {
  const target = execution.value?.target_id
  if (!target) return null
  return isPipeline.value
    ? { name: 'pipelines', query: { id: target } }
    : { name: 'model-detail', params: { id: target } }
})

/** Ask whatever kind of thing this ran what it is called. */
async function nameTheTarget(current: Execution) {
  if (!current.target_id) {
    targetName.value = 'inline definition'
    return
  }
  try {
    targetName.value =
      current.target_type === 'pipeline'
        ? (await pipelinesApi.get(current.target_id)).name
        : (await modelsApi.get(current.target_id)).name
  } catch {
    //  A name is context, not content: a deleted model must not empty the page.
    targetName.value = current.target_id
  }
}

const tableRows = computed(() => {
  const value = payload.value as { rows?: Record<string, unknown>[] } | null
  return value && typeof value === 'object' && Array.isArray(value.rows) ? value.rows : []
})

const inputLabel = computed(() => {
  if (!execution.value) return 'input'
  if (execution.value.dataset_version_id) return 'dataset'
  return Object.keys(execution.value.parameters ?? {}).length ? 'inline input' : 'no input'
})

const metricFacts = computed<Fact[]>(() =>
  Object.entries(execution.value?.metrics ?? {}).map(([label, value]) => ({ label, value })),
)

const parameterFacts = computed<Fact[]>(() =>
  Object.entries(execution.value?.parameters ?? {}).map(([label, value]) => ({ label, value })),
)

const lineageFacts = computed<Fact[]>(() =>
  Object.entries(execution.value?.lineage ?? {}).map(([label, value]) => ({ label, value })),
)

const runFacts = computed<Fact[]>(() => {
  const current = execution.value
  if (!current) return []
  return [
    { label: 'Execution', value: current.id },
    { label: 'Kind', value: current.kind },
    { label: 'Runtime', value: current.runtime },
    { label: 'Started', value: current.started_at ? new Date(current.started_at).toLocaleString() : null },
    { label: 'Duration', value: current.duration_seconds !== null ? `${current.duration_seconds}s` : null },
    { label: 'Model version', value: current.model_version_id },
    { label: 'Produced version', value: current.produced_model_version_id },
  ]
})

onMounted(async () => {
  try {
    execution.value = await executionsApi.get(String(route.params.id))
    await nameTheTarget(execution.value)
    if (execution.value.result_id) {
      result.value = await resultsApi.get(execution.value.result_id)
      payload.value = (await resultsApi.payload(execution.value.result_id)).payload
    }
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
})
</script>

<style scoped>
.log-block {
  margin: 0;
  max-height: 240px;
  overflow: auto;
  white-space: pre-wrap;
  background: var(--fx-surface-inset);
  padding: var(--fx-space-3);
  border-radius: var(--fx-radius-sm);
  font-size: var(--fx-text-xs);
  line-height: 1.55;
}

.banner-error {
  border: 1px solid rgba(179, 69, 59, 0.4);
  border-radius: var(--fx-radius-sm);
  color: #b3453b;
  background: rgba(179, 69, 59, 0.07);
}
</style>

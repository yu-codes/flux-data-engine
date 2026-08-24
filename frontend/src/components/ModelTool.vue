<template>
  <SectionCard :title="tool.name" :subtitle="tool.description">
    <div class="tool">
      <!--
        The parameters the model declares. Rendered from the contract, so a
        provider added tomorrow gets a usable form without anybody touching
        this file — the same promise the pipeline builder already keeps.
      -->
      <ContractForm
        v-if="parameterFields.length"
        v-model="parameters"
        :fields="parameterFields"
      />

      <!--
        One input, chosen. Offering a dataset picker and a JSON box at once
        asks the reader which one wins, and nothing on screen can answer that.
      -->
      <q-btn-toggle
        v-if="datasets.length"
        v-model="inputMode"
        :options="[
          { label: 'A dataset', value: 'dataset' },
          { label: 'Typed input', value: 'typed' },
        ]"
        dense
        unelevated
        no-caps
        toggle-color="primary"
        class="tool__mode"
      />

      <q-select
        v-if="inputMode === 'dataset'"
        v-model="datasetId"
        :options="datasetOptions"
        label="Run it on"
        dense
        outlined
        emit-value
        map-options
      />

      <!--
        Some models take a record rather than a table. A contract that names no
        input fields means the provider validates the payload itself, so the
        honest control is a free one.
      -->
      <q-input
        v-else
        v-model="rawInput"
        label="Input"
        type="textarea"
        autogrow
        dense
        outlined
        :hint="inputHint"
      />

      <div class="tool__actions">
        <q-btn
          no-caps
          color="primary"
          unelevated
          icon="play_arrow"
          label="Run"
          :loading="running"
          @click="run"
        />
        <span v-if="answer?.duration_seconds !== undefined" class="fx-meta">
          answered in {{ (answer.duration_seconds * 1000).toFixed(0) }} ms
        </span>
      </div>

      <q-banner v-if="error" dense class="tool__error">{{ error }}</q-banner>

      <div v-if="answer" class="tool__answer">
        <FactList v-if="summaryFacts.length" :facts="summaryFacts" />
        <DataTable
          v-if="answer.rows?.length"
          :rows="answer.rows.slice(0, 50)"
          :fields="answerColumns"
        />
        <p v-if="answer.truncated" class="fx-meta">
          showing 50 of {{ answer.row_count }} rows — submit it as an execution to
          keep the whole result
        </p>
        <pre v-if="!answer.rows?.length && answer.value !== null" class="tool__value">{{
          formatted(answer.value)
        }}</pre>
      </div>
    </div>
  </SectionCard>
</template>

<script setup lang="ts">
/**
 * One of an application's models, as something a person can actually run.
 *
 * The platform could always do this — `POST /models/{id}/invoke` answers
 * synchronously and `ContractForm` builds a form from any contract — but the
 * two had never been introduced, so running a model was something only the
 * person who built it could do, from the model library.
 */
import { computed, ref } from 'vue'

import { models as modelsApi } from '@/api'
import ContractForm from '@/components/ContractForm.vue'
import DataTable from '@/components/DataTable.vue'
import FactList, { type Fact } from '@/components/FactList.vue'
import SectionCard from '@/components/SectionCard.vue'
import type { ApplicationTool, InvokeAnswer } from '@/types'

const props = withDefaults(
  defineProps<{ tool: ApplicationTool; datasets?: { id: string; name: string }[] }>(),
  { datasets: () => [] },
)

const parameters = ref<Record<string, unknown>>({})
//  A dataset if the application bundled one, because that is the likely
//  intent; typed input when it did not, because there is nothing to pick.
const inputMode = ref<'dataset' | 'typed'>(props.datasets.length ? 'dataset' : 'typed')
const datasetId = ref<string | null>(props.datasets[0]?.id ?? null)
const rawInput = ref('')
const answer = ref<InvokeAnswer | null>(null)
const error = ref<string | null>(null)
const running = ref(false)

const parameterFields = computed(() => props.tool.parameter_contract?.fields ?? [])
const openInput = computed(() => !(props.tool.input_contract?.fields ?? []).length)
const datasetOptions = computed(() =>
  props.datasets.map((d) => ({ label: d.name, value: d.id })),
)
const inputHint = computed(() =>
  openInput.value
    ? 'JSON — an object for one record, or {"rows": [...]} for a table'
    : `expects ${(props.tool.input_contract?.fields ?? []).map((f) => f.name).join(', ')}`,
)

//  DataTable describes its columns rather than naming them, so the header can
//  say what a column holds.
const answerColumns = computed(() =>
  answer.value?.rows?.length
    ? Object.keys(answer.value.rows[0]).map((name) => ({ name }))
    : [],
)

const summaryFacts = computed<Fact[]>(() => {
  const summary = answer.value?.summary
  if (!summary) return []
  return Object.entries(summary)
    .filter(([, value]) => value !== null && typeof value !== 'object')
    .slice(0, 8)
    .map(([label, value]) => ({ label, value: String(value) }))
})

function formatted(value: unknown) {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

async function run() {
  running.value = true
  error.value = null
  answer.value = null
  try {
    //  Exactly one of the two is sent, so the API is never asked to choose.
    let input: Record<string, unknown> = {}
    if (inputMode.value === 'typed' && rawInput.value.trim()) {
      const parsed = JSON.parse(rawInput.value)
      //  A bare list is the shape people type first; accept it rather than
      //  making them wrap it.
      input = Array.isArray(parsed) ? { rows: parsed } : parsed
    }
    answer.value = await modelsApi.invoke(props.tool.model_id, {
      input,
      parameters: parameters.value,
      ...(inputMode.value === 'dataset' && datasetId.value
        ? { dataset_id: datasetId.value }
        : {}),
    })
  } catch (caught) {
    error.value =
      caught instanceof SyntaxError
        ? 'That input is not valid JSON.'
        : (caught as Error).message
  } finally {
    running.value = false
  }
}
</script>

<style scoped>
.tool {
  display: flex;
  flex-direction: column;
  gap: var(--fx-space-3);
}

.tool__mode {
  align-self: flex-start;
}

.tool__actions {
  display: flex;
  align-items: center;
  gap: var(--fx-space-3);
}

.tool__error {
  background: var(--fx-bad-soft, transparent);
  color: var(--fx-bad);
  border-radius: var(--fx-radius-sm);
}

.tool__answer {
  display: flex;
  flex-direction: column;
  gap: var(--fx-space-3);
  min-width: 0;
}

.tool__value {
  margin: 0;
  padding: var(--fx-space-3);
  background: var(--fx-surface-sunken, transparent);
  border-radius: var(--fx-radius-sm);
  font-size: var(--fx-text-sm);
  overflow-x: auto;
}
</style>

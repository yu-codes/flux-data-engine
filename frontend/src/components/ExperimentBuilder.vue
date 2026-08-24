<template>
  <div class="fx-form">
    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-6">
        <q-input v-model="draft.name" label="Name" dense outlined autofocus />
      </div>
      <div class="col-12 col-md-4">
        <q-input
          v-model="draft.primary_metric"
          label="Primary metric"
          dense
          outlined
          hint="what 'better' means — the trials are ranked on it"
        />
      </div>
      <div class="col-12 col-md-2">
        <!--
          Ranking used to assume higher was better for every metric, so an
          experiment measured in error put its worst trial at the top.
        -->
        <q-select
          v-model="draft.primary_direction"
          :options="[
            { label: 'Higher is better', value: 'higher' },
            { label: 'Lower is better', value: 'lower' },
          ]"
          label="Direction"
          dense
          outlined
          emit-value
          map-options
        />
      </div>
    </div>

    <q-input
      v-model="draft.objective"
      label="Objective"
      dense
      outlined
      hint="the question this comparison answers, e.g. maximise category accuracy"
    />

    <!--
      One dataset for the whole experiment, not one per trial: a comparison only
      means something when everything except the variable under test is held
      constant.
    -->
    <q-select
      v-model="draft.dataset_version_id"
      :options="datasetOptions"
      label="Dataset"
      dense
      outlined
      clearable
      emit-value
      map-options
      hint="every trial runs against this; leave empty for models that need no input"
    />

    <div class="builder__heading">
      <div>
        <div class="fx-card__title">Trials</div>
        <div class="fx-card__subtitle">
          Each trial is one runnable with its own parameters — a model, or a pipeline.
          Add the same one twice to compare two settings of it.
        </div>
      </div>
      <q-btn no-caps flat dense icon="add" label="Add trial" @click="addTrial" />
    </div>

    <div v-if="!draft.trials.length" class="fx-meta q-py-sm">
      No trials yet. A comparison needs at least one.
    </div>

    <q-card
      v-for="(trial, index) in draft.trials"
      :key="trial.uid"
      flat
      bordered
      class="builder__trial q-mb-sm"
    >
      <q-card-section class="q-pb-none">
        <div class="row q-col-gutter-sm items-start">
          <div class="col-auto builder__index num">{{ index + 1 }}</div>
          <div class="col-12 col-md-4">
            <!--
              Models and pipelines in one list: "which of these two ways of
              preparing the data is better" is the same question as "which of
              these two models is better", and until a trial could name a
              pipeline the platform could only ask one of them.
            -->
            <q-select
              :model-value="targetKey(trial)"
              :options="targetOptions"
              label="What to compare"
              dense
              outlined
              emit-value
              map-options
              @update:model-value="(value: string) => onTargetChange(trial, value)"
            />
          </div>
          <div class="col-12 col-md-3">
            <q-input
              v-model="trial.label"
              label="Label"
              dense
              outlined
              :placeholder="targetName(trial)"
              hint="how it appears in the comparison"
            />
          </div>
          <div class="col-8 col-md-3">
            <!--
              A pipeline runs as its steps say; there is no kind to choose.
            -->
            <q-select
              v-model="trial.kind"
              :options="kindsFor(trial)"
              label="Run as"
              dense
              outlined
              clearable
              :disable="!trial.target_id || trial.target_type === 'pipeline'"
            />
          </div>
          <div class="col-4 col-md-auto text-right">
            <q-btn flat dense round icon="close" @click="draft.trials.splice(index, 1)">
              <q-tooltip>Remove trial</q-tooltip>
            </q-btn>
          </div>
        </div>
      </q-card-section>

      <!-- Parameters come from the model's own contract, so there is nothing to type. -->
      <q-card-section v-if="parameterFields(trial).length">
        <ContractForm
          v-model="trial.parameters"
          :fields="parameterFields(trial)"
          :columns="datasetColumns"
        />
      </q-card-section>
      <q-card-section v-else-if="trial.target_id" class="q-pt-none">
        <p class="fx-meta q-mb-none">
          {{
            trial.target_type === 'pipeline'
              ? 'A pipeline runs the steps it was built with.'
              : 'This model takes no parameters.'
          }}
        </p>
      </q-card-section>
    </q-card>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { Dataset, FieldSpec, ModelDefinition, Pipeline } from '@/types'
import ContractForm from '@/components/ContractForm.vue'

/**
 * Specifying a comparison: which models, configured how, against what data.
 *
 * The parameters for each trial are rendered from that model's own parameter
 * contract, so adding a provider needs no change here — and a value that does
 * not fit is caught by the experiment check before anything executes, rather
 * than by one trial failing halfway through a run.
 */
interface TrialDraft {
  uid: number
  target_id: string
  target_type: 'model' | 'pipeline'
  label: string
  kind: string | null
  parameters: Record<string, unknown>
}

const props = defineProps<{
  models: ModelDefinition[]
  pipelines: Pipeline[]
  datasets: Dataset[]
  datasetColumns: string[]
  modelValue: {
    name: string
    objective: string
    primary_metric: string
    primary_direction: 'higher' | 'lower'
    dataset_version_id: string | null
    trials: TrialDraft[]
  }
}>()

const emit = defineEmits<{ 'update:modelValue': [typeof props.modelValue] }>()

const draft = ref(props.modelValue)
watch(draft, (value) => emit('update:modelValue', value), { deep: true })
watch(
  () => props.modelValue,
  (value) => {
    draft.value = value
  },
)

let nextUid = 1

//  One list, two kinds. The value carries the kind because the id alone does
//  not say which of the two lists it came from.
const targetOptions = computed(() => [
  ...props.models
    .filter((m) => m.capabilities.executable)
    .map((m) => ({ label: m.name, value: `model:${m.id}` })),
  ...props.pipelines.map((p) => ({ label: `${p.name} · pipeline`, value: `pipeline:${p.id}` })),
])

const datasetOptions = computed(() =>
  props.datasets
    .filter((d) => d.current_version_id)
    .map((d) => ({ label: d.name, value: d.current_version_id as string })),
)

function model(trial: TrialDraft) {
  if (trial.target_type !== 'model') return undefined
  return props.models.find((m) => m.id === trial.target_id)
}

function targetKey(trial: TrialDraft) {
  return trial.target_id ? `${trial.target_type}:${trial.target_id}` : ''
}

function targetName(trial: TrialDraft) {
  if (trial.target_type === 'pipeline') {
    return props.pipelines.find((p) => p.id === trial.target_id)?.name ?? ''
  }
  return model(trial)?.name ?? ''
}

function kindsFor(trial: TrialDraft) {
  return model(trial)?.capabilities.execution_kinds ?? []
}

function parameterFields(trial: TrialDraft): FieldSpec[] {
  return model(trial)?.parameter_contract?.fields ?? []
}

/** A different target invalidates the parameters that belonged to the old one. */
function onTargetChange(trial: TrialDraft, value: string) {
  const [kind, id] = value.split(':')
  trial.target_type = kind === 'pipeline' ? 'pipeline' : 'model'
  trial.target_id = id ?? ''
  trial.parameters = {}
  const kinds = kindsFor(trial)
  trial.kind = kinds.length === 1 ? kinds[0] : null
}

function addTrial() {
  draft.value.trials.push({
    uid: nextUid++,
    target_id: '',
    target_type: 'model',
    label: '',
    kind: null,
    parameters: {},
  })
}

defineExpose({ addTrial })
</script>

<style scoped>
.builder__heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--fx-space-4);
  margin-top: var(--fx-space-2);
}

.builder__trial {
  border-color: var(--fx-border);
  border-radius: var(--fx-radius-sm);
}

.builder__index {
  width: 24px;
  padding-top: 10px;
  text-align: center;
  opacity: var(--fx-ink-faint);
  font-size: var(--fx-text-sm);
}
</style>

<template>
  <q-page class="page-shell">
    <PageHeader
      title="Model library"
      subtitle="A Model is any versioned, executable computational unit. Machine learning is one category of provider, not the architecture."
    >
      <template #actions>
        <q-btn color="primary" unelevated no-caps icon="add" label="New model" @click="openCreate()" />
      </template>
    </PageHeader>

    <!-- providers, grouped by the category they belong to -->
    <SectionCard
      class="q-mb-md"
      title="Providers by category"
      subtitle="Click a provider to start a model from it. Only one of them trains."
      flush
    >
      <div class="providers">
        <div v-for="group in providerGroups" :key="group.type" class="providers__group">
          <div class="providers__type">{{ label(group.type) }}</div>
          <div v-if="group.providers.length" class="providers__list">
            <button
              v-for="provider in group.providers"
              :key="provider.key"
              class="provider"
              type="button"
              @click="openCreate(provider.key)"
            >
              <span class="provider__name">{{ provider.name }}</span>
              <span v-if="provider.trainable" class="provider__note">trainable</span>
            </button>
          </div>
          <div v-else class="providers__none">none yet</div>
        </div>
      </div>
    </SectionCard>

    <SectionCard title="Models" :subtitle="listSubtitle" flush>
      <template #actions>
        <SearchField v-model="term" placeholder="Search models" @update:model-value="loadModels" />
        <q-select
          v-model="typeFilter"
          :options="typeOptions"
          label="Category"
          dense
          outlined
          clearable
          emit-value
          map-options
          style="min-width: 180px"
          @update:model-value="loadModels"
        />
      </template>
      <AsyncSection
        :pending="loading && !models.length"
        :error="loadError"
        title="Could not load the model library"
        :rows="4"
        :on-retry="loadAll"
      >

      <q-list separator class="fx-list">
        <q-item
          v-for="model in models"
          :key="model.id"
          clickable
          :to="{ name: 'model-detail', params: { id: model.id } }"
        >
          <q-item-section avatar>
            <q-icon :name="typeIcon(model.type)" size="20px" class="model__icon" />
          </q-item-section>
          <q-item-section>
            <q-item-label class="truncate">{{ model.name }}</q-item-label>
            <q-item-label caption class="fx-meta truncate">{{ model.description }}</q-item-label>
          </q-item-section>
          <q-item-section side class="model__meta">
            <!--
              What it can do, then what it is. A row is scanned, so the line
              that decides whether this model is the one you want goes first.
            -->
            <div class="model__type">{{ runsAs(model) }}</div>
            <div class="fx-meta">
              {{ label(model.type) }}
              <span class="fx-meta__sep">·</span>{{ model.provider }}
              <template v-if="model.capabilities.trainable">
                <span class="fx-meta__sep">·</span>trains
              </template>
            </div>
          </q-item-section>
          <q-item-section side>
            <StatusText
              v-if="model.status === 'deprecated'"
              status="paused"
              label="deprecated"
            />
            <StatusText
              v-else-if="model.has_unpublished_changes"
              status="pending"
              label="unpublished"
            />
          </q-item-section>
          <q-item-section side>
            <!--
              Where the definition is filed, and the one gesture that changes
              it. A definition is the only thing here worth reusing across
              projects — arithmetic is not about the fleet or the typhoons —
              so the library keeps its reach while the lists stay legible.
            -->
            <q-btn
              flat
              dense
              no-caps
              size="sm"
              :icon="model.project_id ? 'folder_special' : 'public'"
              :label="model.project_id ? 'this project' : 'shared'"
              class="model__filing"
              @click.prevent.stop="toggleFiling(model)"
            >
              <q-tooltip>
                {{
                  model.project_id
                    ? 'Filed under this project. Click to share it across all of them.'
                    : 'Shared across every project. Click to file it under this one.'
                }}
              </q-tooltip>
            </q-btn>
          </q-item-section>
        </q-item>
      </q-list>
      <EmptyState
        v-if="!models.length && !loading"
        :message="term ? 'No model matches that' : 'No models in this category'"
        :hint="
          term
            ? 'Try a shorter term, or clear the search'
            : 'Start with a Formula or a Rule model — neither needs training'
        "
        icon="category"
      />
      </AsyncSection>
    </SectionCard>

    <!-- create -->
    <!--
      Progressive disclosure, in three steps rather than six.

      What changes between providers is the configuration, and the provider
      already describes it — so the form is generated from that contract instead
      of asking somebody to hand-write JSON against a field list printed
      underneath. The JSON is still reachable for anything the form cannot
      express, but it is no longer the only way in.
    -->
    <q-dialog v-model="dialog">
      <q-card class="create">
        <q-card-section class="fx-dialog__title">New model</q-card-section>
        <q-card-section class="fx-dialog__subtitle">
          A model is any versioned, executable computational unit — a formula and a
          regressor are created the same way.
        </q-card-section>

        <!--
          Scrolls on its own so the actions stay reachable: a provider with a
          long configuration made the Create button fall off the screen.
        -->
        <q-card-section class="q-pt-none create__body">
          <div class="fx-form">
            <div class="create__step">1 · What kind of computation</div>
            <q-select
              v-model="form.provider"
              :options="providerOptions"
              label="Provider"
              dense
              outlined
              emit-value
              map-options
              @update:model-value="applyProviderDefaults"
            />
            <div v-if="selectedProvider" class="provider-note">
              <p class="q-mb-sm">{{ selectedProvider.description }}</p>
              <FactList
                :facts="[
                  { label: 'Category', value: label(selectedProvider.model_type) },
                  { label: 'Runs as', value: selectedProvider.supported_kinds.join(', ') },
                  {
                    label: 'Training',
                    value: selectedProvider.trainable ? 'required before predicting' : 'not needed',
                  },
                ]"
              />
            </div>

            <template v-if="form.provider">
              <div class="create__step">2 · Name it</div>
              <q-input v-model="form.name" label="Name" dense outlined autofocus />
              <q-input v-model="form.description" label="Description" dense outlined />

              <div class="create__step">
                3 · Configure it
                <q-btn
                  no-caps
                  flat
                  dense
                  :label="rawMode ? 'Use the form' : 'Edit as JSON'"
                  class="create__toggle"
                  @click="toggleRaw"
                />
              </div>

              <!-- A provider that ships an example starts you from it. -->
              <div v-if="providerExamples.length && !rawMode" class="fx-tags q-mb-sm">
                <q-btn
                  v-for="example in providerExamples"
                  :key="example.name"
                  no-caps
                  flat
                  dense
                  icon="auto_awesome"
                  :label="example.name"
                  @click="useExample(example)"
                />
              </div>

              <ContractForm
                v-if="!rawMode && configurationFields.length"
                v-model="form.configuration"
                :fields="configurationFields"
              />
              <p v-else-if="!rawMode" class="fx-meta">
                This provider needs no configuration.
              </p>
              <q-input
                v-else
                v-model="configurationText"
                label="Configuration (JSON)"
                type="textarea"
                rows="10"
                dense
                outlined
                class="mono"
                :error="!!configurationError"
                :error-message="configurationError ?? undefined"
              />
            </template>
          </div>
        </q-card-section>

        <q-card-actions align="right">
          <q-btn flat no-caps label="Cancel" v-close-popup />
          <q-btn
            unelevated
            no-caps
            color="primary"
            label="Create"
            class="fx-btn"
            :disable="!form.provider || !form.name.trim()"
            :loading="saving"
            @click="create"
          />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { models as modelsApi } from '@/api'
import { getProject } from '@/api/client'
import EmptyState from '@/components/EmptyState.vue'
import FactList from '@/components/FactList.vue'
import SearchField from '@/components/SearchField.vue'
import StatusText from '@/components/StatusText.vue'
import AsyncSection from '@/components/AsyncSection.vue'
import ContractForm from '@/components/ContractForm.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import type { ModelDefinition, ProviderDescriptor } from '@/types'

const $q = useQuasar()
const router = useRouter()

const models = ref<ModelDefinition[]>([])
const term = ref('')

const listSubtitle = computed(() => {
  const count = `${models.value.length} model${models.value.length === 1 ? '' : 's'}`
  return term.value.trim() ? `${count} matching` : `${count} in the library`
})
const providers = ref<ProviderDescriptor[]>([])
const providerGroups = ref<
  { type: string; providers: { key: string; name: string; trainable: boolean }[] }[]
>([])
const loading = ref(false)
const loadError = ref<string | null>(null)
const saving = ref(false)
const dialog = ref(false)
const typeFilter = ref<string | null>(null)

const form = ref({
  name: '',
  provider: 'formula',
  description: '',
  configuration: {} as Record<string, unknown>,
})
const configurationText = ref('{}')

const TYPE_ICONS: Record<string, string> = {
  machine_learning: 'psychology',
  statistical: 'query_stats',
  mathematical: 'functions',
  rule: 'rule',
  optimization: 'tune',
  simulation: 'waves',
  formula: 'calculate',
  custom: 'code',
}

const providerOptions = computed(() =>
  providers.value.map((provider) => ({
    label: `${provider.name} — ${label(provider.model_type)}`,
    value: provider.key,
  })),
)
const typeOptions = computed(() =>
  providerGroups.value.map((group) => ({ label: label(group.type), value: group.type })),
)
const selectedProvider = computed(
  () => providers.value.find((p) => p.key === form.value.provider) ?? null,
)
const configurationFields = computed(
  () => selectedProvider.value?.configuration_contract.fields ?? [],
)

/** The capability that decides whether this row is worth opening. */
function runsAs(model: ModelDefinition) {
  const kinds = model.capabilities.execution_kinds
  if (!kinds.length) return 'not executable'
  return kinds.length > 2 ? `${kinds.slice(0, 2).join(', ')} +${kinds.length - 2}` : kinds.join(', ')
}

function label(type: string) {
  return type.replace(/_/g, ' ').replace(/\b\w/g, (character) => character.toUpperCase())
}

function typeIcon(type: string) {
  return TYPE_ICONS[type] ?? 'category'
}

function applyProviderDefaults() {
  const example = selectedProvider.value?.examples?.[0]
  const configuration = example?.configuration ?? example?.parameters ?? {}
  form.value.configuration = JSON.parse(JSON.stringify(configuration))
  configurationText.value = JSON.stringify(configuration, null, 2)
  configurationError.value = null
}

function openCreate(providerKey?: string) {
  //  Start clean. The dialog defaults to a provider, and its example name was
  //  sticking around after you picked a different one.
  form.value = {
    name: '',
    provider: providerKey ?? form.value.provider,
    description: '',
    configuration: {},
  }
  rawMode.value = false
  applyProviderDefaults()
  dialog.value = true
}

async function loadModels() {
  loading.value = true
  loadError.value = null
  try {
    const params = new URLSearchParams()
    if (typeFilter.value) params.set('model_type', typeFilter.value)
    if (term.value.trim()) params.set('search', term.value.trim())
    models.value = await modelsApi.list(params.toString() ? `?${params}` : '')
  } catch (error) {
    //  Keep the reason on the page; a toast is gone in four seconds.
    loadError.value = (error as Error).message
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    loading.value = false
  }
}

const rawMode = ref(false)
const configurationError = ref<string | null>(null)

const providerExamples = computed(() => selectedProvider.value?.examples ?? [])

/** Carry whatever has been entered across the form/JSON boundary. */
function toggleRaw() {
  if (rawMode.value) {
    try {
      form.value.configuration = JSON.parse(configurationText.value || '{}')
      configurationError.value = null
    } catch (error) {
      configurationError.value = (error as Error).message
      return
    }
  } else {
    configurationText.value = JSON.stringify(form.value.configuration ?? {}, null, 2)
  }
  rawMode.value = !rawMode.value
}

function useExample(example: { name: string; configuration?: Record<string, unknown> }) {
  form.value.configuration = JSON.parse(JSON.stringify(example.configuration ?? {}))
  if (!form.value.name.trim()) form.value.name = example.name
}

async function create() {
  saving.value = true
  try {
    //  Whichever mode the form is in is the source of truth for the payload.
    const configuration = rawMode.value
      ? JSON.parse(configurationText.value || '{}')
      : form.value.configuration
    const created = await modelsApi.create({
      name: form.value.name,
      provider: form.value.provider,
      description: form.value.description,
      configuration,
    })
    $q.notify({ type: 'positive', message: `"${created.name}" created at v1` })
    dialog.value = false
    form.value = { name: '', provider: form.value.provider, description: '', configuration: {} }
    rawMode.value = false
    await router.push({ name: 'model-detail', params: { id: created.id } })
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    saving.value = false
  }
}

/**
 * One loader for the page, so one failure is reported once.
 *
 * The catalogue was fetched in `onMounted` and the models in their own
 * function; when the catalogue failed it threw before the models were ever
 * requested, so the page recorded no error and rendered its empty state — the
 * one outcome that reads as "there is nothing here" rather than "this broke".
 */
/**
 * Share this definition across every project, or pull it into the current one.
 *
 * Only definitions move. The runs, results and datasets a model produced stay
 * filed where the work happened, because that is what they are evidence of.
 */
async function toggleFiling(model: ModelDefinition) {
  const project = getProject()
  if (!model.project_id && !project) {
    $q.notify({ type: 'warning', message: 'Choose a project first' })
    return
  }
  try {
    await modelsApi.fileUnder(model.id, model.project_id ? null : project)
    await loadModels()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

async function loadAll() {
  loading.value = true
  loadError.value = null
  try {
    const [providerList, types] = await Promise.all([modelsApi.providers(), modelsApi.types()])
    providers.value = providerList.providers
    providerGroups.value = types.types
  } catch (error) {
    loadError.value = (error as Error).message
    $q.notify({ type: 'negative', message: (error as Error).message })
    loading.value = false
    return
  }
  await loadModels()
}

onMounted(loadAll)
</script>

<style scoped>
/*  Filing is context, not the row's purpose: quiet until hovered. */
.model__filing {
  opacity: var(--fx-ink-muted);
}

.model__filing:hover {
  opacity: 1;
}

.providers {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
}

.providers__group {
  padding: var(--fx-space-4) var(--fx-space-5);
  border-right: 1px solid var(--fx-border);
  border-bottom: 1px solid var(--fx-border);
}

.providers__type {
  font-size: var(--fx-text-xs);
  font-weight: 600;
  opacity: var(--fx-ink-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: var(--fx-space-2);
}

.providers__list {
  display: flex;
  flex-direction: column;
  gap: var(--fx-space-1);
  align-items: flex-start;
}

.provider {
  background: none;
  border: none;
  padding: 2px 0;
  cursor: pointer;
  color: inherit;
  font: inherit;
  text-align: left;
  display: flex;
  align-items: baseline;
  gap: var(--fx-space-2);
}

.provider__name {
  font-size: var(--fx-text-sm);
  border-bottom: 1px solid transparent;
}

.provider:hover .provider__name {
  border-bottom-color: currentColor;
}

.provider__note {
  font-size: var(--fx-text-xs);
  opacity: var(--fx-ink-faint);
}

.providers__none {
  font-size: var(--fx-text-xs);
  opacity: var(--fx-ink-faint);
}

.model__icon {
  opacity: var(--fx-ink-faint);
}

.model__meta {
  align-items: flex-end;
  text-align: right;
}

/*
  On a phone the capability text and the model name were competing for one
  line, and the name lost - a list of "Typho..." tells you nothing. Below the
  small breakpoint the row stacks and the name gets the full width.
*/
@media (max-width: 599px) {
  .fx-list .q-item {
    flex-wrap: wrap;
  }

  .model__meta {
    align-items: flex-start;
    padding-left: 0;
    text-align: left;
    width: 100%;
  }

  .model__meta .fx-meta,
  .model__type {
    font-size: var(--fx-text-xs);
  }
}

.model__type {
  font-size: var(--fx-text-sm);
}

.provider-note {
  padding: var(--fx-space-3) var(--fx-space-4);
  background: var(--fx-surface-muted);
  border-radius: var(--fx-radius-sm);
  font-size: var(--fx-text-sm);
}

.create {
  width: 720px;
  max-width: 92vw;
  display: flex;
  flex-direction: column;
  max-height: 88vh;
}

.create__body {
  overflow-y: auto;
}

.create__step {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fx-space-2);
  margin-top: var(--fx-space-2);
  font-size: var(--fx-text-xs);
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  opacity: var(--fx-ink-muted);
}

.create__step:first-child {
  margin-top: 0;
}

.create__toggle {
  font-size: var(--fx-text-xs);
  letter-spacing: 0;
  text-transform: none;
}

.provider-note p {
  margin: 0 0 var(--fx-space-3);
  opacity: var(--fx-ink);
}
</style>

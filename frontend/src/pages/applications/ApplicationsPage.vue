<template>
  <q-page class="page-shell">
    <PageHeader
      title="Applications"
      subtitle="Where data, models and dashboards are packaged into something a person actually uses"
    >
      <template #actions>
        <q-btn no-caps color="primary" unelevated icon="add" label="New application" @click="dialog = true" />
      </template>
    </PageHeader>

    <div class="row q-col-gutter-md">
      <div v-for="application in applications" :key="application.id" class="col-12 col-md-6">
        <SectionCard
          :title="application.name"
          :subtitle="application.description"
          :icon="application.kind === 'builtin' ? 'auto_awesome' : 'apps'"
        >
          <template #actions>
            <StatusText :status="application.status" />
          </template>
          <FactList :facts="composition(application)" />
          <!--
            Shown inline rather than in a dialog that disappears: the link is
            the thing somebody came here to copy, and hiding it behind a
            second click after they have already asked for it helps nobody.
          -->
          <div v-if="shareLinks[application.id]" class="share">
            <div class="fx-meta">Anybody with this link can open it. No account needed.</div>
            <div class="share__row">
              <code class="share__url">{{ shareLinks[application.id] }}</code>
              <q-btn
                no-caps
                flat
                dense
                icon="content_copy"
                label="Copy"
                @click="copyLink(application.id)"
              />
            </div>
          </div>
          <template #footer>
            <!--
              Everything opens somewhere: a built-in application at the route
              it names, anything else on its own page.
            -->
            <q-btn no-caps
              flat
              color="primary"
              icon="open_in_new"
              label="Open"
              :to="application.entrypoint || `/applications/${application.id}`"
            />
            <q-btn no-caps
              v-if="application.status !== 'published'"
              flat
              icon="publish"
              label="Publish"
              :disable="!canPublish(application)"
              @click="publish(application.id)"
            >
              <q-tooltip v-if="!canPublish(application)">
                Add a dashboard first — publishing it would make nothing reachable
              </q-tooltip>
            </q-btn>
            <!--
              What a Deployment's "stop" was reaching for. Nothing is torn down
              because nothing was stood up: the application stops being offered
              and everything it bundles is left alone.
            -->
            <q-btn no-caps
              v-else
              flat
              icon="unpublished"
              label="Unpublish"
              @click="unpublish(application)"
            />
            <!--
              Only for a published application, because sharing a draft would
              put a half-built thing in front of somebody outside.
            -->
            <q-btn no-caps
              v-if="application.status === 'published'"
              flat
              :icon="application.is_shared ? 'link_off' : 'link'"
              :label="application.is_shared ? 'Stop sharing' : 'Share link'"
              @click="toggleShare(application)"
            />
            <q-space />
            <q-btn
              v-if="application.kind !== 'builtin'"
              flat
              dense
              round
              icon="delete"
              color="negative"
              @click="remove(application)"
            />
          </template>
        </SectionCard>
      </div>
    </div>

    <EmptyState v-if="!applications.length && !loading" message="No applications" icon="apps" />

    <q-dialog v-model="dialog">
      <q-card style="min-width: 480px">
        <q-card-section class="fx-dialog__title">New application</q-card-section>
        <q-card-section class="q-gutter-md">
          <q-input v-model="form.name" label="Name" dense outlined autofocus />
          <q-input v-model="form.description" label="Description" dense outlined />
          <q-select
            v-model="form.model_ids"
            :options="modelOptions"
            label="Models"
            dense
            outlined
            multiple
            use-chips
            emit-value
            map-options
          />
          <q-select
            v-model="form.dashboard_ids"
            :options="dashboardOptions"
            label="Dashboards"
            dense
            outlined
            multiple
            use-chips
            emit-value
            map-options
          />
          <q-input v-model="form.entrypoint" label="Entrypoint route" dense outlined hint="e.g. /dashboards" />
        </q-card-section>
        <q-card-actions align="right">
          <q-btn no-caps flat label="Cancel" v-close-popup />
          <q-btn no-caps unelevated color="primary" label="Create" @click="create" />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref } from 'vue'

import { analysis, applications as appsApi, models as modelsApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import FactList, { type Fact } from '@/components/FactList.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import StatusText from '@/components/StatusText.vue'
import type { Application, Dashboard, ModelDefinition } from '@/types'

const $q = useQuasar()
const applications = ref<Application[]>([])
//  Held per application: several can be shared, and each has its own link.
const shareLinks = ref<Record<string, string>>({})
const modelList = ref<ModelDefinition[]>([])
const dashboards = ref<Dashboard[]>([])
const loading = ref(false)
const dialog = ref(false)
const form = ref({
  name: '',
  description: '',
  entrypoint: '',
  model_ids: [] as string[],
  dashboard_ids: [] as string[],
})

//  A picker offers the library. Step models belong to their pipeline and
//  choosing one here would attach a stage of somebody else's chain.
const modelOptions = computed(() =>
  modelList.value.map((m) => ({ label: m.name, value: m.id })),
)
const dashboardOptions = computed(() => dashboards.value.map((d) => ({ label: d.name, value: d.id })))

/** What an application is made of, in the same shape on every card. */
function composition(application: Application): Fact[] {
  return [
    { label: 'Kind', value: application.kind },
    { label: 'Models', value: application.model_ids.length, numeric: true },
    { label: 'Datasets', value: application.dataset_ids.length, numeric: true },
    { label: 'Dashboards', value: application.dashboard_ids.length, numeric: true },
  ]
}

async function load() {
  loading.value = true
  try {
    const [apps, allModels, dashboardList] = await Promise.all([
      appsApi.list(),
      modelsApi.all(),
      analysis.listDashboards(),
    ])
    applications.value = apps
    modelList.value = allModels
    dashboards.value = dashboardList
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    loading.value = false
  }
}

async function create() {
  try {
    await appsApi.create({ ...form.value, entrypoint: form.value.entrypoint || null })
    dialog.value = false
    form.value = { name: '', description: '', entrypoint: '', model_ids: [], dashboard_ids: [] }
    await load()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

/**
 * Whether there is anything to open.
 *
 * The rule is not "has an entrypoint" - that only ever held because a composed
 * application had no page of its own. It has one now, so what it needs is
 * something to show on it.
 */
function canPublish(application: Application): boolean {
  return Boolean(application.entrypoint) || application.dashboard_ids.length > 0
}

async function publish(id: string) {
  try {
    await appsApi.publish(id)
    await load()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

/**
 * Turn sharing on or off.
 *
 * Revoking asks first: the link may already be in somebody else's hands, and
 * breaking it is not the kind of thing to do on a stray click.
 */
async function toggleShare(application: Application) {
  if (application.is_shared) {
    $q.dialog({
      title: 'Stop sharing',
      message:
        `Anyone already holding the link to "${application.name}" will lose ` +
        'access immediately. Sharing again later creates a new link.',
      cancel: true,
    }).onOk(async () => {
      try {
        await appsApi.unshare(application.id)
        delete shareLinks.value[application.id]
        await load()
      } catch (error) {
        $q.notify({ type: 'negative', message: (error as Error).message })
      }
    })
    return
  }
  try {
    const shared = await appsApi.share(application.id)
    //  Absolute, because the point of the link is to be sent somewhere else.
    shareLinks.value[application.id] = `${window.location.origin}${shared.share_url}`
    await load()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

async function copyLink(applicationId: string) {
  const link = shareLinks.value[applicationId]
  if (!link) return
  try {
    await navigator.clipboard.writeText(link)
    $q.notify({ type: 'positive', message: 'Link copied' })
  } catch {
    //  Clipboard access can be refused; the link is on screen either way.
    $q.notify({ type: 'warning', message: 'Copy it from the box above' })
  }
}

function unpublish(application: Application) {
  $q.dialog({
    title: 'Unpublish application',
    message: `Stop offering "${application.name}"? It goes back to draft; nothing it bundles is changed.`,
    cancel: true,
  }).onOk(async () => {
    try {
      await appsApi.unpublish(application.id)
      await load()
    } catch (error) {
      $q.notify({ type: 'negative', message: (error as Error).message })
    }
  })
}

function remove(application: Application) {
  $q.dialog({
    title: 'Delete application',
    message: `Delete "${application.name}"?`,
    cancel: true,
  }).onOk(async () => {
    await appsApi.remove(application.id)
    await load()
  })
}

onMounted(load)
</script>

<template>
  <q-page class="page-shell">
    <PageHeader
      title="Projects"
      subtitle="每一份工作有自己的資料目錄與清單；切換專案就切換整個 Data & analysis 看到的內容"
    >
      <template #actions>
        <q-btn
          no-caps
          color="primary"
          unelevated
          icon="create_new_folder"
          label="New project"
          @click="openCreate"
        />
      </template>
    </PageHeader>

    <SectionCard
      title="What a project is"
      subtitle="A workspace decides who may see what; a project decides what you are looking at"
      class="q-mb-md"
      tight
    >
      <dl class="fx-facts">
        <dt class="fx-facts__label">Boundary</dt>
        <dd class="fx-facts__value">
          Workspace. Nothing crosses it — a resource from another workspace cannot be read
          even if you know its id.
        </dd>
        <dt class="fx-facts__label">Filing</dt>
        <dd class="fx-facts__value">
          Project. Sources, datasets, pipelines, visualizations, dashboards, models,
          executions, results, experiments and evaluations are filed under one, and each
          page lists the current project's.
        </dd>
        <dt class="fx-facts__label">Unfiled</dt>
        <dd class="fx-facts__value">
          Anything without a project — a model definition meant to be reused, say — appears
          under <em>every</em> project rather than none, so shared work stays reachable.
        </dd>
        <dt class="fx-facts__label">Not filed at all</dt>
        <dd class="fx-facts__value">
          Applications, reports and schedules. Each names what it acts on, so none of them
          needs a project chosen first.
        </dd>
      </dl>
    </SectionCard>

    <AsyncSection
      :pending="loading && !projects.length"
      :error="error"
      title="Could not load projects"
      :rows="4"
      :on-retry="load"
    >
      <SectionCard title="Projects" flush>
        <q-list separator class="fx-list">
          <q-item v-for="project in projects" :key="project.id">
            <q-item-section avatar>
              <q-avatar
                size="32px"
                :color="project.id === currentId ? 'primary' : 'grey-6'"
                text-color="white"
              >
                {{ project.name.slice(0, 1).toUpperCase() }}
              </q-avatar>
            </q-item-section>
            <q-item-section>
              <q-item-label>
                {{ project.name }}
                <StatusText v-if="project.is_default" status="draft" label="default" />
                <StatusText
                  v-if="project.id === currentId"
                  status="succeeded"
                  label="current"
                />
              </q-item-label>
              <q-item-label caption>{{ project.description || '—' }}</q-item-label>
              <q-item-label caption class="fx-meta">
                <code>data/{{ project.directory }}/sources/</code>
                <span v-if="holdings[project.id]"> · {{ summarise(holdings[project.id]) }}</span>
              </q-item-label>
            </q-item-section>
            <q-item-section side>
              <div class="row items-center q-gutter-xs">
                <q-btn
                  v-if="project.id !== currentId"
                  no-caps
                  flat
                  dense
                  icon="login"
                  label="Switch to"
                  @click="switchTo(project)"
                />
                <q-btn flat dense round icon="edit" @click="openEdit(project)">
                  <q-tooltip>Rename or re-describe</q-tooltip>
                </q-btn>
                <q-btn
                  flat
                  dense
                  round
                  icon="delete"
                  color="negative"
                  :disable="project.is_default"
                  @click="remove(project)"
                >
                  <q-tooltip>
                    {{
                      project.is_default
                        ? 'The default project cannot be deleted'
                        : 'Delete this project'
                    }}
                  </q-tooltip>
                </q-btn>
              </div>
            </q-item-section>
          </q-item>
        </q-list>
        <EmptyState v-if="!projects.length && !loading" message="No projects" icon="folder" />
      </SectionCard>
    </AsyncSection>

    <q-dialog v-model="dialog">
      <q-card style="min-width: 460px">
        <q-card-section class="fx-dialog__title">
          {{ editing ? 'Edit project' : 'New project' }}
        </q-card-section>
        <q-card-section class="q-gutter-md">
          <q-input v-model="form.name" label="Name" dense outlined autofocus />
          <q-input
            v-model="form.description"
            label="Description"
            type="textarea"
            autogrow
            dense
            outlined
          />
          <q-input
            v-model="form.directory"
            label="Directory"
            dense
            outlined
            :hint="directoryHint"
          />
        </q-card-section>
        <q-card-actions align="right">
          <q-btn no-caps flat label="Cancel" v-close-popup />
          <q-btn
            no-caps
            unelevated
            color="primary"
            :label="editing ? 'Save' : 'Create'"
            :loading="saving"
            :disable="!form.name.trim()"
            @click="save"
          />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
/**
 * Where a piece of work is started, and where you can see what each one holds.
 *
 * The switcher in the toolbar is how you *move* between projects; this page is
 * how you make one, retire one, and see the directory its source files land
 * in — which is the part that matters when somebody is dropping files onto the
 * server by hand.
 */
import { computed, onMounted, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute, useRouter } from 'vue-router'

import { projects as projectsApi } from '@/api'
import { getProject, setProject } from '@/api/client'
import AsyncSection from '@/components/AsyncSection.vue'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import StatusText from '@/components/StatusText.vue'
import type { Project } from '@/types'

const $q = useQuasar()
const route = useRoute()
const router = useRouter()

const projects = ref<Project[]>([])
const holdings = ref<Record<string, Record<string, number>>>({})
const loading = ref(false)
const error = ref('')
const saving = ref(false)
const dialog = ref(false)
const editing = ref<Project | null>(null)
const form = ref({ name: '', description: '', directory: '' })
const currentId = ref<string | null>(getProject())

const directoryHint = computed(() =>
  editing.value
    ? 'Renaming this moves the files on disk'
    : 'Left blank, the name is used. Created under the data root.',
)

/** "3 datasets · 2 pipelines", counting only what is actually there. */
function summarise(holds: Record<string, number>) {
  const parts = Object.entries(holds)
    .filter(([, count]) => count > 0)
    .map(([kind, count]) => `${count} ${kind}`)
  return parts.length ? parts.join(' · ') : 'empty'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    projects.value = await projectsApi.list()
    //  Counts are a separate call per project, and a project that fails to
    //  answer should cost its own row's detail, not the whole page.
    const counted = await Promise.all(
      projects.value.map(async (project) => {
        try {
          return [project.id, (await projectsApi.holdings(project.id)).holds] as const
        } catch {
          return [project.id, {}] as const
        }
      }),
    )
    holdings.value = Object.fromEntries(counted)
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  form.value = { name: '', description: '', directory: '' }
  dialog.value = true
}

function openEdit(project: Project) {
  editing.value = project
  form.value = {
    name: project.name,
    description: project.description,
    directory: project.directory,
  }
  dialog.value = true
}

async function save() {
  saving.value = true
  try {
    if (editing.value) {
      await projectsApi.update(editing.value.id, {
        name: form.value.name.trim(),
        description: form.value.description,
        directory: form.value.directory.trim(),
      })
    } else {
      const body: { name: string; description?: string; directory?: string } = {
        name: form.value.name.trim(),
        description: form.value.description,
      }
      //  Absent rather than empty: the backend derives one from the name, and
      //  an empty string is a directory called "".
      if (form.value.directory.trim()) body.directory = form.value.directory.trim()
      await projectsApi.create(body)
    }
    dialog.value = false
    await load()
  } catch (err) {
    $q.notify({ type: 'negative', message: (err as Error).message })
  } finally {
    saving.value = false
  }
}

/**
 * Move to this project, then reload — as the toolbar switcher does.
 *
 * Everything on screen was filtered by the project being left, so refetching
 * page by page would leave the two mixed for as long as the requests take.
 */
function switchTo(project: Project) {
  setProject(project.id)
  window.location.reload()
}

function remove(project: Project) {
  const holds = summarise(holdings.value[project.id] ?? {})
  $q.dialog({
    title: 'Delete project',
    message:
      holds === 'empty'
        ? `Delete ${project.name}? Its directory stays on disk.`
        : `${project.name} still holds ${holds}. Deleting is refused until it is empty.`,
    cancel: true,
  }).onOk(async () => {
    try {
      await projectsApi.remove(project.id)
      //  Deleting what you were looking at would leave every page filtering
      //  by an id that no longer exists.
      if (project.id === currentId.value) {
        setProject(null)
        window.location.reload()
        return
      }
      await load()
    } catch (err) {
      $q.notify({ type: 'negative', message: (err as Error).message })
    }
  })
}

onMounted(async () => {
  await load()
  //  The toolbar's "New project" lands here rather than carrying a second
  //  copy of this form. Consumed from the URL so a reload does not reopen it.
  if (route.query.new) {
    //  Clear the flag *before* opening, and wait for it: a QDialog dismisses
    //  itself on route change, so opening first and tidying the URL after
    //  closed the dialog on the same tick it appeared.
    await router.replace({ name: 'projects' })
    openCreate()
  }
})
</script>

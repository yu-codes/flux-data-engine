<template>
  <!-- The sign-in page has no chrome around it. -->
  <q-layout v-if="bare" view="hHh Lpr lFf">
    <q-page-container>
      <router-view />
    </q-page-container>
  </q-layout>

  <q-layout v-else view="hHh Lpr lFf">
    <q-header elevated class="bg-dark text-white">
      <q-toolbar>
        <q-btn no-caps dense flat round icon="menu" aria-label="Toggle navigation" @click="drawer = !drawer" />
        <!--
          The toolbar carries the product name only. The abstraction was a motto
          — true, but not something anyone acts on — and the provider count and
          execution mode are deployment facts that belong on Settings, where
          somebody looking for them would go.
        -->
        <q-toolbar-title class="shell__title">
          <span class="shell__brand">flux-data-engine</span>
        </q-toolbar-title>

        <q-space />

        <!--
          Where you are: which workspace, and which project inside it.

          Deliberately not a button. This is product context — the answer to
          "which of my pieces of work am I looking at" — and the first cut of
          it (a filled, bordered pill with a folder icon) carried more visual
          weight than the page title it sat above. Icon plus border plus fill
          is three ways of saying the same thing; the name and a small caret
          say it once, and the background appears on hover, where an
          affordance belongs.

          The workspace half appears only when there is more than one: a
          chooser with one choice is furniture. The slash between them is the
          relationship — a project lives inside a workspace.
        -->
        <div class="scope">
          <q-btn
            v-if="workspaces.length > 1"
            flat
            no-caps
            class="scope__btn"
            data-testid="workspace-switcher"
          >
            <span class="scope__name">{{ currentWorkspace?.name ?? 'Workspace' }}</span>
            <q-icon name="expand_more" size="16px" class="scope__caret" />
            <q-tooltip :delay="400">Switch workspace</q-tooltip>
            <q-menu class="scope__menu" :offset="[0, 12]">
              <div class="scope__heading">Workspaces</div>
              <q-list class="scope__list">
                <q-item
                  v-for="workspace in workspaces"
                  :key="workspace.id"
                  clickable
                  v-close-popup
                  class="scope__option"
                  :class="{ 'scope__option--current': workspace.id === currentWorkspaceId }"
                  @click="switchWorkspace(workspace)"
                >
                  <q-item-section>
                    <q-item-label class="scope__option-name">{{ workspace.name }}</q-item-label>
                    <q-item-label caption class="scope__option-note">
                      {{ workspace.is_default ? 'the default' : workspace.description || '—' }}
                    </q-item-label>
                  </q-item-section>
                  <q-item-section v-if="workspace.id === currentWorkspaceId" side>
                    <q-icon name="check" size="16px" />
                  </q-item-section>
                </q-item>
              </q-list>
            </q-menu>
          </q-btn>

          <span v-if="workspaces.length > 1" class="scope__slash">/</span>

          <q-btn flat no-caps class="scope__btn" data-testid="project-switcher">
            <span class="scope__name">{{ currentProject?.name ?? 'Project' }}</span>
            <q-icon name="expand_more" size="16px" class="scope__caret" />
            <q-tooltip :delay="400">Switch project</q-tooltip>
            <q-menu class="scope__menu" :offset="[0, 12]">
              <div class="scope__heading">Projects</div>
              <q-list class="scope__list">
                <q-item
                  v-for="project in projects"
                  :key="project.id"
                  clickable
                  v-close-popup
                  class="scope__option"
                  :class="{ 'scope__option--current': project.id === currentProjectId }"
                  @click="switchProject(project)"
                >
                  <q-item-section>
                    <q-item-label class="scope__option-name">
                      {{ project.name }}
                      <span v-if="project.is_default" class="scope__default">default</span>
                    </q-item-label>
                    <q-item-label caption class="scope__option-note">
                      {{ project.description || `data/${project.directory}/` }}
                    </q-item-label>
                  </q-item-section>
                  <q-item-section v-if="project.id === currentProjectId" side>
                    <q-icon name="check" size="16px" />
                  </q-item-section>
                </q-item>
              </q-list>
              <q-separator />
              <!--
                Starting a piece of work is the other thing somebody opens this
                for, so it is here rather than only on the page. Both routes
                land on the same page — the form lives in one place.
              -->
              <q-item
                clickable
                v-close-popup
                class="scope__action"
                :to="{ name: 'projects', query: { new: '1' } }"
              >
                <q-item-section>
                  <span class="scope__action-line">
                    <q-icon name="add" size="16px" />
                    New project
                  </span>
                </q-item-section>
              </q-item>
              <q-item clickable v-close-popup class="scope__action" :to="{ name: 'projects' }">
                <q-item-section>
                  <span class="scope__action-line">
                    <q-icon name="settings" size="16px" />
                    Manage projects
                  </span>
                </q-item-section>
              </q-item>
            </q-menu>
          </q-btn>
        </div>

        <q-btn dense flat round :icon="$q.dark.isActive ? 'light_mode' : 'dark_mode'" @click="toggleTheme" />

        <q-btn v-if="auth.authEnabled" dense flat round icon="account_circle">
          <q-menu>
            <q-list style="min-width: 220px">
              <q-item-label header class="q-pb-none">
                {{ auth.user?.display_name }}
                <div class="text-caption" style="opacity: 0.7">{{ auth.user?.email }}</div>
              </q-item-label>
              <q-item>
                <q-item-section>
                  <span class="fx-tag">{{ auth.user?.role }} role</span>
                </q-item-section>
              </q-item>
              <q-separator />
              <q-item clickable v-close-popup @click="passwordDialog = true">
                <q-item-section avatar><q-icon name="key" size="20px" /></q-item-section>
                <q-item-section>Change password</q-item-section>
              </q-item>
              <q-item clickable v-close-popup @click="signOut">
                <q-item-section avatar><q-icon name="logout" size="20px" /></q-item-section>
                <q-item-section>Sign out</q-item-section>
              </q-item>
            </q-list>
          </q-menu>
        </q-btn>
      </q-toolbar>
    </q-header>

    <q-drawer v-model="drawer" show-if-above bordered :width="248">
      <q-scroll-area class="fit">
        <q-list padding>
          <q-item clickable v-ripple :to="{ name: 'dashboard' }" exact>
            <q-item-section avatar><q-icon name="space_dashboard" /></q-item-section>
            <q-item-section>Overview</q-item-section>
          </q-item>

          <template v-for="group in visibleNavigation" :key="group.label">
            <q-item-label header class="shell__group">{{ group.label }}</q-item-label>
            <q-item
              v-for="item in group.items"
              :key="item.name"
              clickable
              v-ripple
              :to="{ name: item.name }"
            >
              <q-item-section avatar><q-icon :name="item.icon" size="20px" /></q-item-section>
              <q-item-section>{{ item.label }}</q-item-section>
            </q-item>
          </template>
        </q-list>
      </q-scroll-area>
    </q-drawer>

    <q-page-container>
      <router-view v-slot="{ Component }">
        <component :is="Component" />
      </router-view>
    </q-page-container>

    <!-- change password -->
    <q-dialog v-model="passwordDialog">
      <q-card style="min-width: 400px">
        <q-card-section class="fx-dialog__title">Change password</q-card-section>
        <q-card-section class="q-gutter-md">
          <q-input v-model="passwordForm.current" label="Current password" type="password" dense outlined />
          <q-input
            v-model="passwordForm.replacement"
            label="New password"
            type="password"
            dense
            outlined
            hint="at least 8 characters"
          />
        </q-card-section>
        <q-card-actions align="right">
          <q-btn no-caps flat label="Cancel" v-close-popup />
          <q-btn no-caps unelevated color="primary" label="Change" :loading="changing" @click="changePassword" />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-layout>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { auth as authApi, projects as projectsApi, workspaces as workspacesApi } from '@/api'
import { getProject, getWorkspace, setProject, setWorkspace } from '@/api/client'
import type { Project, Workspace } from '@/types'
import { useAuthStore } from '@/stores/auth'

const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const drawer = ref(true)
const passwordDialog = ref(false)
const changing = ref(false)
const passwordForm = ref({ current: '', replacement: '' })

const bare = computed(() => Boolean(route.meta.bare))

/**
 * The sidebar names the work, not the tables — and never an individual
 * resource.
 *
 * It previously carried one entry per persisted entity, which is why it held
 * both Jobs and Executions (the same rows, filtered), a Schemas page that was
 * inferred metadata with nothing to do on it, and a "Dashboard" sitting two
 * lines above "Dashboards". Those are gone.
 *
 * No application appears here, published or not. The shell of a general
 * platform should not name one domain, and listing instances of one resource
 * in navigation would mean listing instances of all of them. Applications are
 * found on the Applications page, which is what that page is for.
 */
const navigation = [
  {
    label: 'Data & analysis',
    items: [
      { name: 'sources', label: 'Sources', icon: 'cable' },
      { name: 'datasets', label: 'Datasets', icon: 'table_chart' },
      { name: 'pipelines', label: 'Pipelines', icon: 'account_tree' },
      { name: 'explore', label: 'Explore', icon: 'travel_explore' },
      { name: 'visualizations', label: 'Visualizations', icon: 'insights' },
      { name: 'dashboards', label: 'Dashboards', icon: 'dashboard' },
    ],
  },
  {
    //  The order is the workflow: define what to run, specify a comparison,
    //  run it, read what came out, judge it. Results sits before Evaluation
    //  because a result is the evidence and an evaluation is the verdict.
    label: 'Models & results',
    items: [
      { name: 'models', label: 'Model library', icon: 'category' },
      { name: 'experiments', label: 'Experiments', icon: 'science' },
      { name: 'executions', label: 'Executions', icon: 'play_circle' },
      { name: 'results', label: 'Results', icon: 'output' },
      { name: 'evaluation', label: 'Evaluation', icon: 'rule' },
    ],
  },
  {
    //  What the platform delivers on a standing basis. Reports and Schedules
    //  sit here rather than beside Results and the system pages: each is
    //  self-contained — a report cites resources by id, a schedule names a
    //  runnable — so neither needs a project chosen first.
    label: 'Applications',
    items: [
      { name: 'applications', label: 'All applications', icon: 'apps' },
      { name: 'reports', label: 'Reports', icon: 'description' },
      { name: 'schedules', label: 'Schedules', icon: 'schedule' },
    ],
  },
  {
    label: 'System',
    items: [
      { name: 'projects', label: 'Projects', icon: 'folder_special' },
      { name: 'users', label: 'Users', icon: 'group', permission: 'platform:admin' },
      { name: 'audit', label: 'Audit', icon: 'history' },
      { name: 'settings', label: 'Settings', icon: 'settings' },
    ],
  },
]

//  Hide what the account cannot open, rather than letting it 403 on arrival.
const visibleNavigation = computed(() =>
  navigation
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => !item.permission || auth.may(item.permission)),
    }))
    .filter((group) => group.items.length),
)

function toggleTheme() {
  $q.dark.toggle()
  localStorage.setItem('flux-dark', String($q.dark.isActive))
}

async function signOut() {
  auth.signOut()
  await router.replace({ name: 'login' })
}

async function changePassword() {
  changing.value = true
  try {
    await authApi.changePassword(passwordForm.value.current, passwordForm.value.replacement)
    passwordDialog.value = false
    passwordForm.value = { current: '', replacement: '' }
    $q.notify({ type: 'positive', message: 'Password changed' })
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    changing.value = false
  }
}

const workspaces = ref<Workspace[]>([])
const currentWorkspaceId = ref<string | null>(getWorkspace())

const currentWorkspace = computed(
  () =>
    workspaces.value.find((w) => w.id === currentWorkspaceId.value) ??
    workspaces.value.find((w) => w.is_default) ??
    null,
)

/**
 * Change workspace, then reload.
 *
 * A full reload rather than refetching every open page: the workspace decides
 * what every request returns, so anything already on screen belongs to the
 * old one. Leaving it there while new data arrives beneath it is how somebody
 * ends up acting on a resource from a workspace they have just left.
 */
function switchWorkspace(workspace: Workspace) {
  if (workspace.id === currentWorkspaceId.value) return
  setWorkspace(workspace.is_default ? null : workspace.id)
  window.location.reload()
}

async function loadWorkspaces() {
  //  Nobody signed in means no workspaces to list. The condition used to read
  //  `!authEnabled && !user`, which is true only when auth is off *and*
  //  nobody is signed in - so the one case it was meant to skip, a signed-out
  //  visitor on a platform with auth on, was the case it let through.
  if (auth.authEnabled && !auth.user) return
  try {
    workspaces.value = await workspacesApi.list()
    //  A stored workspace that no longer exists, or that this person has been
    //  removed from, must not leave every page failing: fall back to the
    //  default rather than to an error.
    if (
      currentWorkspaceId.value &&
      !workspaces.value.some((w) => w.id === currentWorkspaceId.value)
    ) {
      setWorkspace(null)
      currentWorkspaceId.value = null
    }
  } catch {
    workspaces.value = []
  }
}

const projects = ref<Project[]>([])
const currentProjectId = ref<string | null>(getProject())

const currentProject = computed(
  () =>
    projects.value.find((p) => p.id === currentProjectId.value) ??
    projects.value.find((p) => p.is_default) ??
    null,
)

/**
 * Change project, then reload — for the same reason switching workspace does.
 *
 * The stored value is the id even for the default, unlike the workspace: a
 * workspace is a boundary and "none" means the default one, but a project is
 * a filing system where "none" means unfiled, and unfiled resources show
 * under every project. Naming the default explicitly keeps the two apart.
 */
function switchProject(project: Project) {
  if (project.id === currentProjectId.value) return
  setProject(project.id)
  window.location.reload()
}

async function loadProjects() {
  if (auth.authEnabled && !auth.user) return
  try {
    projects.value = await projectsApi.list()
    const known = projects.value.some((p) => p.id === currentProjectId.value)
    if (!known) {
      //  Either nothing was chosen yet, or what was chosen has been deleted.
      //  Both land on the default, which always exists.
      const fallback = projects.value.find((p) => p.is_default) ?? projects.value[0] ?? null
      setProject(fallback?.id ?? null)
      currentProjectId.value = fallback?.id ?? null
    }
  } catch {
    projects.value = []
  }
}

/**
 * Both lists, in order: the project list is a list of *that* workspace's
 * projects, so asking for it before the workspace is settled lists the wrong
 * ones.
 */
async function loadScopes() {
  await loadWorkspaces()
  await loadProjects()
}

onMounted(() => {
  const stored = localStorage.getItem('flux-dark')
  if (stored !== null) $q.dark.set(stored === 'true')
  loadScopes()
})

/**
 * Load again when somebody signs in.
 *
 * The shell mounts around the sign-in page too, so on a cold start both loads
 * run before there is anyone to load for, take the `!auth.user` exit, and —
 * without this — never run again: the layout is not remounted by signing in,
 * so the switchers stayed empty for the rest of the session. It was invisible
 * while the workspace chooser hid itself at one workspace; the project
 * chooser is always shown, so it showed the hole.
 */
watch(
  () => auth.user?.id,
  (id, before) => {
    if (id && id !== before) loadScopes()
  },
)
</script>

<style scoped>
/*
 * The toolbar is laid out with gap rather than Quasar's `q-gutter-*`, whose
 * negative margins pull the title box back underneath the menu button. The
 * text stayed clear of it, but the boxes overlapped — and a layout that only
 * looks right is one bad wrap away from looking wrong.
 */
.shell__title {
  display: flex;
  align-items: center;
  gap: var(--fx-space-3);
  min-width: 0;
  /* Quasar sets 21px here, which is off the scale and louder than the page
     title it sits above. The brand is chrome; the page title is the heading. */
  font-size: var(--fx-text-lg);
}

/* Group headings carry the structure, so they are readable rather than faint. */
.shell__group {
  font-size: var(--fx-text-xs);
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  opacity: var(--fx-ink);
  padding-top: var(--fx-space-5);
  padding-bottom: var(--fx-space-1);
}

.shell__brand {
  font-size: var(--fx-text-lg);
  font-weight: 700;
  letter-spacing: -0.01em;
  white-space: nowrap;
}

/*
 * The scope control: where you are.
 *
 * Context, not a control — so it is transparent at rest and only takes a
 * background on hover, where an affordance belongs. The first cut was a
 * filled, bordered pill with a folder icon, which put three ways of saying
 * "this is a button" next to a page title that says its thing once, and read
 * heavier than anything else in the chrome. The name carries it; the caret
 * says it opens.
 *
 * The header is `bg-dark` in both themes, so these are white alphas rather
 * than tokens: no theme branching, because there is only ever one background.
 */
.scope {
  display: flex;
  align-items: center;
  gap: var(--fx-space-1);
  min-width: 0;
  max-width: 42vw;
}

.scope__btn {
  min-height: 32px;
  padding: 0 var(--fx-space-2);
  border-radius: var(--fx-radius-sm);
  font-size: var(--fx-text-base);
  font-weight: 500;
  letter-spacing: 0;
  min-width: 0;
}

/*  Quasar paints a hover state through a ::before overlay; this is the same
    idea at the weight the toolbar wants. */
.scope__btn:hover {
  background: rgba(255, 255, 255, 0.1);
}

/* Quasar centres button content; this lays it out as name-then-caret. */
.scope__btn :deep(.q-btn__content) {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: var(--fx-space-1);
  min-width: 0;
}

.scope__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scope__caret {
  opacity: 0.55;
  flex: none;
}

.scope__slash {
  opacity: 0.3;
  flex: none;
}

/* -- the menu ------------------------------------------------------------ */
.scope__heading {
  padding: var(--fx-space-3) var(--fx-space-4) var(--fx-space-1);
  font-size: var(--fx-text-xs);
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  opacity: var(--fx-ink-muted);
}

.scope__list {
  min-width: 268px;
  max-width: 340px;
  padding-bottom: var(--fx-space-1);
}

.scope__option {
  min-height: 0;
  padding: var(--fx-space-2) var(--fx-space-4);
}

/*  A left rule rather than Quasar's `active` fill: the app has one accent and
    it belongs to primary actions, not to "you are here". */
.scope__option--current {
  box-shadow: inset 2px 0 0 currentColor;
  background: var(--fx-surface-muted);
}

.scope__option-name {
  font-size: var(--fx-text-base);
  font-weight: 600;
}

.scope__option-note {
  font-size: var(--fx-text-xs);
  opacity: var(--fx-ink-muted);
  /*  One line: a description is a hint here, not the content. */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scope__default {
  margin-left: var(--fx-space-2);
  font-size: var(--fx-text-xs);
  font-weight: 500;
  opacity: var(--fx-ink-faint);
}

.scope__action {
  min-height: 0;
  padding: var(--fx-space-2) var(--fx-space-4);
  font-size: var(--fx-text-sm);
}

.scope__action:first-of-type {
  padding-top: var(--fx-space-3);
}

.scope__action-line {
  display: flex;
  align-items: center;
  gap: var(--fx-space-2);
  opacity: var(--fx-ink);
}

/*  Below the phone breakpoint the brand and the scope cannot both have the
    row. The scope wins: which project you are in changes what every page
    says, and the product name does not. */
@media (max-width: 599px) {
  .shell__brand {
    display: none;
  }

  .scope {
    max-width: 60vw;
  }
}
</style>

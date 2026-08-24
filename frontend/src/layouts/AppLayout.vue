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
          Which workspace you are looking at, and how to change it. Beside the
          account menu because "who am I" and "where am I" are the same
          question asked twice, and a page that shows one namespace's contents
          without saying which one is how people lose work.

          Hidden when there is only the default: a chooser with one choice is
          furniture.
        -->
        <q-btn
          v-if="workspaces.length > 1"
          dense
          flat
          no-caps
          icon="workspaces"
          :label="currentWorkspace?.name ?? 'Workspace'"
          class="shell__workspace"
        >
          <q-menu>
            <q-list style="min-width: 240px">
              <q-item-label header class="q-pb-none">Workspace</q-item-label>
              <q-item
                v-for="workspace in workspaces"
                :key="workspace.id"
                clickable
                v-close-popup
                :active="workspace.id === currentWorkspaceId"
                @click="switchWorkspace(workspace)"
              >
                <q-item-section>
                  <q-item-label>{{ workspace.name }}</q-item-label>
                  <q-item-label caption class="fx-meta">
                    {{ workspace.is_default ? 'the default' : workspace.description }}
                  </q-item-label>
                </q-item-section>
                <q-item-section v-if="workspace.id === currentWorkspaceId" side>
                  <q-icon name="check" size="18px" />
                </q-item-section>
              </q-item>
            </q-list>
          </q-menu>
        </q-btn>

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

import { auth as authApi, workspaces as workspacesApi } from '@/api'
import { getWorkspace, setWorkspace } from '@/api/client'
import type { Workspace } from '@/types'
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
      { name: 'reports', label: 'Reports', icon: 'description' },
    ],
  },
  {
    label: 'Applications',
    items: [{ name: 'applications', label: 'All applications', icon: 'apps' }],
  },
  {
    label: 'System',
    items: [
      { name: 'schedules', label: 'Schedules', icon: 'schedule' },
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

onMounted(() => {
  const stored = localStorage.getItem('flux-dark')
  if (stored !== null) $q.dark.set(stored === 'true')
  loadWorkspaces()
})
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


</style>

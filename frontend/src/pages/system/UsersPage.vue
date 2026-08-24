<template>
  <q-page class="page-shell">
    <PageHeader title="Users" subtitle="Accounts and what each role is allowed to do">
      <template #actions>
        <q-btn no-caps color="primary" unelevated icon="person_add" label="New account" @click="openCreate" />
      </template>
    </PageHeader>

    <SectionCard title="Roles" subtitle="What each role may do" class="q-mb-md" tight>
      <dl class="fx-facts">
        <template v-for="role in ROLES" :key="role.value">
          <dt class="fx-facts__label">{{ role.value }}</dt>
          <dd class="fx-facts__value">{{ role.description }}</dd>
        </template>
      </dl>
    </SectionCard>

    <SectionCard title="Accounts" flush>
      <q-list separator class="fx-list">
        <q-item v-for="account in users" :key="account.id">
          <q-item-section avatar>
            <q-avatar size="32px" :color="roleColour(account.role)" text-color="white">
              {{ account.display_name.slice(0, 1).toUpperCase() }}
            </q-avatar>
          </q-item-section>
          <q-item-section>
            <q-item-label>
              {{ account.display_name }}
              <span class="text-caption" style="opacity: 0.6"> · {{ account.email }}</span>
            </q-item-label>
            <q-item-label caption>
              {{ account.permissions.length }} permissions
              <span v-if="account.last_login_at">
                · last signed in {{ new Date(account.last_login_at).toLocaleString() }}
              </span>
            </q-item-label>
          </q-item-section>
          <q-item-section side>
            <div class="row items-center q-gutter-xs">
              <StatusText v-if="!account.is_active" status="paused" label="disabled" />
              <q-select
                :model-value="account.role"
                :options="ROLES.map((r) => r.value)"
                dense
                outlined
                borderless
                style="min-width: 96px"
                @update:model-value="(role) => changeRole(account, role)"
              />
              <q-btn
                flat
                dense
                :icon="account.is_active ? 'block' : 'check_circle'"
                @click="toggleActive(account)"
              >
                <q-tooltip>{{ account.is_active ? 'Disable' : 'Enable' }}</q-tooltip>
              </q-btn>
              <q-btn flat dense round icon="delete" color="negative" @click="remove(account)" />
            </div>
          </q-item-section>
        </q-item>
      </q-list>
      <EmptyState v-if="!users.length && !loading" message="No accounts" icon="group" />
    </SectionCard>

    <q-dialog v-model="dialog">
      <q-card style="min-width: 440px">
        <q-card-section class="fx-dialog__title">New account</q-card-section>
        <q-card-section class="q-gutter-md">
          <q-input v-model="form.email" label="Email" type="email" dense outlined autofocus />
          <q-input v-model="form.display_name" label="Display name" dense outlined />
          <q-input
            v-model="form.password"
            label="Password"
            type="password"
            dense
            outlined
            hint="at least 8 characters"
          />
          <q-select v-model="form.role" :options="ROLES.map((r) => r.value)" label="Role" dense outlined />
        </q-card-section>
        <q-card-actions align="right">
          <q-btn no-caps flat label="Cancel" v-close-popup />
          <q-btn no-caps unelevated color="primary" label="Create" :loading="saving" @click="create" />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { onMounted, ref } from 'vue'

import { auth as authApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusText from '@/components/StatusText.vue'
import SectionCard from '@/components/SectionCard.vue'
import type { UserAccount } from '@/types'

const $q = useQuasar()

const ROLES = [
  { value: 'admin', description: 'Everything, including accounts and settings' },
  { value: 'editor', description: 'Build and run, but not administer accounts' },
  { value: 'viewer', description: 'Read-only across the whole platform' },
]

const users = ref<UserAccount[]>([])
const loading = ref(false)
const saving = ref(false)
const dialog = ref(false)
const form = ref({ email: '', password: '', display_name: '', role: 'viewer' })

function roleColour(role: string) {
  return { admin: 'accent', editor: 'primary', viewer: 'secondary' }[role] ?? 'grey'
}

async function load() {
  loading.value = true
  try {
    users.value = await authApi.listUsers()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.value = { email: '', password: '', display_name: '', role: 'viewer' }
  dialog.value = true
}

async function create() {
  saving.value = true
  try {
    await authApi.createUser(form.value)
    dialog.value = false
    await load()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    saving.value = false
  }
}

async function changeRole(account: UserAccount, role: string) {
  try {
    await authApi.updateUser(account.id, { role })
    await load()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

async function toggleActive(account: UserAccount) {
  try {
    await authApi.updateUser(account.id, { is_active: !account.is_active })
    await load()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

function remove(account: UserAccount) {
  $q.dialog({
    title: 'Delete account',
    message: `Delete ${account.email}?`,
    cancel: true,
  }).onOk(async () => {
    try {
      await authApi.deleteUser(account.id)
      await load()
    } catch (error) {
      $q.notify({ type: 'negative', message: (error as Error).message })
    }
  })
}

onMounted(load)
</script>

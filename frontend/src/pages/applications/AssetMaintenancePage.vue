<template>
  <q-page class="page-shell">
    <PageHeader
      title="設備預防性維護分析"
      :subtitle="headline"
    >
      <template #actions>
        <q-select
          v-model="policy"
          :options="policyOptions"
          dense
          outlined
          emit-value
          map-options
          class="am__policy"
          label="分析政策"
        />
        <q-btn
          no-caps
          flat
          dense
          icon="fact_check"
          label="分析器"
          @click="explain = true"
        />
        <q-btn
          no-caps
          unelevated
          color="primary"
          icon="autorenew"
          label="重新評估"
          :loading="assessing"
          @click="reassess"
        />
      </template>
    </PageHeader>

    <AsyncSection :pending="loading && !fleet" :error="error" :on-retry="load" :rows="6">
      <template v-if="fleet">
        <!-- what the fleet looks like right now -->
        <div class="row q-col-gutter-md q-mb-md">
          <div class="col-6 col-md-3">
            <StatCard
              label="需要處置"
              :value="fleet.summary.maintenance_required"
              :hint="`共 ${fleet.summary.assets} 台設備`"
              icon="build_circle"
            />
          </div>
          <div class="col-6 col-md-3">
            <StatCard
              label="平均健康分數"
              :value="fleet.summary.mean_health ?? '—'"
              :hint="`最低 ${fleet.summary.worst_health ?? '—'}`"
              icon="monitor_heart"
            />
          </div>
          <div class="col-6 col-md-3">
            <StatCard
              label="高風險以上"
              :value="highRisk"
              :hint="riskHint"
              icon="warning_amber"
            />
          </div>
          <div class="col-6 col-md-3">
            <StatCard
              label="資料可疑"
              :value="fleet.summary.suspect_data"
              hint="先確認儀器再判定設備"
              icon="sensors_off"
            />
          </div>
        </div>

        <div class="row q-col-gutter-md">
          <!-- the fleet -->
          <div class="col-12 col-lg-5">
            <SectionCard
              title="機隊"
              :subtitle="`${filtered.length} / ${fleet.total} 台，依處置優先順序排列`"
              flush
            >
              <template #actions>
                <q-toggle v-model="requiredOnly" dense label="只看需處置" />
              </template>
              <div class="am__filters">
                <SearchField v-model="search" placeholder="搜尋設備編號或名稱" />
                <q-select
                  v-model="site"
                  :options="siteOptions"
                  dense
                  outlined
                  emit-value
                  map-options
                  clearable
                  label="廠區"
                  class="am__filter"
                />
                <q-select
                  v-model="criticality"
                  :options="criticalityOptions"
                  dense
                  outlined
                  emit-value
                  map-options
                  clearable
                  label="重要程度"
                  class="am__filter"
                />
              </div>

              <q-list separator class="fx-list am__list">
                <q-item
                  v-for="asset in filtered"
                  :key="asset.asset_id"
                  clickable
                  :active="asset.asset_id === selected"
                  active-class="am__row--active"
                  @click="selected = asset.asset_id"
                >
                  <q-item-section>
                    <q-item-label class="am__row-title">
                      <span class="mono">{{ asset.asset_id }}</span>
                      <span class="am__row-name">{{ asset.asset_name }}</span>
                    </q-item-label>
                    <q-item-label caption class="am__row-meta">
                      <StatusText :status="healthTone(asset.health_status)" :label="asset.health_status" />
                      <span>健康 {{ asset.health_score ?? '—' }}</span>
                      <span>風險 {{ asset.risk_level ?? '—' }}</span>
                      <span class="am__row-crit">{{ criticalityLabel(asset.criticality) }}</span>
                    </q-item-label>
                  </q-item-section>
                  <q-item-section side>
                    <span class="am__priority" :class="`am__priority--${asset.priority.toLowerCase()}`">
                      {{ priorityLabel(asset.priority) }}
                    </span>
                  </q-item-section>
                </q-item>
                <q-item v-if="!filtered.length">
                  <q-item-section class="fx-meta">沒有符合條件的設備</q-item-section>
                </q-item>
              </q-list>
            </SectionCard>
          </div>

          <!-- the selected asset -->
          <div class="col-12 col-lg-7">
            <AsyncSection :pending="detailLoading && !detail" :error="detailError" :on-retry="loadDetail">
              <SectionCard
                v-if="detail?.decision"
                :title="String(detail.asset.asset_name ?? selected)"
                :subtitle="assetSubtitle"
              >
                <template #actions>
                  <span class="fx-tag">{{ detail.policy_label ?? detail.policy }}</span>
                </template>

                <div class="am__verdict">
                  <div class="am__verdict-main">
                    <div class="am__score" :class="`am__score--${healthTone(detail.decision.health_status)}`">
                      {{ detail.decision.health_score ?? '—' }}
                    </div>
                    <div class="am__verdict-text">
                      <div class="am__verdict-status">{{ detail.decision.health_status }}</div>
                      <div class="fx-meta">
                        風險 {{ detail.decision.risk_level }} ·
                        可能性 {{ detail.decision.likelihood }} ×
                        後果 {{ criticalityLabel(detail.decision.consequence) }}
                      </div>
                      <div class="fx-meta">
                        信心 {{ pct(detail.decision.confidence) }} ·
                        證據覆蓋率 {{ pct(detail.decision.health_coverage) }}
                      </div>
                    </div>
                  </div>
                  <div class="am__verdict-action">
                    <div class="am__action-label">
                      {{ detail.decision.maintenance_required ? '建議處置' : '維持觀察' }}
                    </div>
                    <div class="am__action-text">{{ detail.decision.recommended_action }}</div>
                    <div class="fx-meta">{{ windowText }}</div>
                  </div>
                </div>

                <q-tabs v-model="tab" dense no-caps align="left" class="am__tabs">
                  <q-tab name="why" label="判斷依據" />
                  <q-tab name="telemetry" label="遙測" />
                  <q-tab name="health" label="健康組成" />
                  <q-tab name="timeline" label="時間軸" />
                  <q-tab name="profile" label="設備資料" />
                </q-tabs>
                <q-separator />

                <q-tab-panels v-model="tab" animated class="am__panels">
                  <!-- why -->
                  <q-tab-panel name="why" class="am__panel">
                    <p class="fx-meta am__lead">
                      每一項結論都來自某一個分析器與具體數字。負貢獻是反對採取行動的證據——
                      一支卡住的感測器是「不要相信這個讀值」的理由，不是「情況更嚴重」的理由。
                    </p>
                    <ul class="am__evidence">
                      <li v-for="(item, index) in detail.evidence ?? []" :key="index">
                        <div class="am__evidence-head">
                          <span class="am__analyzer">{{ analyzerLabel(item.analyzer) }}</span>
                          <span
                            class="am__contribution"
                            :class="item.contribution >= 0 ? 'am__contribution--up' : 'am__contribution--down'"
                          >
                            {{ item.contribution >= 0 ? '+' : '' }}{{ item.contribution.toFixed(1) }}
                          </span>
                          <span class="fx-meta">信心 {{ pct(item.confidence) }}</span>
                        </div>
                        <div class="am__evidence-text">{{ item.statement }}</div>
                        <div v-if="item.action" class="fx-meta am__evidence-action">
                          建議：{{ item.action }}
                        </div>
                      </li>
                      <li v-if="!(detail.evidence ?? []).length" class="fx-meta">
                        這個政策下沒有分析器提出證據。
                      </li>
                    </ul>
                  </q-tab-panel>

                  <!-- telemetry -->
                  <q-tab-panel name="telemetry" class="am__panel">
                    <AsyncSection :pending="seriesLoading && !series" :error="seriesError" :on-retry="loadSeries">
                      <template v-if="series">
                        <div class="am__params">
                          <q-btn
                            v-for="option in series.available"
                            :key="option.parameter"
                            no-caps
                            dense
                            :flat="option.parameter !== parameter"
                            :unelevated="option.parameter === parameter"
                            :color="option.parameter === parameter ? 'primary' : undefined"
                            :label="option.label"
                            @click="parameter = option.parameter"
                          />
                        </div>
                        <ConditionChart
                          :points="series.points"
                          :events="series.events"
                          :title="`${series.parameter_label}（${series.unit}）`"
                          :unit="series.unit"
                          :direction="series.direction"
                          subtitle="界線隨當日負載與廠房溫度移動，因此健康的設備在任何工況下都貼著應有值"
                        />
                      </template>
                    </AsyncSection>

                    <div class="am__measures fx-scroll-x">
                      <table class="am__table">
                        <thead>
                          <tr>
                            <th>量測</th>
                            <th class="num">實測</th>
                            <th class="num">應有</th>
                            <th class="num">門檻進度</th>
                            <th class="num">每日趨勢</th>
                            <th>狀態</th>
                            <th class="num">資料品質</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="item in detail.measurements ?? []" :key="item.parameter">
                            <td>{{ item.parameter_label }}</td>
                            <td class="num">{{ num(item.value) }} {{ item.unit }}</td>
                            <td class="num">{{ num(item.expected) }}</td>
                            <td class="num">{{ num(item.limit_progress_pct) }}%</td>
                            <td class="num">{{ num(item.trend_per_day) }}</td>
                            <td>
                              <StatusText :status="statusTone(item.status)" :label="statusLabel(item.status)" />
                            </td>
                            <td class="num">{{ num(item.quality_score) }}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </q-tab-panel>

                  <!-- health -->
                  <q-tab-panel name="health" class="am__panel">
                    <p class="fx-meta am__lead">
                      健康分數是加權綜合，而不是單一公式。每個成分說明自己缺席時代表什麼，
                      分數旁邊也標明實際能算到多少證據——「72 分」和「72 分，來自三分之二的證據」
                      是兩個不同的答案。
                    </p>
                    <ul class="am__components">
                      <li v-for="component in detail.health?.components ?? []" :key="component.name">
                        <div class="am__component-head">
                          <span>{{ component.name }}</span>
                          <span class="am__component-score">{{ component.score ?? '—' }}</span>
                        </div>
                        <div class="am__meter" aria-hidden="true">
                          <span :style="{ width: `${Math.max(0, Math.min(100, component.score ?? 0))}%` }" />
                        </div>
                        <div class="fx-meta">
                          權重 {{ component.weight }} · {{ component.reason }}
                          <template v-if="component.description"> — {{ component.description }}</template>
                        </div>
                      </li>
                    </ul>
                    <FactList :facts="qualityFacts" />
                  </q-tab-panel>

                  <!-- timeline -->
                  <q-tab-panel name="timeline" class="am__panel">
                    <ol class="am__timeline">
                      <li v-for="(entry, index) in detail.timeline ?? []" :key="index" :class="`am__t--${entry.kind}`">
                        <div class="am__t-when mono">{{ entry.at }}</div>
                        <div class="am__t-body">
                          <div class="am__t-title">{{ entry.title }}</div>
                          <div v-if="entry.detail" class="fx-meta">{{ entry.detail }}</div>
                          <div v-if="entry.kind === 'assessment'" class="am__t-reasons">
                            <div v-for="(reason, at) in (entry.extra.reasons as string[]) ?? []" :key="at" class="fx-meta">
                              • {{ reason }}
                            </div>
                          </div>
                        </div>
                      </li>
                    </ol>
                  </q-tab-panel>

                  <!-- profile -->
                  <q-tab-panel name="profile" class="am__panel">
                    <FactList :facts="profileFacts" />
                    <h3 class="am__subhead">適用保養政策</h3>
                    <ul class="am__policies">
                      <li v-for="(item, index) in detail.policies ?? []" :key="index" class="fx-meta">
                        {{ item.task }} —
                        <template v-if="item.interval_hours">每 {{ item.interval_hours }} 運轉小時</template>
                        <template v-else-if="item.interval_days">每 {{ item.interval_days }} 天</template>
                        （{{ item.source }}）
                      </li>
                    </ul>
                  </q-tab-panel>
                </q-tab-panels>
              </SectionCard>

              <SectionCard v-else-if="detail" title="無法評估" :subtitle="detail.message ?? ''">
                <p class="fx-meta">此設備在這個日期之前沒有可用的量測資料。</p>
              </SectionCard>
            </AsyncSection>
          </div>
        </div>
      </template>
    </AsyncSection>

    <q-dialog v-model="explain">
      <q-card class="am__dialog">
        <q-card-section>
          <div class="text-subtitle1">分析器與政策</div>
          <p class="fx-meta">
            政策不是不同的程式，而是同一個引擎啟用不同的分析器組合。這也是它們可以被放進
            同一個實驗互相比較的原因。
          </p>
        </q-card-section>
        <q-separator />
        <q-card-section class="am__dialog-body">
          <h3 class="am__subhead">分析器</h3>
          <ul class="am__analyzers">
            <li v-for="item in catalogue?.analyzers ?? []" :key="item.key">
              <b>{{ item.label }}</b>
              <span class="fx-meta"> — {{ item.description }}</span>
            </li>
          </ul>
          <h3 class="am__subhead">政策</h3>
          <ul class="am__analyzers">
            <li v-for="item in catalogue?.policies ?? []" :key="item.key">
              <b>{{ item.label }}</b>
              <span class="fx-meta"> — {{ item.description }}</span>
            </li>
          </ul>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn v-close-popup flat no-caps label="關閉" />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref, watch } from 'vue'

import { assetMaintenance } from '@/api'
import AsyncSection from '@/components/AsyncSection.vue'
import ConditionChart from '@/components/ConditionChart.vue'
import FactList, { type Fact } from '@/components/FactList.vue'
import PageHeader from '@/components/PageHeader.vue'
import SearchField from '@/components/SearchField.vue'
import SectionCard from '@/components/SectionCard.vue'
import StatCard from '@/components/StatCard.vue'
import StatusText from '@/components/StatusText.vue'
import { useUrlSelection } from '@/composables/useUrlSelection'
import type {
  MaintenanceAssetDetail,
  MaintenanceCatalogue,
  MaintenanceFleet,
  MaintenanceSeries,
} from '@/types'

/**
 * The maintenance application's own page.
 *
 * Three panes rather than a dashboard, because the questions arrive in a fixed
 * order: which of forty assets needs attention, why does the system say so,
 * and what does the measurement behind that actually look like. A grid of
 * charts answers the first question badly and the other two not at all.
 *
 * The "why" tab is the point of the whole application. A maintenance system
 * that cannot be argued with does not get used twice.
 */
const $q = useQuasar()

const fleet = ref<MaintenanceFleet | null>(null)
const detail = ref<MaintenanceAssetDetail | null>(null)
const series = ref<MaintenanceSeries | null>(null)
const catalogue = ref<MaintenanceCatalogue | null>(null)

const loading = ref(false)
const detailLoading = ref(false)
const seriesLoading = ref(false)
const assessing = ref(false)
const error = ref<string | null>(null)
const detailError = ref<string | null>(null)
const seriesError = ref<string | null>(null)

const policy = ref('full_risk_adjusted')
const search = ref('')
const site = ref<string | null>(null)
const criticality = ref<string | null>(null)
const requiredOnly = ref(false)
const parameter = ref<string>('')
const tab = ref('why')
const explain = ref(false)

const { selected, settle } = useUrlSelection('asset')

const CRITICALITY: Record<string, string> = {
  critical: '關鍵',
  high: '高',
  medium: '中',
  low: '低',
}
const PRIORITY: Record<string, string> = {
  IMMEDIATE: '立即',
  HIGH: '高',
  MEDIUM: '中',
  LOW: '低',
  NONE: '—',
}
const STATUS: Record<string, string> = {
  normal: '正常',
  warning: '警戒',
  critical: '嚴重',
  emergency: '緊急',
}

const policyOptions = computed(() =>
  (catalogue.value?.policies ?? []).map((item) => ({ label: item.label, value: item.key })),
)
const siteOptions = computed(() =>
  (fleet.value?.sites ?? []).map((value) => ({ label: value, value })),
)
const criticalityOptions = Object.entries(CRITICALITY).map(([value, label]) => ({ label, value }))

const headline = computed(() => {
  if (!fleet.value) return '以門檻、統計、趨勢、失效型態、保養政策與資料品質合成的維護決策'
  return `評估日 ${fleet.value.as_of} · ${fleet.value.policy_label} · ${fleet.value.total} 台設備`
})

const highRisk = computed(() => {
  const risks = fleet.value?.summary.by_risk ?? {}
  return (risks.HIGH ?? 0) + (risks.CRITICAL ?? 0)
})
const riskHint = computed(() => {
  const risks = fleet.value?.summary.by_risk ?? {}
  return `CRITICAL ${risks.CRITICAL ?? 0} · HIGH ${risks.HIGH ?? 0}`
})

/**
 * Filtered in the browser rather than by refetching: the fleet is forty rows
 * and a round trip per keystroke would be slower and no more correct.
 */
const filtered = computed(() => {
  const rows = fleet.value?.assets ?? []
  const needle = search.value.trim().toLowerCase()
  return rows.filter(
    (row) =>
      (!needle || `${row.asset_id} ${row.asset_name}`.toLowerCase().includes(needle)) &&
      (!site.value || row.site_id === site.value) &&
      (!criticality.value || row.criticality === criticality.value) &&
      (!requiredOnly.value || row.maintenance_required),
  )
})

const assetSubtitle = computed(() => {
  const asset = detail.value?.asset ?? {}
  return [asset.asset_type_label, asset.location, asset.manufacturer, asset.model_number]
    .filter(Boolean)
    .join(' · ')
})

const windowText = computed(() => {
  const window = detail.value?.window
  if (!window) return ''
  const basis: Record<string, string> = {
    calculated: '已越線／可直接計算',
    estimated: '依趨勢外推',
    inferred: '僅能推斷方向',
    unknown: '無法判斷',
  }
  const range = window.start && window.end ? `${window.start} – ${window.end}` : '無明確日期'
  return `維修窗口 ${range}（${basis[window.basis] ?? window.basis}）：${window.reason}`
})

const profileFacts = computed<Fact[]>(() => {
  const asset = (detail.value?.asset ?? {}) as Record<string, unknown>
  const decision = detail.value?.decision
  return [
    { label: '設備編號', value: asset.asset_id },
    { label: '類型', value: asset.asset_type_label },
    { label: '製造商', value: `${asset.manufacturer ?? ''} ${asset.model_number ?? ''}`.trim() },
    { label: '廠區位置', value: asset.location },
    { label: '啟用日期', value: asset.commission_date },
    { label: '設備年齡（年）', value: asset.age_years },
    { label: '設計壽命（年）', value: asset.design_life_years },
    { label: 'MTBF（小時）', value: asset.mtbf_hours },
    { label: '重要程度', value: CRITICALITY[String(asset.criticality)] ?? asset.criticality },
    { label: '運轉型態', value: asset.duty_pattern },
    { label: '累積運轉時數', value: decision?.runtime_hours },
    { label: '保養週期消耗', value: decision?.interval_usage_pct },
    { label: '觀測天數', value: decision?.observed_days },
    { label: '負責單位', value: asset.owner },
  ]
})

const qualityFacts = computed<Fact[]>(() => {
  const quality = (detail.value?.data_quality ?? {}) as Record<string, unknown>
  return [
    { label: '最低品質分數', value: quality.min_score },
    { label: '平均品質分數', value: quality.mean_score },
    { label: '品質判定', value: quality.flag },
    { label: '觀測天數', value: quality.observed_days },
    { label: '量測項目數', value: quality.measurements },
    { label: '可疑量測', value: (quality.suspect as string[] | undefined)?.join('、') },
  ]
})

function analyzerLabel(key: string): string {
  return catalogue.value?.analyzers.find((item) => item.key === key)?.label ?? key
}
function criticalityLabel(value: string | null | undefined): string {
  return CRITICALITY[String(value)] ?? String(value ?? '—')
}
function priorityLabel(value: string): string {
  return PRIORITY[value] ?? value
}
function statusLabel(value: string): string {
  return STATUS[value] ?? value
}
/** Map a domain word onto the platform's four status tones. */
function healthTone(status: string | null | undefined): string {
  if (status === 'HEALTHY') return 'succeeded'
  if (status === 'WATCH') return 'running'
  if (status === 'DEGRADED') return 'pending'
  if (status === 'POOR' || status === 'CRITICAL') return 'failed'
  return 'neutral'
}
function statusTone(status: string): string {
  if (status === 'normal') return 'succeeded'
  if (status === 'warning') return 'pending'
  return 'failed'
}
function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`
}
function num(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const size = Math.abs(value)
  return size >= 100 ? value.toFixed(0) : size >= 1 ? value.toFixed(2) : value.toFixed(3)
}

async function load() {
  loading.value = true
  error.value = null
  try {
    if (!catalogue.value) {
      catalogue.value = await assetMaintenance.catalogue()
      if (catalogue.value.default_policy) policy.value = catalogue.value.default_policy
    }
    fleet.value = await assetMaintenance.fleet({ policy: policy.value })
    const chosen = settle(fleet.value.assets.map((asset) => asset.asset_id))
    if (chosen) await loadDetail()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    loading.value = false
  }
}

async function loadDetail() {
  if (!selected.value) return
  detailLoading.value = true
  detailError.value = null
  try {
    detail.value = await assetMaintenance.asset(selected.value, { policy: policy.value })
    //  The measurement the assessment is about, not the first alphabetically.
    const ranked = [...(detail.value.measurements ?? [])].sort(
      (a, b) => (b.limit_progress_pct ?? -999) - (a.limit_progress_pct ?? -999),
    )
    parameter.value = ranked[0]?.parameter ?? ''
    await loadSeries()
  } catch (exc) {
    detailError.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    detailLoading.value = false
  }
}

async function loadSeries() {
  if (!selected.value) return
  seriesLoading.value = true
  seriesError.value = null
  try {
    series.value = await assetMaintenance.series(selected.value, {
      parameter: parameter.value || undefined,
      days: 90,
    })
  } catch (exc) {
    seriesError.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    seriesLoading.value = false
  }
}

async function reassess() {
  assessing.value = true
  try {
    const answer = await assetMaintenance.assess({ policy: policy.value })
    $q.notify({
      type: answer.status === 'succeeded' ? 'positive' : 'warning',
      message: `評估已記錄為 Execution ${answer.execution_id}`,
      caption: `需處置 ${answer.metrics.maintenance_required ?? '—'} 台`,
    })
    await load()
  } catch (exc) {
    $q.notify({ type: 'negative', message: exc instanceof Error ? exc.message : String(exc) })
  } finally {
    assessing.value = false
  }
}

watch(selected, () => {
  void loadDetail()
})
watch(parameter, () => {
  void loadSeries()
})
watch(policy, () => {
  void load()
})

onMounted(load)
</script>

<style scoped>
.am__policy {
  min-width: 190px;
}

.am__filters {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fx-space-2);
  padding: var(--fx-space-3);
  border-bottom: 1px solid var(--fx-border);
}

.am__filter {
  min-width: 130px;
  flex: 1 1 130px;
}

.am__list {
  max-height: 620px;
  overflow-y: auto;
}

.am__row--active {
  background: var(--fx-surface-inset);
}

.am__row-title {
  display: flex;
  gap: var(--fx-space-2);
  align-items: baseline;
  min-width: 0;
  overflow-wrap: anywhere;
}

.am__row-name {
  font-weight: 500;
}

.am__row-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fx-space-3);
  align-items: center;
}

.am__row-crit {
  opacity: var(--fx-ink-faint);
}

.am__priority {
  font-size: var(--fx-text-xs);
  font-weight: 600;
  white-space: nowrap;
}

.am__priority--immediate {
  color: var(--fx-bad);
}

.am__priority--high {
  color: var(--fx-wait);
}

.am__priority--none {
  opacity: var(--fx-ink-faint);
}

.am__verdict {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fx-space-5);
  align-items: flex-start;
  margin-bottom: var(--fx-space-4);
  min-width: 0;
}

.am__verdict-main {
  display: flex;
  gap: var(--fx-space-3);
  align-items: center;
  min-width: 0;
}

.am__score {
  font-size: var(--fx-text-3xl);
  font-weight: 700;
  line-height: 1;
  min-width: 72px;
}

.am__score--succeeded {
  color: var(--fx-ok);
}

.am__score--failed {
  color: var(--fx-bad);
}

.am__score--pending {
  color: var(--fx-wait);
}

.am__score--running {
  color: var(--fx-run);
}

.am__verdict-text {
  min-width: 0;
}

.am__verdict-status {
  font-weight: 600;
}

.am__verdict-action {
  flex: 1 1 260px;
  min-width: 0;
  border-left: 2px solid var(--fx-border);
  padding-left: var(--fx-space-4);
}

.am__action-label {
  font-size: var(--fx-text-xs);
  text-transform: uppercase;
  opacity: var(--fx-ink-muted);
}

.am__action-text {
  font-weight: 600;
  overflow-wrap: anywhere;
}

.am__tabs {
  margin-top: var(--fx-space-2);
}

/*
 * Five tabs do not fit in 375px, and Quasar's own answer is to clip them and
 * add arrows — which `check:layout` correctly reports as content escaping its
 * box. Letting the strip scroll is the project's pattern for wide content
 * (`.fx-scroll-x`), and it is the right one here: a tab you can reach by
 * swiping is reachable, and one behind an arrow that only appears on hover is
 * not.
 */
.am__tabs :deep(.q-tabs__content) {
  overflow-x: auto;
  overscroll-behavior-x: contain;
}

.am__tabs :deep(.q-tabs__content)::-webkit-scrollbar {
  height: 4px;
}

.am__panels {
  background: transparent;
}

.am__panel {
  padding: var(--fx-space-4) 0 0;
  min-width: 0;
}

.am__lead {
  margin: 0 0 var(--fx-space-3);
}

.am__evidence,
.am__components,
.am__analyzers,
.am__policies {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--fx-space-3);
}

.am__evidence li {
  border-left: 2px solid var(--fx-border);
  padding-left: var(--fx-space-3);
  min-width: 0;
}

.am__evidence-head {
  display: flex;
  gap: var(--fx-space-2);
  align-items: baseline;
  flex-wrap: wrap;
}

.am__analyzer {
  font-size: var(--fx-text-xs);
  font-weight: 600;
  text-transform: uppercase;
  opacity: var(--fx-ink-muted);
}

.am__contribution {
  font-family: var(--fx-mono);
  font-weight: 600;
}

.am__contribution--up {
  color: var(--fx-bad);
}

.am__contribution--down {
  color: var(--fx-ok);
}

.am__evidence-text {
  overflow-wrap: anywhere;
}

.am__evidence-action {
  margin-top: var(--fx-space-1);
}

.am__component-head {
  display: flex;
  justify-content: space-between;
  gap: var(--fx-space-2);
}

.am__component-score {
  font-family: var(--fx-mono);
}

.am__meter {
  height: 4px;
  border-radius: 2px;
  background: var(--fx-surface-inset);
  margin: var(--fx-space-1) 0;
  overflow: hidden;
}

.am__meter span {
  display: block;
  height: 100%;
  background: var(--fx-run);
}

.am__params {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fx-space-1);
  margin-bottom: var(--fx-space-3);
}

.am__measures {
  margin-top: var(--fx-space-4);
}

.am__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fx-text-sm);
  min-width: 560px;
}

.am__table th,
.am__table td {
  text-align: left;
  padding: var(--fx-space-2);
  border-bottom: 1px solid var(--fx-border);
  white-space: nowrap;
}

.am__table th {
  font-weight: 600;
  opacity: var(--fx-ink-muted);
}

.am__table .num {
  text-align: right;
  font-family: var(--fx-mono);
}

.am__timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--fx-space-3);
}

.am__timeline li {
  display: flex;
  gap: var(--fx-space-3);
  border-left: 2px solid var(--fx-border);
  padding-left: var(--fx-space-3);
  min-width: 0;
}

.am__t--failure {
  border-left-color: var(--fx-bad);
}

.am__t--corrective {
  border-left-color: var(--fx-wait);
}

.am__t--assessment {
  border-left-color: var(--fx-run);
}

.am__t-when {
  flex: 0 0 82px;
  font-size: var(--fx-text-xs);
  opacity: var(--fx-ink-muted);
}

.am__t-body {
  min-width: 0;
  overflow-wrap: anywhere;
}

.am__t-title {
  font-weight: 500;
}

.am__t-reasons {
  margin-top: var(--fx-space-1);
}

.am__subhead {
  font-size: var(--fx-text-base);
  font-weight: 600;
  margin: var(--fx-space-4) 0 var(--fx-space-2);
}

.am__dialog {
  max-width: 620px;
  width: 90vw;
}

.am__dialog-body {
  max-height: 60vh;
  overflow-y: auto;
}
</style>

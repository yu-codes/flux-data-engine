import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

/**
 * Keeps a master/detail selection in the URL.
 *
 * Six pages in this app are master/detail, and every one of them held the
 * selected id in a local ref. That is invisible until you try to do the things
 * people actually do with a web application: send someone a link to the
 * pipeline that failed, keep a tab open on a report, or press reload. All of
 * them lost the selection, and none of them could be shared at all.
 *
 * The id goes in the query string rather than the path because these pages are
 * a list *and* a detail view at once — the list is not a separate destination
 * you navigate away from, so a path segment would misdescribe the structure.
 *
 * `replace` is deliberate: choosing between items in a list is browsing within
 * one view, and pushing every click would turn Back into an undo button for
 * selection rather than a way out of the page.
 */
export function useUrlSelection(key = 'id') {
  const route = useRoute()
  const router = useRouter()
  const selected = ref<string>((route.query[key] as string) ?? '')

  watch(selected, (value) => {
    const current = (route.query[key] as string) ?? ''
    if (current === value) return
    const query = { ...route.query }
    if (value) query[key] = value
    else delete query[key]
    void router.replace({ query })
  })

  //  Someone may edit the address bar, or arrive through browser history.
  watch(
    () => route.query[key],
    (value) => {
      const next = (value as string) ?? ''
      if (next !== selected.value) selected.value = next
    },
  )

  /**
   * Resolve the selection once a list has loaded: honour a valid id from the
   * URL, otherwise fall back to the first row so the page is never blank.
   */
  function settle(ids: string[]) {
    if (selected.value && ids.includes(selected.value)) return selected.value
    selected.value = ids[0] ?? ''
    return selected.value
  }

  onMounted(() => {
    const initial = (route.query[key] as string) ?? ''
    if (initial) selected.value = initial
  })

  return { selected, settle }
}

import { computed, ref, type Ref } from 'vue'

/**
 * Narrow a loaded list by a search term.
 *
 * Filtering happens in the browser because these collections are already
 * fetched in full and a round trip per keystroke would make a glance feel like
 * a query. Where a collection can genuinely outgrow one response — datasets and
 * models — the API takes a `search` parameter and the page uses that instead;
 * the search box is the same either way, which is the part a person notices.
 */
export function useListFilter<T>(
  items: Ref<T[]>,
  fields: (item: T) => (string | null | undefined)[],
) {
  const term = ref('')

  const filtered = computed(() => {
    const needle = term.value.trim().toLowerCase()
    if (!needle) return items.value
    return items.value.filter((item) =>
      fields(item).some((value) => (value ?? '').toLowerCase().includes(needle)),
    )
  })

  /** "12 of 48" while filtering, plain "48" otherwise — never a bare count. */
  const summary = computed(() => {
    const total = items.value.length
    const shown = filtered.value.length
    if (!term.value.trim()) return `${total}`
    return `${shown} of ${total}`
  })

  const isFiltering = computed(() => term.value.trim().length > 0)

  return { term, filtered, summary, isFiltering }
}

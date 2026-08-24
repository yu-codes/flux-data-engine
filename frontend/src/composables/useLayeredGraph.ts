import { computed, type Ref } from 'vue'

/**
 * Laying out a directed graph in columns, without a graph library.
 *
 * Depth from a node with no parent decides the column, order within the column
 * decides the row. That is enough for the shapes both graphs in this app can
 * take - a pipeline is a tree that widens, a lineage walk is a tree that
 * narrows - and it keeps the drawing in SVG we own rather than in a dependency.
 *
 * Extracted from `PipelineGraph.vue` when the lineage view needed the same
 * arrangement. Two copies of a layout drift into two slightly different
 * pictures of the same idea, and the second one is always the one that looks
 * wrong.
 */

export interface LaidOutNode<T> {
  node: T
  x: number
  y: number
}

export interface LaidOutEdge {
  path: string
}

export interface LayeredGraphOptions {
  nodeWidth?: number
  nodeHeight?: number
  gapX?: number
  gapY?: number
}

export function useLayeredGraph<T>(
  nodes: Ref<T[]>,
  edges: Ref<{ from: string; to: string }[]>,
  keyOf: (node: T) => string,
  options: LayeredGraphOptions = {},
) {
  const NODE_W = options.nodeWidth ?? 150
  const NODE_H = options.nodeHeight ?? 48
  const GAP_X = options.gapX ?? 66
  const GAP_Y = options.gapY ?? 22

  const depths = computed(() => {
    const parent = new Map<string, string>()
    for (const edge of edges.value) parent.set(edge.to, edge.from)

    const depth = new Map<string, number>()
    const resolve = (id: string, seen = new Set<string>()): number => {
      if (depth.has(id)) return depth.get(id)!
      //  A cycle would otherwise recurse for ever. The graphs here are acyclic
      //  by construction, which is exactly the assumption worth not trusting.
      if (seen.has(id)) return 0
      seen.add(id)
      const from = parent.get(id)
      const value = from ? resolve(from, seen) + 1 : 0
      depth.set(id, value)
      return value
    }
    for (const node of nodes.value) resolve(keyOf(node))
    return depth
  })

  const laidOutNodes = computed<LaidOutNode<T>[]>(() => {
    const rows = new Map<number, number>()
    return nodes.value.map((node) => {
      const column = depths.value.get(keyOf(node)) ?? 0
      const row = rows.get(column) ?? 0
      rows.set(column, row + 1)
      return {
        node,
        x: 10 + column * (NODE_W + GAP_X),
        y: 10 + row * (NODE_H + GAP_Y),
      }
    })
  })

  const positions = computed(() =>
    Object.fromEntries(laidOutNodes.value.map((laid) => [keyOf(laid.node), laid])),
  )

  const laidOutEdges = computed<LaidOutEdge[]>(() =>
    edges.value.flatMap((edge) => {
      const from = positions.value[edge.from]
      const to = positions.value[edge.to]
      if (!from || !to) return []
      const x1 = from.x + NODE_W
      const y1 = from.y + NODE_H / 2
      const x2 = to.x
      const y2 = to.y + NODE_H / 2
      const mid = (x1 + x2) / 2
      return [{ path: `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}` }]
    }),
  )

  const width = computed(
    () => Math.max(...laidOutNodes.value.map((n) => n.x + NODE_W), NODE_W) + 20,
  )
  const height = computed(
    () => Math.max(...laidOutNodes.value.map((n) => n.y + NODE_H), NODE_H) + 20,
  )

  return { laidOutNodes, laidOutEdges, width, height, NODE_W, NODE_H }
}

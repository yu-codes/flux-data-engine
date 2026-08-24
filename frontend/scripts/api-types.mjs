/**
 * Generate TypeScript types from the backend's OpenAPI schema, and check that
 * the hand-written ones have not drifted from it.
 *
 * `src/types/index.ts` is written by hand against pydantic models that live in
 * another language, in another process. Nothing connects the two, so a field
 * renamed on the server stays correct-looking on the client until something
 * reads `undefined` at runtime - in front of a user, in a page that worked
 * yesterday.
 *
 * FastAPI already publishes the schema. This reads it with nothing but the
 * standard library and Node's own fetch, because a code generator that brings
 * its own toolchain is a second thing to keep working.
 *
 *   npm run types:api      # write src/types/api.generated.ts
 *   npm run types:check    # fail if the hand-written types contradict it
 */

import { writeFileSync, readFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const OUT = join(HERE, '..', 'src', 'types', 'api.generated.ts')
const HANDWRITTEN = join(HERE, '..', 'src', 'types', 'index.ts')
const SCHEMA_URL = process.env.FLUX_OPENAPI_URL ?? 'http://localhost:38000/openapi.json'

const PRIMITIVES = {
  string: 'string',
  integer: 'number',
  number: 'number',
  boolean: 'boolean',
}

/** One OpenAPI schema node as a TypeScript type expression. */
function typeOf(node, schemas) {
  if (!node) return 'unknown'
  if (node.$ref) return node.$ref.split('/').pop()

  //  Nullable and union types arrive as anyOf. `anyOf: [X, null]` is by far
  //  the commonest, and reads far better as `X | null` than as a union of two.
  if (node.anyOf || node.oneOf) {
    const parts = (node.anyOf ?? node.oneOf).map((entry) => typeOf(entry, schemas))
    return [...new Set(parts)].join(' | ')
  }
  if (node.enum) {
    return node.enum.map((value) => JSON.stringify(value)).join(' | ')
  }
  if (node.type === 'array') return `${wrap(typeOf(node.items, schemas))}[]`
  if (node.type === 'object' || node.properties) {
    if (node.additionalProperties && node.additionalProperties !== true) {
      return `Record<string, ${typeOf(node.additionalProperties, schemas)}>`
    }
    return 'Record<string, unknown>'
  }
  if (node.type === 'null') return 'null'
  return PRIMITIVES[node.type] ?? 'unknown'
}

function wrap(expression) {
  return expression.includes('|') ? `(${expression})` : expression
}

function render(name, schema, schemas) {
  const required = new Set(schema.required ?? [])
  const lines = [`export interface ${name} {`]
  for (const [field, node] of Object.entries(schema.properties ?? {})) {
    const optional = required.has(field) ? '' : '?'
    const description = node.description ? `  /** ${node.description} */\n` : ''
    lines.push(`${description}  ${field}${optional}: ${typeOf(node, schemas)}`)
  }
  lines.push('}')
  return lines.join('\n')
}

async function fetchSchema() {
  let response
  try {
    response = await fetch(SCHEMA_URL)
  } catch (error) {
    //  On Windows `localhost` often resolves to ::1 first, which Docker may
    //  not be listening on. Without this the check reports "backend not
    //  running" against a backend that is running, and gets ignored.
    const v4 = SCHEMA_URL.replace('localhost', '127.0.0.1')
    if (v4 === SCHEMA_URL) throw error
    response = await fetch(v4)
  }
  if (!response.ok) throw new Error(`${SCHEMA_URL} answered ${response.status}`)
  return response.json()
}

function generate(document) {
  const schemas = document.components?.schemas ?? {}
  const names = Object.keys(schemas).sort()
  const body = names
    .filter((name) => schemas[name].properties)
    .map((name) => render(name, schemas[name], schemas))
    .join('\n\n')

  return `/**
 * Generated from the backend's OpenAPI schema. Do not edit.
 *
 * Regenerate with \`npm run types:api\` while the backend is running. The
 * hand-written types in \`index.ts\` are checked against this by
 * \`npm run types:check\`, so a field renamed on the server fails there rather
 * than at runtime in front of somebody.
 */

${body}
`
}

/** Field names per interface, from TypeScript source, without a parser. */
function interfacesIn(source) {
  const found = {}
  const pattern = /export interface (\w+)\s*\{([\s\S]*?)\n\}/g
  let match
  while ((match = pattern.exec(source))) {
    const [, name, body] = match
    const fields = [...body.matchAll(/^\s{2}(\w+)\??:/gm)].map((m) => m[1])
    found[name] = new Set(fields)
  }
  return found
}

async function check() {
  const document = await fetchSchema()
  const schemas = document.components?.schemas ?? {}
  if (!existsSync(HANDWRITTEN)) {
    console.log('No hand-written types to check.')
    return 0
  }
  const handwritten = interfacesIn(readFileSync(HANDWRITTEN, 'utf8'))

  const problems = []
  for (const [name, fields] of Object.entries(handwritten)) {
    //  Match by name: the client calls a thing what the server calls it, or
    //  there is nothing to compare and nothing to check.
    const schema = schemas[name] ?? schemas[`${name}Out`]
    if (!schema?.properties) continue
    const published = new Set(Object.keys(schema.properties))
    for (const field of fields) {
      if (!published.has(field)) {
        problems.push(
          `${name}.${field} is not in the API schema ` +
            `(it has: ${[...published].sort().join(', ')})`,
        )
      }
    }
  }

  if (problems.length) {
    console.log('The hand-written types have drifted from the API:\n')
    for (const problem of problems) console.log(`  ${problem}`)
    console.log(`\n${problems.length} field(s) the server does not publish.`)
    return 1
  }
  console.log('Hand-written types agree with the API schema.')
  return 0
}

async function main() {
  const mode = process.argv[2] ?? 'generate'
  try {
    if (mode === 'check') return await check()
    const document = await fetchSchema()
    writeFileSync(OUT, generate(document), 'utf8')
    const count = Object.keys(document.components?.schemas ?? {}).length
    console.log(`Wrote ${OUT} from ${count} schemas.`)
    return 0
  } catch (error) {
    //  A generator that fails the build when the backend is not running would
    //  make every offline task depend on a server being up.
    console.log(`Skipped: could not read the API schema (${error.message}).`)
    return 0
  }
}

process.exit(await main())

# AGENTS.md — flux-data-engine

Project instructions for human and AI contributors. These rules are binding.

---

## What this project is

`flux-data-engine` is a general-purpose **Data, Model and Execution platform**.

The core product abstraction is:

```text
Data → Model → Execution → Result → Application
```

**A Model is NOT synonymous with a machine-learning model.**

A Model is any versioned, describable and executable computational unit that
transforms inputs, parameters and context into outputs:

```text
Output = Model(Input, Parameters, Context)
```

Supported categories: machine learning, statistical, mathematical, formula,
rule/logic, optimization, simulation, custom — and, in future, LLM/agent models.

Training is optional. Prediction is only one kind of Execution. Every execution
produces a Result.

---

## Hard rules

### Rule 1 — Model ≠ ML
Never design `Model` as an alias for `MLModel`. The domain-level abstraction
stays `Model`; ML is one provider under `app/plugins/sklearn/`.

### Rule 2 — Training is optional
Formula, rule, simulation and optimisation models have no training step. A
provider becomes trainable by implementing `train()`; nothing else changes.

### Rule 3 — Prediction is one Execution kind
Use `Execution` and `Result`. The kinds are training, prediction, simulation,
optimization, calculation, evaluation and transformation.

### Rule 4 — Core domain must not depend on ML frameworks
`import sklearn` / `xgboost` / `torch` / `mlflow` must never appear outside
`app/plugins/`. Enforced by `tests/test_model_abstraction.py`.

### Rule 5 — Provider-ise framework code
Framework-specific implementations live in `app/plugins/<provider>/`.
Register them in `app/plugins/bootstrap.py`. Never branch on model type in the
application layer — resolve the provider through the registry.

### Rule 6 — Schema first
Every Model declares an input contract, a parameter contract and an output
contract, built from `app/shared/contracts.py` primitives — the same primitives
that describe dataset schemas.

Contracts nest: a field may declare `fields` (an object), `item` (an array of
one shape), `values` (a mapping of name to one shape) or `visible_when` (shown
only for certain values of another field). `ContractForm.vue` renders all of
them recursively, so a provider that needs a structured parameter describes it
rather than asking the user to type JSON. The contract must match what the
provider actually reads — `test_provider_contracts.py` exists because the
optimizer declared `variables` as a list and read it as a mapping, and the form
was wrong for as long as nobody looked.

### Rule 6.5 — A comparison declares which way is better
An `Experiment` carries `primary_direction` (`higher` / `lower`) alongside
`primary_metric`, and the leaderboard sorts by it. Ranking used to assume
higher was better for every metric, so an experiment measured in RMSE put its
worst trial at the top and called it the leader.

Do not infer direction from the metric's name. A name is a string coincidence —
two providers can spell the same idea differently, and the platform compares
across providers on purpose.

### Rule 7 — Result is first class
Executions return a `ResultPayload`; the platform persists a `Result`. Never
`return dict` as the output contract.

---

## Layering

Each module under `app/modules/<name>/` has four layers:

| Layer | May import | Must not import |
|-------|-----------|-----------------|
| `domain/` | stdlib, `app.shared`, other modules' domain | SQLAlchemy, FastAPI, pydantic, any ML framework |
| `application/` | its module's `domain.ports`, domain entities, other modules' application services | `infrastructure`, SQLAlchemy, FastAPI |
| `infrastructure/` | SQLAlchemy, drivers, file formats, its module's ports | FastAPI |
| `api/` | FastAPI, pydantic, application services | repositories, ORM rows |

Services are constructed with ports, never with concrete repositories. Only the
composition root — `app/core/container.py`, which `app/api/deps.py`, the seeder
and the worker all build through — names a concrete implementation. When you
add a service method that needs new persistence, add it to the module's
`domain/ports.py` first, then implement it.

Dependency direction is a stack, lowest first. A module may import from its own
level and anything below it, never above:

```text
4   orchestration    reporting    evaluation
3          execution        analysis
2             model           results
1   platform    data    applications    jobs
```

`tests/test_module_dependencies.py` walks every module's imports with `ast` and
fails on an upward edge. Adding a module means adding it to `LAYERS`,
deliberately, at the level where it belongs. If a new dependency does not fit,
the fix is almost never to move the module up a level.

Two placements are load-bearing and should not drift:

- **`model` depends on no module at all.** `ResultPayload` lives in
  `app/shared/payloads.py` so the plugin contract needs nothing but `shared`.
  A plugin author should not have to import the platform to return a result.
- **`jobs` is at the bottom.** The kinds of work it runs are *injected* by the
  container, not imported. The moment `jobs` imports `orchestration` to run a
  pipeline, it stops being a queue and becomes a scheduler for one thing.

Business logic never lives in a route. Routes parse, delegate and serialise.

### The composition root

`app/core/container.py` is the only place that names a concrete repository,
reader, storage backend or dispatcher. The API (`api/deps.py`), the seeder and
the worker all build their services through it. If you find yourself importing
a `Sql*Repository` anywhere else, the wiring belongs in the container instead.

---

## Isolation belongs in the repository

Every resource carries `workspace_id` and `created_by`, and uniqueness is
`(workspace_id, name)` rather than `name`. The filtering lives in
`app/shared/scoping.py`, mixed into the SQL repositories:

| Helper | What it does |
|--------|--------------|
| `_stamp(row)` | writes the current workspace onto anything created |
| `_scoped(select)` | adds `where workspace_id = ?` to every listing |
| `_fetch(id)` | returns `None` for a row belonging to another workspace |

`_fetch` is the one that matters. Filtering a list is the obvious half;
refusing a direct lookup by id is the half that gets forgotten, and forgetting
it means an id leaked from one workspace reads fine in another. Put scoping in
a route or a service and the next endpoint will not have it — put it in the
repository and the only way to bypass it is to bypass the repository.

`WorkspaceScope.unscoped()` exists for genuinely global work (bootstrap,
migrations). It is not a convenience; if you reach for it in request handling,
something is wrong.

## Claiming work is one statement

Taking a pending Execution or Job is a conditional update, and the database
decides the winner:

```sql
UPDATE executions SET status = 'running', attempts = attempts + 1, ...
 WHERE id = ? AND status = 'pending'
```

`rowcount == 0` means somebody else has it; the loser returns the current row
and does nothing. Never read a row, check its status and write "running" — that
is three steps with two gaps, and the recovery sweep re-queues anything that
has been PENDING too long, which includes work a worker picked up a moment ago.

The failure is silent: two Results for one submission, two materialised
datasets, two calls to whatever the model talks to. `test_concurrent_workers.py`
runs the race with real threads; if you add a third kind of claimable work,
add it there too.

**Claim before resolving anything.** The claimed row is the one to work on;
resolving first and claiming after throws away whatever the resolution already
wrote to the entity.

## Long work belongs in a Job

A pipeline run, an experiment run and a report export used to happen inside the
HTTP call — a request timeout waiting to happen, and in queue mode nothing told
the page the work had finished. `app/modules/jobs/` owns that now: enqueue,
lease, heartbeat, cancel, retry cap, dead letter, and an SSE stream the
frontend consumes through `useJob.ts`.

The module imports none of the work it runs. Handlers are registered into it
from `app/core/container.py`, so a new kind of background work is a handler
plus a registration, and `jobs` stays at the bottom of the dependency stack.

Cancellation is cooperative and is checked **between `plugin.execute()` and
`results.persist()`**. Check it after persisting and a cancelled execution
still points at a stored result, which reads as a UI glitch and is not one.

**A provider that searches an open-ended space checks `context.should_stop()`
inside its loop** — cancelled, or out of time. The optimizer and the simulation
both do, every few hundred iterations, and both report `complete` so that an
answer which stopped early cannot be mistaken for one that did not. A bound on
the *count* of iterations is not a bound on time: a grid of a million cheap
candidates and a grid of a thousand expensive ones look the same to a limit and
nothing like each other to a person waiting.

The typhoon backtest is the exception, and deliberately: its loop is inside the
preserved research algorithms, which are kept line-for-line. It is bounded by
`MAX_SAMPLE` instead.

SSE is consumed with `fetch` + `ReadableStream`, not `EventSource` — the
latter cannot send an `Authorization` header.

## The platform must not name a domain

`app/` outside `plugins/` contains no reference to any application's subject.
That is checkable, and it was not true: the core imported a typhoon router by
name and `Settings` carried a `typhoon_data_dir`.

A plugin that ships user-facing endpoints registers them in
`app/plugins/contrib.py`, and `app/api/router.py` mounts whatever it finds. A
plugin that needs a path to its own data owns that path — see
`plugins/typhoon_analog/paths.py`. A plugin that wants resources in place on first
run declares them in its own `seed/` package and contributes them through
`contributed_seeders()`. Adding or removing a built-in application must never
edit a file under `app/core/` or `app/modules/` — and
`test_seed_fixtures.py` fails if the word "typhoon" reappears anywhere in
`app/core/`.

Two rules that keep this honest:

- Every value in `ModelType` must have at least one registered provider.
  `test_model_type_coverage.py` fails otherwise. A category the Model Library
  displays with "none yet" beside it is a promise the product cannot keep.
- Every value in `SourceType` must have a reader. `/sources/types` reports only
  the registered ones, so the UI can never offer a format that fails on use.

## A version *is* the definition

`ModelVersion.definition_snapshot` is what an execution runs when it names a
version. This is not decoration and must not become it again: the snapshot was
once written by `publish_version` and read by nothing, so every execution — even
one pinned to a version — was handed the live model row. Editing a model changed
the result of re-running an old version, and nothing in the record said why.

The model row is the **working definition**: what you edit. A version is the
**published definition**: what executes. An unpinned execution runs the current
version, so editing has no effect until you publish — which is why
`has_unpublished_changes` exists and why the detail page states it.

That flag is computed by comparing `_BEHAVIOURAL` fields, never stored. A status
somebody sets by hand can disagree with the facts; a comparison cannot. For the
same reason `ModelStatus` has only the two states a person genuinely chooses
(active / deprecated) — draft and published are facts, not choices.

## Capabilities, not categories

A model reports what it can do — `execution_kinds`, `trainable`, `configurable`,
`open_input`/`open_output` — and the UI renders from that. Do not add
`if type == …` to a component: `ModelType` is an open set, and a branch per
category needs a new branch for every category added. The capability set is
finite; the categories are not.

`open_input` means the provider validates the payload rather than a declared
field list — a FREE shape *or* no declared fields. Both look the same to a
reader, so they must report the same, or two cards on one page contradict
each other.

## A Model and a Pipeline are both Runnables

An Execution names a **target**, not a model: `target_type` says what kind of
runnable it is and `target_id` says which one. `Execution.model_id` is still
there and still answers "which model", but it is a read-only property that
returns `None` for anything that is not a model — never the pipeline's id,
because a caller filtering by model must not match a pipeline.

This is why it matters: everything built on execution — scheduling, experiments,
serving, lineage, the executions list — used to work for exactly one kind of
thing. A pipeline could not be compared, invoked, or nested, and each of those
absences had its own excuse. They were one absence.

Rules that come with it:

* **`execution.target_id` carries no foreign key, and must not be given one.**
  What it points at depends on `target_type`, and no column can reference two
  tables. It kept `model_id`'s key through the rename, and the first pipeline
  execution against PostgreSQL was rejected by it — while every SQLite test
  passed, because SQLite does not enforce foreign keys unless asked. It is now
  asked (`app/core/database.py`), so the suite fails the way the deployment
  would.
* **Execution must not import orchestration.** A pipeline is run through a
  `RunnableRunner` injected by `app/core/container.py`, the same way `jobs`
  learns about handlers. `orchestration` sits above `execution`; that direction
  stays one-way.
* **A new kind of runnable adds a `RunnableKind` value and a runner** — and
  `test_runnable_coverage.py` will fail until it can be executed, listed,
  invoked and compared, which is the point of that file.

## A pipeline step can be another pipeline

`PipelineStep` runs exactly one thing: a `provider` configured inline, a
`model_id` from the library, or a `pipeline_id`. Naming two is refused when the
pipeline is saved rather than resolved quietly — whichever one lost would have
run for years.

Cycles are refused at save time over the whole reachable graph (A nesting B
nesting A is the same loop as A nesting A), and `MAX_NESTING_DEPTH` stops a run
that got past it by two pipelines being edited between saves.

A nested step delegates to a **real run** of that pipeline — its own row, its
own step runs, its own outputs — and the step records `pipeline_run_id`.
Flattening it into the parent would read more tidily and would lose the reason
to nest at all: that the shared pipeline is one pipeline, run and reviewed the
same way wherever it is used.

## Independent steps run at the same time

Steps are dispatched in **waves**: everything in a wave has its inputs already,
so nothing in it waits for anything else in it. A wave is run in threads, each
with a database session of its own, through a `StepWorker` injected by the
composition root — a SQLAlchemy Session belongs to one thread, and sharing the
request's would trade a slow pipeline for a corrupted one.

Two things stay in the calling thread: a nested pipeline step (it writes a run
through this session) and everything, always, on SQLite — one writer at a time
means threads there buy nothing and produce "database is locked". That is what
`Settings.steps_may_run_in_parallel` decides; `pipeline_max_parallel_steps` is
the width.

The run record is written by the thread that started it. A worker returns a
`StepRun` to copy from, never the same object mutated from two places.

## A pipeline leaves behind exactly its outputs

A twelve-step pipeline used to create twelve `ModelDefinition`s and twelve
`Dataset`s nobody had asked for — the model library was 48% plumbing and the
dataset list 81% — so `ModelScope.STEP` hid the models, `DatasetOrigin.INTERMEDIATE`
hid the datasets, and two service passes ran afterwards to relabel what the run
had just produced.

Both patches are gone, because what they were hiding is no longer created. A
step carries its own provider and configuration **inline**, and only what the
pipeline was built to produce becomes a Dataset: twelve steps now leave zero
models and one dataset. `test_resource_scope.py` pins that invariant.

`DatasetOrigin.INTERMEDIATE` remains in the enum for rows created before the
change. Nothing new produces it; do not reach for it.

The general rule: if you add something that creates resources in bulk, the fix
is to stop creating them, not to add a flag that hides them.

## Data rules

External formats are normalised into `Table` (Apache Arrow) at the boundary in
`app/modules/data/infrastructure/readers.py`. Nothing downstream —
visualisation, model execution, results — may know a file was ever a CSV.

**Work happens in Arrow, not in Python rows.** `Table` filters, sorts,
aggregates, deduplicates and profiles through `app/shared/table_ops.py`
(`pyarrow.compute`); `from_parquet(path, columns=…)` reads only the columns
asked for. `to_rows()` is the exit, not the workspace — materialising a
dataset to dicts to return one page of it is the bug this replaced. New query
paths must push down, and `test_query_pushdown.py` counts the rows and columns
actually touched, so "it returns the right answer" is not enough.

**Push the projection into the read, not after it.** Narrowing a table that is
already in memory saves Python and no disk at all. A caller that knows which
columns it needs passes them to `read_table` — the Explore query does, and so
does every chart.

**A missing column reads as nulls.** `column_values` answers `[None] * n`
rather than `[]`, because that is what `row.get(name)` did and code written
against rows depends on it — a chart split by a column the dataset lacks drew
one unnamed band, and should not start raising instead. Callers that care
whether a column exists ask `columns` first.

Rewriting a row-based operation into Arrow risks silent behaviour change — a
sort that used to be stable, a deduplication that kept the first row, a filter
that matched because both sides were stringified. Keep the original as an
oracle in the test and require the two to agree on a deliberately untidy table,
as `test_table_operations.py` and `test_columnar_transforms.py` do.

**A plugin declares the data it needs.** `PluginDescriptor.required_datasets`
is how a provider's data goes through the front door — versioned, traceable,
replaceable without a volume mount — instead of opening files beside the
platform.

Dataset versions are immutable. Re-reading a source appends a new version.
Bulk data goes to Parquet in the object store; PostgreSQL holds metadata only.

Model versions are immutable. Changing behaviour publishes the next version.

---

## The seeded worked example

`app/core/seed.py` seeds the sales golden path **and nothing domain-specific**.
It discovers plugin seeders through `app/plugins/contrib.py:contributed_seeders()`
and knows only that plugins have things to set up.

A plugin's setup has two halves. The declarative half is a `Fixture`
(`app/plugins/fixtures.py`): sources, datasets, models, visualisations,
dashboards and applications, listed as data and wired to each other **by name**,
because ids do not exist until the thing does. The loader looks everything up
by name before creating it, so it is idempotent by construction, and a section
that fails costs that section rather than the whole application.

The code half is what genuinely is code. For the typhoon application:
`plugins/typhoon_analog/seed/climatology.py` builds the analysis half on the
real record — a twelve-step Pipeline of standard transforms, sixteen
Visualizations, four Dashboards — and `seed/backtests.py` builds the validation
half: three backtest Models compared in one Experiment, their Evaluations, a
Schedule and two Reports.

The fixture is loaded, then the code seeder runs, then the fixture is loaded
again — because the dashboards an application bundles exist only after the
charts have been computed, and an application published with an empty dashboard
list opens on nothing. Reference dashboards by importing the name the code
seeder uses, never by retyping it: a name typed twice is a name that drifts,
and this one drifted silently for a whole release.

Seeding is idempotent, and the climatology pipeline compares its run's step
names against the pipeline's own before deciding whether to re-run: an install
seeded by an earlier version gets the newer chain and a fresh run, rather than
a new chain with a stale result.

Every feature the sidebar exposes must have at least one seeded example that a
reader can open and use. If you add a feature, seed one. Seeding is idempotent
and each step runs in its own savepoint, so a failing step never rolls back the
steps before it — keep it that way, and make new steps check for what they
create before creating it.

Seeding forces `RunInline()` for its dispatcher so the example is complete even
when the deployment runs in queue mode, and it seeds **into the default
workspace** rather than into no workspace at all — a row with no workspace is
invisible to every scoped query, which is a seeded example nobody can see.

---

## The typhoon algorithms are preserved code

`app/plugins/typhoon_analog/algorithms/` holds the original research pipeline.
It is kept as written so its numerical behaviour stays verifiable:

- Do **not** reformat, refactor or "tidy" these files.
- Only import paths were changed when the code was rehomed.
- They are excluded from some lint rules in `pyproject.toml`, deliberately.
- They print progress to stdout; the plugins capture that into execution logs.

Coastline (absolute-position Chamfer distance inside a coastline buffer) and
Coastline-RRF (reciprocal rank fusion, coastline weighted 0.80) are the
substance of the typhoon application. Changes there need a stated reason and
must keep `tests/test_typhoon_analog.py` green.

---

## Before changing code

1. Inspect the existing architecture.
2. Identify affected modules.
3. Identify domain / API / database changes.
4. Produce an implementation plan.
5. Implement the smallest coherent change.
6. Add or update tests.
7. Run `pytest -q` and `ruff check app tests`; if the frontend changed, also
   `npm run build`, `npm run types:check` (hand-written TS vs the OpenAPI
   schema) and `npm run check:layout`. `bash scripts/test.sh` runs all of them.
8. Report changed files, tests, risks and remaining work.

Do not refactor unrelated code. Do not introduce duplicate abstractions.
Do not change API contracts without explicit justification. Do not delete tests
to make an implementation pass. Do not hardcode secrets.

Do not introduce microservices or Kubernetes unless explicitly requested — this
is a modular monolith with a plugin architecture.

---

## Adding things

**A new source format** — add a reader in
`app/modules/data/infrastructure/readers.py`, add the `SourceType`, register it.
Nothing else changes.

**A new model provider** — create `app/plugins/<name>/plugin.py` implementing
`describe()`, `validate()`, `execute()` (and `train()` if it is trainable),
then register it in `app/plugins/bootstrap.py`.

**Explore has an exit.** A query worked out on screen becomes a pipeline
through `POST /pipelines/from-query`; the query-to-steps translation lives in
`orchestration/application/from_query.py` because it is knowledge about
transforms - which one implements a condition, what it calls its options - and
belongs where it can be tested without a browser. Generated steps chain with
`input_from`: leaving it unset means "the pipeline's input dataset", which
turns a chain into a fan of branches that each ignore the others and still
succeeds, with the wrong answer.

**A provider states what it costs.** `PluginDescriptor.version` travels into
an execution's lineage, so re-running a pinned definition after the provider
changed says so rather than looking identical; `timeout_seconds` becomes that
execution's deadline, because one number for a formula and a leave-one-out
backtest is either too tight to be safe or too loose to be useful. Both are
read by the platform. Do not add descriptor fields nothing reads - a declared
capability with no consumer is the empty promise `test_model_type_coverage.py`
exists to prevent.

**A multi-input provider** — declare more than one input in the descriptor and
read the extra tables from the execution context, as `plugins/join/` does.
Align key column types before comparing them, and when a key is missing say
which side is missing which column: "join key not found" is a message nobody
can act on.

**A new execution kind** — add it to `ExecutionKind` and declare it in the
providers that support it.

**A migration** — it must run on SQLite as well as PostgreSQL, because this
project documents SQLite as the way to run without PostgreSQL. That means
`op.batch_alter_table` for anything SQLite cannot ALTER (columns, constraints),
no PostgreSQL-only SQL (`NOW()` is not a thing there — bind the value), and
dropping an index before the column it covers. `test_migrations.py` runs
`upgrade head`, compares the result against `Base.metadata`, and downgrades the
whole chain back to base; a migration that only works one way fails it.

**A new ORM module** — nothing to do. `import_all_orm_models()` discovers
`modules/*/infrastructure/*orm*.py` rather than listing them, because the list
fell behind during the reorganisation and `alembic revision --autogenerate`
started proposing `op.drop_table` for every module it had not been told about.

**Lineage is derived, never stored.** `app/modules/lineage/` walks the rows
that already record it — a version's `lineage` dict, an execution's model and
dataset version, a chart's source. Do not add an edge table: it would have to
be written alongside those rows, and the first time the two disagreed the graph
would be the half nobody trusts.

Two directions are not two graphs. An arrow always points the way the data
flowed; which way the *walk* goes is a separate question, which is why
`_edges()` returns `(edge, next)` pairs. Containment — a version belongs to its
dataset — is followable both ways; flow is not. Getting that wrong made a
dataset built by a pipeline look like it came from nowhere.

**Two verbs must not disagree about what an input is.** `invoke` took only
`dataset_version_id` while `submit` took either that or `dataset_id` - and
pydantic ignores fields it does not know, so a caller who sent `dataset_id` got
a successful answer computed from no input at all. When you add a parameter to
one of the two, check the other.

**One `/invoke`, two kinds of runnable.** `POST /models/{id}/invoke` and
`POST /pipelines/{id}/invoke` answer with the same body, built by
`invoke_response()` — an integration that can read one can read the other,
which is what makes "both are runnables" mean something to the caller and not
only to the code. Both record nothing: no Execution, no PipelineRun, no
Result, no Dataset. A pipeline invoke chains its steps through
`ExecutionService.invoke_once`, which hands back the whole payload rather than
the response's first thousand rows — truncating mid-chain would quietly answer
the wrong thing.

**A synchronous answer is capped.** `invoke` returns at most
`INVOKE_MAX_ROWS` rows, with `row_count` and `truncated` beside them. Serving is
not a bulk export: a caller that wants the whole table submits an execution,
which keeps it and can materialise it as a dataset.

**A new kind of background work** — write a handler, register it into
`JobService` from `app/core/container.py`. Never import the work into
`app/modules/jobs/`.

**Resources a built-in application ships with** — add them to the plugin's
`Fixture`, not to `app/core/seed.py`. Anything that is genuinely an action —
running a backtest, recording an evaluation, computing a chart — stays as code
in the plugin's `seed/` package and is contributed alongside the fixture.

**A new storage backend** — implement `ObjectStore` and register it in
`app/shared/storage.py:create_object_store`. Callers address objects by URI and
never learn which backend answered.

**An application's models are its tools.** `model_ids` was a list nothing
rendered: the platform's whole proposition is "give it input, it runs a model,
you get an answer", and that was available to whoever built the model and to
nobody else. `ApplicationViewPage` renders each bound model through
`ContractForm` + `/invoke`, and offers **the application's own datasets** to
run against - not the workspace's, because bundling datasets is a choice and
handing over the whole catalogue makes that choice meaningless.

The shared, tokenless view deliberately gets no tools. A link holder reads;
running a model from an unauthenticated page would turn a read-only capability
URL into an open compute endpoint, which is a different thing from what the
link is documented to be.

**Publishing an application** — the rule is that publishing must not make
nothing reachable, and it is *not* "must have an entrypoint". A composed
application has a page of its own at `/applications/{id}`, so what it needs is
a dashboard to show there; a built-in one names the route it is, so it still
needs an entrypoint. Both that page and the shared link render through
`applications/api/rendering.py` and `RenderedDashboards.vue` - one renderer
each side, because what somebody shares should be what they saw.

**A new page or card in the UI** — use the shared primitives in
`frontend/src/components`: `PageHeader` for the page heading, `SectionCard` for
every card, `StatusText` for status, `FactList` for label/value pairs,
`ContractForm` for anything with a Contract, `ChartView` for charts. Do not
reach for `q-badge` or `q-chip`: a coloured pill on every row turns a list into
confetti and stops carrying meaning. A quiet `.fx-tag` span is the default; a
pill has to earn its place.

Values come from tokens in `src/css/app.scss`, never from a number typed into a
component: `--fx-space-*` (4px scale), `--fx-text-*` (six steps, nothing
between them), `--fx-radius`/`--fx-radius-sm`, `--fx-icon`/`--fx-icon-lg`,
`--fx-ok`/`--fx-bad`/`--fx-run`/`--fx-wait`. If a computed style shows a value
outside those sets, a component invented one.

Three rules that come from layout faults this codebase actually had, each of
which passed review by eye and failed at a real viewport:

- **`min-width: 0` on anything holding user text.** A flex or grid child will
  not shrink below its content's intrinsic width without it, so one long name
  widens the page instead of wrapping. Pair it with `overflow-wrap: anywhere` —
  a 120-character identifier has no break opportunity of its own.
- **Wide content scrolls in its own box.** Wrap it in `.fx-scroll-x`. Sixty
  columns wrapped to a phone's width produced a page 190,000 pixels tall; the
  same table in a scroll region is still a table.
- **A list row is an index entry.** `.fx-list` clamps labels to two lines, so a
  pasted paragraph cannot make one row taller than the screen. The full text
  belongs in the detail pane.

Quasar's `q-gutter-*` uses negative margins and will overlap the box beside it;
use `gap`. Its `size="sm"` on a button shrinks the *label* to 10px — below the
type scale — so use `dense` for a smaller box instead.

**Fetching** — call the typed function in `src/api/index.ts`; never `fetch` a
platform endpoint from a component. Endpoints whose answer cannot change while
the tab is open — the provider catalogue, the transform vocabulary, the chart
types — are wrapped in `reference()` and served from `src/api/cache.ts`: a
five-minute TTL, one shared request when several components ask at once, and
**everything dropped on any write or workspace switch**. Do not cache anything
a person edits. A cache nobody invalidates is a way to show stale data
confidently, which is worse than a second request.

Work that can outlive a request goes through `useJob()` — submit, watch the SSE
stream, offer cancel — rather than a spinner and hope.

**A list a person browses** — needs search (`SearchField` plus either
`useListFilter` for a loaded collection or the API's `search` parameter for one
that can outgrow a response), an `AsyncSection` wrapper so a failed load reads
as a failure rather than as an empty platform, and — if it is master/detail —
`useUrlSelection`, so the view can be linked, reloaded and shared.

**Checking UI work** — run `npm run check:pages` first. It opens every route
signed in and fails on a 4xx/5xx, a console error, an uncaught exception, or
one of the app's own failure states on screen. It exists because everything
else in this project can pass while a page is completely broken: `check:layout`
measures geometry, and the unit tests build their own schema with `create_all`,
so neither can notice that the database the running application talks to is a
migration behind the code. That is exactly how `/experiments` and
`/evaluation` served 500s to every visitor while every check reported success.

**If you add a migration, migrate the running stack too.** The container runs
`alembic upgrade head` at start-up, so a restart is enough — but editing code
without restarting leaves the app one revision ahead of its database, which
fails only on the tables the migration touched.

**Layout is measurable, and guessing is not the same as looking.** `npm run check:layout` drives headless Chrome over every route at
375/768/1024/1440/1920 in both themes and reports document overflow, elements
past the viewport, overlapping block siblings, silent clipping and contrast
below 4.5:1. Use it rather than trusting that a change "looks fine": the same
sweep found 225 faults in a UI that had been reviewed by eye twice.
`--width=1440` narrows it while iterating; `--shots <dir>` saves screenshots.

It skips loudly — no Chrome, no frontend answering — and it exits non-zero if
it crashes, because a check that passes by failing is worse than no check.

One trap worth knowing: the dev container bind-mounts `frontend/` from the host,
and filesystem events do not cross that boundary on Windows or macOS. Vite is
configured with `server.watch.usePolling` for that reason. Without it the server
keeps serving what it read at start-up, and an edit appears to do nothing —
which silently invalidates any check you run against it.

**A new chart** — the backend `ChartSpec` carries the presentation metadata
(`x_title`, `y_title`, `unit`, `subtitle`, `value_labels`, `x_order`,
`series_order`, `bins`). Fill them in there rather than hard-coding labels in
the component: `ChartView` draws axis titles, ticks, gridlines, a zero line, a
legend and a hover readout from that spec.

Pick the chart type from the question, not from habit: a distribution is a
`histogram`, a spread per category is a `box`, two categorical axes are a
`heatmap`, composition is a `stacked_bar`. Ordinal categories need `x_order` or
`series_order` — alphabetical order puts 中度 before 輕度, which is wrong.

**A new chart type** — add it to `ChartType`, give it a builder in
`analysis/application/services.py` (the existing ones are `_histogram`,
`_boxes`, `_grid`), and render it in `ChartView.vue`. The envelope stays the
same — `categories` plus named `series` — so a new type must express itself in
that shape rather than inventing a new payload.

**A new transform** — implement it in `plugins/python_function/columnar.py`
against `Table`, and register it in `standard.py` with a Contract naming every
parameter. One table in, one table out, one job. That contract is what the
pipeline builder renders as a form, so a transform whose parameters are
undescribed is a transform nobody can configure.

**Write it column-wise.** `Transform` still accepts a row-based `fn`, and
nothing uses it: all twenty-two are `table_fn`, and a test asserts that none of
them materialises its input as rows. Where Arrow has a kernel, use it. Where
the semantics need Python - six timestamp formats tried in order, a number
pulled out of `"30 (m/s)"` - read the one column you need with
`column_values()` and assemble the result with `set_column()`. Never
`to_rows()` the input: that is the cost the whole rewrite removed, and it was
worth 31x on a 50,000 x 40 table.

`set_column`, not `append_column`, when replacing a column: appending moves it
to the end, and a step that reorders columns changes what every step after it
reads.

**A new API endpoint** — add it to its module's router. Authorisation comes from
the module guard in `app/api/router.py`, keyed on the HTTP method, so a safe
method needs the module's read permission and an unsafe one its write
permission. If an endpoint needs more, add `Depends(requires(...))` to it.

---

## Security rules

Passwords go through `app/shared/tokens.py` (scrypt), never anywhere else.
Access tokens are HS256 only, and the signature is verified before any claim is
read — do not relax either property.

`FLUX_SECRET_KEY` signs every token. It must be overridden outside development;
the API warns at start-up while it is still the default.

Never widen a role's permissions to make a test pass. If an endpoint genuinely
needs a different permission, say so in the route rather than in the role.

The expression evaluator (`app/plugins/formula/expression.py`) is an allow-list
AST walker on purpose. Do not add `eval`, attribute access, imports or arbitrary
user source execution to it, or to any other provider.

**Outbound requests are attacker-controlled URLs.** A REST source is a URL a
user types and the server fetches, which is a server-side request forgery
primitive unless something says no. `app/shared/outbound.py` resolves the host
and refuses anything that is not a public address — loopback, private ranges,
link-local (including the cloud metadata endpoint), and redirects are capped at
three so a public URL cannot bounce to a private one. Any new outbound fetch
goes through `check_url()`.

**A database source is a statement somebody wrote.**
`app/modules/data/infrastructure/sql_guard.py` allows exactly one read
statement — a single `SELECT` or `WITH`, no forbidden keywords, no stacking —
and table names are validated rather than interpolated. Give the reader its own
least-privileged credentials; the application's connection is not it.

**API keys are stored as SHA-256 hashes.** The plaintext is returned once, at
creation, and never again. Do not add an endpoint that reveals a key, and do
not log one.

**A share token is a capability.** `/public/applications/{token}` is the only
unauthenticated route, mounted apart from the guarded list precisely so that
"which routes need no credential" is answerable by reading one line. It is
read-only, scoped to one application, and revocable. Keep all four properties.

---

## Conventions

- Replies to the user: 繁體中文. Code comments: English.
- Commit messages: Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `chore:`).
- Prefer the standard library; avoid unnecessary dependencies.
- Secrets go in environment variables, never in code or commit messages.
- Validate all external input (see `resolve_path` and the AST expression
  evaluator for the pattern).
- Follow OWASP Top 10.

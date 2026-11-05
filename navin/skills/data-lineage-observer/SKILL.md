---
name: data-lineage-observer
description: Observe data pipelines through OpenLineage events and lineage catalogs (Marquez, DataHub, OpenMetadata) to answer where a dataset comes from, what depends on it and whether it is fresh. Reads RunEvents (START, COMPLETE, FAIL, facets schema, columnLineage, errorMessage, outputStatistics) emitted by Airflow, dbt, Spark, Flink or Dagster, walks the graph for impact and root cause, finds orphan datasets, traces personal data column by column and checks freshness SLAs from a JSONL export. Use when the user asks to "what breaks if I change this table", "why is this dashboard stale", "where does this column come from", "set up OpenLineage for Airflow", "which datasets are orphaned", or when a data quality incident needs a producer and a timeline. In French "analyse d'impact sur une table", "lineage des donnees", "pourquoi la donnee est perimee".
metadata: {"navin":{"emoji":"🧬","category":"data"}}
---

# Data Lineage Observer

## Overview

This skill reads data lineage as a graph of datasets and jobs built from
OpenLineage events, then answers operational questions: downstream impact of a
schema change, root cause of stale or wrong data, propagation of personal data,
orphan datasets and freshness against expected cadences. It works from a
Marquez, DataHub or OpenMetadata instance when one exists and from raw event
exports otherwise. The default posture is read-only and evidence-based: pull
events and graph responses, compute offline, cite run ids, job names and event
times, and never change a pipeline, a producer configuration or a catalog
entry without the user's explicit approval.

## When it applies

- A table, column or file layout is about to change and the owner needs the
  list of jobs and datasets that read it, with their owners.
- A dashboard or report shows stale or wrong numbers and the source of the
  problem is somewhere upstream.
- Privacy asks where an email, a national id or a phone number column ends
  up (warehouse, exports, feature stores).
- The platform team wants to find datasets nobody reads, jobs with no
  declared datasets, or namespaces that split the graph.
- Freshness SLAs must be verified from run history rather than from the
  scheduler's green ticks.
- A new producer (Airflow, dbt, Spark, Flink, Dagster, Great Expectations)
  must be wired to the lineage backend.

Hand over to `data-quality-agent` for profiling and assertion design, to
`sql-analyst` for query-level debugging and to `data-privacy-auditor` for the
legal classification of the fields found.

## Engines and sources

### OpenLineage events

- `RunEvent` with `eventType` in `START`, `RUNNING`, `COMPLETE`, `ABORT`,
  `FAIL`, `OTHER`; `eventTime`; `producer`; `schemaURL`; `run.runId` and
  `run.facets` (`nominalTime`, `parent`, `errorMessage`, `processing_engine`);
  `job.namespace`, `job.name`, `job.facets` (`sql`, `sourceCode`,
  `sourceCodeLocation`, `jobType`); `inputs[]` and `outputs[]` with
  `namespace`, `name`, `facets` (`schema`, `dataSource`, `columnLineage`,
  `lifecycleStateChange`, `symlinks`), `inputFacets` (`dataQualityMetrics`,
  `dataQualityAssertions`) and `outputFacets` (`outputStatistics`).
- `DatasetEvent` and `JobEvent` carry static metadata without a run.
- Naming: namespace is the datasource URI (`postgres://host:5432`,
  `snowflake://account`, `s3://bucket`, `kafka://broker:9092`), name is
  `schema.table` or the object path. Field tables, state machine and minimal
  events: `references/openlineage-schema.md`.

### Marquez

- Ingest: `POST /api/v1/lineage`.
- Read: `GET /api/v1/namespaces`, `GET /api/v1/namespaces/{ns}/datasets/{name}`,
  `GET /api/v1/namespaces/{ns}/jobs/{job}/runs`,
  `GET /api/v1/lineage?nodeId=dataset:{ns}:{name}&depth=3`,
  `GET /api/v1/column-lineage?nodeId=datasetField:{ns}:{name}:{field}`,
  `GET /api/v1/events/lineage?after=&before=&limit=` for raw events,
  `GET /api/v1/search?q=`.
- DataHub (`/openapi/openlineage/api/v1/lineage`, GraphQL
  `searchAcrossLineage`) and OpenMetadata
  (`GET /api/v1/lineage/table/name/{fqn}?downstreamDepth=3`) equivalents:
  `references/marquez-api.md`.

### Producers

- Airflow: `apache-airflow-providers-openlineage`, `[openlineage] transport`
  and `namespace` in `airflow.cfg`, job name `dag_id.task_id`.
- dbt: `openlineage-dbt`, run with `dbt-ol run`, `OPENLINEAGE_URL`,
  `OPENLINEAGE_NAMESPACE`.
- Spark: `spark.extraListeners=io.openlineage.spark.agent.OpenLineageSparkListener`
  and `spark.openlineage.transport.*`, `spark.openlineage.namespace`.
- Flink listener, Great Expectations `OpenLineageValidationAction`, Dagster.
- Config snippets and the post-enable checklist: `references/producers-setup.md`.

### Navin integration

- Scheduler pods, Spark clusters, warehouse hosts and object storage are
  inspected through the `infra` tool at observe level (`kubectl get
  configmap airflow-config -o yaml`, `aws s3 ls`); prepare and execute
  levels only for changes the user approved.
- Datadog, Grafana, Slack, GitHub, Notion: deliver findings and read
  pipeline alerts through the `connectors` tool.
- `MARQUEZ_API_KEY`, `DATAHUB_TOKEN`, `OPENMETADATA_JWT`, warehouse
  passwords come from environment variables; report their presence only.
- `jq`, `curl`, `dot` (Graphviz) are installed with the `toolbelt` skill when
  missing.
- Event exports can reach hundreds of megabytes: search them with the `grep`
  tool (`"eventType": "FAIL"`, a dataset name, a `runId`) before reading.

## Workflow

1. Frame the question as a graph query: a start node (dataset
   `namespace/name` or job), a direction (downstream for impact, upstream for
   root cause), a depth and a time window. Write down the naming convention
   in use so the node can be addressed exactly.
2. Choose the source. Catalog present and reachable: query the lineage API
   directly and export the raw events for the window with
   `GET /api/v1/events/lineage?after=<iso>&limit=5000` (Marquez) into
   `events_<from>_<to>.json`, then convert to JSONL with `jq -c '.events[]'`.
   No catalog: collect events from the producer side (`file` transport,
   Kafka topic dump, Airflow task logs with `debug_mode`).
3. Validate the export before trusting it:
   `python3 scripts/lineage_graph.py examples/openlineage_events.jsonl stats`
   (replace the sample by the export). Check that every run id has a
   terminal event, that producers are the expected ones, and that datasets
   are not split across namespaces (`postgres://host:5432` versus
   `postgres://host`). Fix the mapping in the report, not in the data.
4. Impact analysis:
   `python3 scripts/lineage_graph.py examples/openlineage_events.jsonl impact raw.orders`
   lists downstream datasets with hop count and the job on each hop; add
   `--format dot` and render with `dot -Tsvg` when the user wants a picture.
   For a column change, read the `columnLineage` facets of the first-hop
   outputs (fields whose `inputFields` reference the column) and continue
   hop by hop; record `transformationType` or `transformations[].subtype`
   to tell direct copies from aggregations.
5. Root cause of stale or wrong data:
   `python3 scripts/lineage_graph.py examples/openlineage_events.jsonl upstream analytics.daily_revenue`
   gives the sources; then
   `python3 scripts/freshness_check.py examples/openlineage_events.jsonl examples/expected_cadence.json`
   shows, per expected dataset, the last `COMPLETE` write, its age, the
   producing job, its last run state and the `errorMessage` of a failed run.
   Exit 1 marks at least one stale dataset or failed last run. Walk upstream
   from the first stale node until a job with `FAIL` or a `COMPLETE` with
   `outputStatistics.rowCount: 0` appears; that is the root cause candidate.
   Confirm with the scheduler logs through `infra` before naming it.
6. Orphans and hygiene:
   `python3 scripts/lineage_graph.py examples/openlineage_events.jsonl orphans`
   separates datasets written but never read (candidates for deletion or
   missing consumers) from datasets read but never written (external sources
   or a producer that is not instrumented). Classify each with the owner.
7. Personal data propagation: start from the columns tagged in the `schema`
   facet (`description` containing PII, Marquez tags `PII`), follow
   `columnLineage` downstream, and list every dataset where the field lands
   with `masking` false. Datasets reached through jobs without column
   lineage are reported as "unknown propagation", never as safe.
8. Freshness SLAs: build the cadence file from the scheduler definitions
   (Airflow `schedule`, dbt job cron) or from the `nominalTime` facets, run
   `scripts/freshness_check.py` with `--now` set to the report time, and
   keep the file in the repository so the check can run in CI.
9. Write the deliverable with evidence per row and list the proposed changes
   (producer config, namespace fixes, SLA alerts) as an approval table.

## Rules

- Inputs and outputs are cumulative over a run: a `COMPLETE` without
  `outputs` does not mean nothing was written; merge with the `START`.
- `eventTime` is observation time; use `run.facets.nominalTime` for
  schedule-based reasoning (late runs, backfills). A backfill emits events
  with a `nominalStartTime` in the past and a recent `eventTime`.
- A `COMPLETE` with `outputStatistics.rowCount: 0` is the most common silent
  failure; the freshness script shows the row count so it can be flagged.
- Airflow `BashOperator` and `PythonOperator` without `inlets` and `outlets`
  produce jobs with no datasets; the graph has a hole there, say so.
- Spark job names change with the execution command and partition paths
  create one dataset per day unless `spark.openlineage.dataset.removePath.pattern`
  is set; aggregate by prefix in the report.
- Marquez `depth` counts edges (dataset to job is one edge); the script
  counts dataset hops. State which one a number refers to.
- Column lineage is only as complete as the producer: dbt-ol without the SQL
  parser, Spark with disabled facets and Airflow Python tasks emit none.
- Never print SQL facets that contain literals from personal data; quote the
  table and column names only.
- Never modify `airflow.cfg`, dbt profiles, Spark confs, Marquez tags or
  catalog ownership without approval; prepare the exact change and the
  rollback.
- Deleting or re-running a job is out of scope for this skill; hand the
  decision to the owner with the evidence.

## Deliverable

Produce a Markdown report with:

1. Scope: question, start node, direction, window, source (catalog URL or
   export file), event counts from `stats`.
2. Graph summary: `| Metric | Value |` (events, runs, jobs, datasets, edges,
   column lineage edges, producers).
3. Impact or upstream table: `| Hops | Dataset | Via job | Owner | Column lineage | Note |`.
4. Freshness table: `| Dataset | Cadence | Last COMPLETE (UTC) | Age | Producing job | Last job state | Status |`.
5. Root cause timeline: `| Time (UTC) | Job | Run id | Event | Evidence |`.
6. Orphans: `| Kind | Dataset | Owner | Recommendation |`.
7. Personal data propagation: `| Source column | Destination dataset | Destination column | Transformation | Masked | Hops |`.
8. Findings and proposed actions: `| # | Finding | Evidence | Proposed change | Rollback | Needs approval |`.

Attach the DOT file when a diagram was produced. When the user wants an HTML
report, render the same content with the `studio-html-report` skill; post it
through the `connectors` tool when asked.

## Resources

- `scripts/lineage_graph.py`: OpenLineage events (JSONL or JSON array) to a
  dataset and job graph; subcommands `stats`, `impact <dataset>`,
  `upstream <dataset>`, `orphans`; `--format markdown|json|dot` or `--json`
  after the subcommand; `--depth` for walks. Steps 3, 4, 5 and 6.
- `scripts/freshness_check.py`: events plus a cadence JSON to last COMPLETE,
  age, staleness, producing job, last run state and error message; `--now`
  and `--json`; exit 1 on stale or failed. Steps 5 and 8.
- `examples/openlineage_events.jsonl`: one day of synthetic events from
  Airflow, dbt, Spark and Great Expectations with an hourly ingest, a dbt
  model with column lineage, a failed report job and a running feature job.
- `examples/expected_cadence.json`: the cadence file format
  (`cadence_minutes`, `grace_minutes`, `owner` per `namespace/name`).
- `references/openlineage-schema.md`: RunEvent, DatasetEvent, JobEvent, every
  facet with its fields, naming conventions, minimal events. Open it when
  reading raw events or writing a producer.
- `references/marquez-api.md`: Marquez endpoints, node ids, graph response
  shape, raw event export, DataHub and OpenMetadata equivalents. Open it
  before querying a catalog.
- `references/producers-setup.md`: transport configuration and per-producer
  snippets for Airflow, dbt, Spark, Flink, Great Expectations and Dagster,
  with the checklist to verify a new producer. Open it for setup requests
  and when the graph has holes.

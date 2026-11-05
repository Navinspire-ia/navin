# Marquez API and the DataHub / OpenMetadata equivalents

Marquez is the reference OpenLineage consumer. Default ports: API `5000`,
admin `5001`, web UI `3000`. Auth is off by default; behind a gateway, pass
`Authorization: Bearer $MARQUEZ_API_KEY` from the environment. All paths are
under `/api/v1`.

## Ingest

| Method and path | Body | Notes |
|---|---|---|
| `POST /api/v1/lineage` | one RunEvent, DatasetEvent or JobEvent | returns 201; Marquez creates namespaces, jobs and datasets on the fly |

Producers point to it with `OPENLINEAGE_URL=http://marquez:5000` or the
`http` transport in `openlineage.yml`.

## Read

| Method and path | Parameters | Returns |
|---|---|---|
| `GET /api/v1/namespaces` | `limit`, `offset` | `namespaces[]` with `name`, `createdAt`, `updatedAt`, `ownerName`, `description`, `isHidden` |
| `GET /api/v1/namespaces/{ns}/datasets` | `limit` (default 100), `offset` | `datasets[]` and `totalCount` |
| `GET /api/v1/namespaces/{ns}/datasets/{name}` | | `id {namespace, name}`, `type` (`DB_TABLE`, `STREAM`), `physicalName`, `sourceName`, `fields[] {name, type, tags, description}`, `tags`, `lastModifiedAt`, `lastLifecycleState`, `currentVersion`, `facets`, `columnLineage`, `deleted` |
| `GET /api/v1/namespaces/{ns}/datasets/{name}/versions` | `limit`, `offset` | every schema or write version with `createdByRun`, `facets` |
| `GET /api/v1/namespaces/{ns}/jobs` | `limit`, `offset` | `jobs[]` with `latestRun`, `inputs`, `outputs`, `location`, `parentJobName` |
| `GET /api/v1/namespaces/{ns}/jobs/{job}` | | `id`, `type` (`BATCH`, `STREAM`, `SERVICE`), `inputs[]`, `outputs[]`, `latestRun`, `facets`, `currentVersion` |
| `GET /api/v1/namespaces/{ns}/jobs/{job}/runs` | `limit`, `offset` | `runs[]` with `id`, `state` (`NEW`, `RUNNING`, `COMPLETED`, `ABORTED`, `FAILED`), `createdAt`, `startedAt`, `endedAt`, `durationMs`, `nominalStartTime`, `nominalEndTime`, `args`, `facets`, `inputDatasetVersions`, `outputDatasetVersions` |
| `GET /api/v1/jobs/runs/{runId}` | | one run |
| `GET /api/v1/jobs/runs/{runId}/facets?type=run` | `type` in `run`, `job`, `input`, `output` | the raw facets of that run |
| `GET /api/v1/lineage` | `nodeId` (required), `depth` (default 20), `withDownstream` | graph |
| `GET /api/v1/column-lineage` | `nodeId=datasetField:{ns}:{dataset}:{field}`, `depth`, `withDownstream` | column graph |
| `GET /api/v1/search` | `q`, `filter` (`DATASET` or `JOB`), `sort` (`NAME`, `UPDATE_AT`), `limit`, `namespace` | `results[]` with `type`, `name`, `namespace`, `nodeId`, `updatedAt` |
| `GET /api/v1/events/lineage` | `before`, `after` (ISO), `limit`, `sortDirection` | raw events as received; the source for `scripts/lineage_graph.py` |
| `GET /api/v1/tags` | | tag list (`PII`, `SENSITIVE`, custom) |
| `POST /api/v1/namespaces/{ns}/datasets/{name}/tags/{tag}` | | tag a dataset; `.../fields/{field}/tags/{tag}` for a column |

Node ids: `dataset:{namespace}:{name}`, `job:{namespace}:{name}`,
`datasetField:{namespace}:{name}:{field}`. URL-encode the namespace
(`postgres%3A%2F%2Fwarehouse.internal%3A5432`).

### Lineage graph response

```json
{"graph":[
  {"id":"dataset:postgres://warehouse.internal:5432:raw.orders","type":"DATASET",
   "data":{"type":"DB_TABLE","name":"raw.orders","namespace":"postgres://warehouse.internal:5432","lastLifecycleState":""},
   "inEdges":[{"origin":"job:airflow://prod:ingest_orders.load_raw_orders","destination":"dataset:...:raw.orders"}],
   "outEdges":[{"origin":"dataset:...:raw.orders","destination":"job:dbt://analytics:analytics.fct_orders"}]},
  {"id":"job:dbt://analytics:analytics.fct_orders","type":"JOB",
   "data":{"latestRun":{"state":"COMPLETED","endedAt":"2026-03-07T06:31:00Z"}},"inEdges":[],"outEdges":[]}
]}
```

Walk `outEdges` for downstream impact and `inEdges` for upstream sources.
`depth` counts edges, so a dataset two tables away is at depth 4.

### Useful calls

```bash
# everything downstream of a table, 3 dataset hops
curl -sG "$MARQUEZ_URL/api/v1/lineage" --data-urlencode 'nodeId=dataset:postgres://warehouse.internal:5432:raw.orders' --data-urlencode 'depth=6'
# last 20 runs of a job, newest first
curl -s "$MARQUEZ_URL/api/v1/namespaces/airflow%3A%2F%2Fprod/jobs/reporting.daily_revenue/runs?limit=20"
# raw events of the last day for offline analysis
curl -sG "$MARQUEZ_URL/api/v1/events/lineage" --data-urlencode "after=$(date -u -d '1 day ago' +%FT%TZ)" --data-urlencode 'limit=5000' > events.json
```

`GET /api/v1/events/lineage` returns `{"events": [...], "totalCount": N}`;
extract `events` to JSONL (`jq -c '.events[]'`) before running the scripts.

## DataHub

- Ingest OpenLineage directly: `POST /openapi/openlineage/api/v1/lineage` on
  the GMS (`DATAHUB_GMS_URL`, token in `Authorization: Bearer $DATAHUB_TOKEN`).
- Lineage read: GraphQL `searchAcrossLineage(input: {urn, direction:
  DOWNSTREAM, degree: ["1", "2", "3+"]})` at `/api/graphql`, or REST
  `GET /openapi/v3/entity/dataset/{urn}/relationships?direction=DOWNSTREAM`.
- URNs: `urn:li:dataset:(urn:li:dataPlatform:postgres,warehouse.raw.orders,PROD)`,
  `urn:li:dataJob:(urn:li:dataFlow:(airflow,ingest_orders,prod),load_raw_orders)`.
- Column lineage lives in the `upstreamLineage` aspect, `fineGrainedLineages[]`
  with `upstreams` and `downstreams` schemaField urns.
- Freshness: `operation` aspect (`lastUpdatedTimestamp`, `operationType`) and
  Assertions (`assertionRunEvent`) for SLAs.

## OpenMetadata

- OpenLineage connector consumes events from Kafka or `POST
  /api/v1/openlineage/lineage` (version 1.3+).
- Lineage read: `GET /api/v1/lineage/table/name/{fqn}?upstreamDepth=3&downstreamDepth=3`
  with `Authorization: Bearer $OPENMETADATA_JWT`; response `nodes[]` and
  `upstreamEdges[]`, `downstreamEdges[]` with `fromEntity`, `toEntity`,
  `lineageDetails.columnsLineage[] {fromColumns, toColumn}`.
- Fully qualified names: `service.database.schema.table`.
- Freshness and quality: `GET /api/v1/dataQuality/testCases?entityLink=<#E::table::fqn>`
  and the `tableFreshness` profiler metric.

## Astro, Atlan and others

Astronomer (Astro) exposes Marquez-compatible endpoints at
`https://<org>.astronomer.run/<deployment>/lineage/api/v1`. Atlan, Collibra and
Microsoft Purview accept OpenLineage through their own connectors; their read
APIs are proprietary, so export events at the producer side (file or Kafka
transport) when offline analysis is needed.

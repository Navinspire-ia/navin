# OpenLineage schema: RunEvent, DatasetEvent, JobEvent and facets

Spec: `https://openlineage.io/spec/2-0-2/OpenLineage.json`. Every event is a
JSON object posted to `POST /api/v1/lineage` on the consumer (Marquez,
DataHub, OpenMetadata, Atlan, Astro). Facets are versioned separately under
`https://openlineage.io/spec/facets/<version>/<Name>.json` and every facet
object carries `_producer` and `_schemaURL`.

## RunEvent

| Field | Type | Required | Notes |
|---|---|---|---|
| `eventType` | enum | yes | `START`, `RUNNING`, `COMPLETE`, `ABORT`, `FAIL`, `OTHER` |
| `eventTime` | ISO 8601 with zone | yes | time the state was observed, not the nominal schedule |
| `producer` | URI | yes | the integration that emitted the event, e.g. `https://github.com/apache/airflow/tree/providers-openlineage/1.9.0` |
| `schemaURL` | URI | yes | `https://openlineage.io/spec/2-0-2/OpenLineage.json#/definitions/RunEvent` |
| `run.runId` | UUID string | yes | stable across the START, RUNNING and terminal events of one run |
| `run.facets` | object | no | see run facets |
| `job.namespace` | string | yes | the scheduler or engine, e.g. `airflow://prod`, `dbt://analytics`, `spark://emr-prod` |
| `job.name` | string | yes | Airflow `dag_id.task_id`, dbt `model.project.name` or `schema.model`, Spark `app_name.execution_command` |
| `job.facets` | object | no | see job facets |
| `inputs[]` | InputDataset | no | datasets read; carry `facets` and `inputFacets` |
| `outputs[]` | OutputDataset | no | datasets written; carry `facets` and `outputFacets` |

State machine per `runId`: one `START`, zero or more `RUNNING`, exactly one
terminal event (`COMPLETE`, `FAIL`, `ABORT`). `OTHER` carries extra facets
without changing state. Inputs and outputs are cumulative: the consumer merges
the datasets of all events of the run, so a `COMPLETE` with no `outputs` does
not remove outputs declared on `START`.

## DatasetEvent and JobEvent

Since spec 2.0, an event may describe a dataset or a job without a run:

- `DatasetEvent`: `eventTime`, `producer`, `schemaURL`, `dataset` (namespace,
  name, facets). Used to publish schema or ownership changes from catalogs.
- `JobEvent`: `eventTime`, `producer`, `schemaURL`, `job`, `inputs`,
  `outputs`. Declares static lineage (for example parsed from SQL at deploy
  time) before any run exists.

## Run facets

| Facet | Fields | Use |
|---|---|---|
| `nominalTime` | `nominalStartTime`, `nominalEndTime` | the scheduled interval (Airflow `logical_date`), used for freshness and backfill detection |
| `parent` | `run.runId`, `job.namespace`, `job.name`, optional `root.run.runId` and `root.job` | task to DAG, dbt model to `dbt run` invocation, Spark action to application |
| `errorMessage` | `message`, `programmingLanguage`, `stackTrace` | present on `FAIL`; the root cause string for stale-data investigations |
| `processing_engine` | `version`, `name`, `openlineageAdapterVersion` | which Spark, Flink or Airflow version ran |
| `externalQuery` | `externalQueryId`, `source` | warehouse query id (BigQuery job id, Snowflake query id) |
| `environmentVariables` (unofficial, Spark) | list of `name`, `value` | check for secrets before storing |
| `airflow` (Airflow provider) | `dag`, `dagRun`, `task`, `taskInstance`, `taskUuid` | `taskInstance.try_number` tells retries |
| `dbt_version`, `dbt_run` | version, `invocation_id` | dbt only |
| `spark_properties`, `spark.logicalPlan` | job config and plan | heavy; filter with `spark.openlineage.facets.disabled` |

## Job facets

| Facet | Fields | Use |
|---|---|---|
| `sql` | `query`, `dialect` | the SQL executed; parse it for column lineage when the producer did not |
| `sourceCode` | `language`, `sourceCode` | Python callable of an Airflow task |
| `sourceCodeLocation` | `type` (`git`), `url`, `repoUrl`, `path`, `version`, `tag`, `branch` | links a job to a commit |
| `jobType` | `processingType` (`BATCH`, `STREAMING`), `integration` (`AIRFLOW`, `DBT`, `SPARK`, `FLINK`, `DAGSTER`), `jobType` (`DAG`, `TASK`, `MODEL`, `JOB`, `QUERY`) | filter task-level versus pipeline-level nodes |
| `documentation` | `description`, `contentType` | DAG doc or dbt model description |
| `ownership` | `owners[]` with `name`, `type` | on-call routing |

## Dataset facets (static, on `facets`)

| Facet | Fields | Use |
|---|---|---|
| `schema` | `fields[]` with `name`, `type`, `description`, nested `fields` | detect schema drift between runs; mark PII columns in `description` or with tags |
| `dataSource` | `name`, `uri` | the physical source, equals the namespace by convention |
| `columnLineage` | `fields.<outputColumn>.inputFields[]` with `namespace`, `name`, `field`, optional `transformations[]` (`type`: `DIRECT` or `INDIRECT`, `subtype`: `IDENTITY`, `TRANSFORMATION`, `AGGREGATION`, `JOIN`, `FILTER`, `SORT`, `GROUP_BY`, `WINDOW`, `CONDITIONAL`; `masking` boolean); older producers emit `transformationType` and `transformationDescription` | personal data propagation, impact of a column rename |
| `lifecycleStateChange` | `lifecycleStateChange` (`ALTER`, `CREATE`, `DROP`, `OVERWRITE`, `RENAME`, `TRUNCATE`), `previousIdentifier` | find who dropped or overwrote a table |
| `ownership` | `owners[]` | dataset owner |
| `symlinks` | `identifiers[]` with `namespace`, `name`, `type` (`TABLE`, `TOPIC`) | maps `s3://` paths to Hive or Glue table names |
| `storage` | `storageLayer` (`iceberg`, `delta`), `fileFormat` (`parquet`) | |
| `version` | `datasetVersion` | Iceberg snapshot, Delta version |
| `documentation` | `description` | |

## Input facets (on `inputFacets`)

| Facet | Fields | Use |
|---|---|---|
| `dataQualityMetrics` | `rowCount`, `bytes`, `fileCount`, `columnMetrics.<col>` with `nullCount`, `distinctCount`, `sum`, `count`, `min`, `max`, `quantiles` | Great Expectations, Spark listener |
| `dataQualityAssertions` | `assertions[]` with `assertion`, `success`, `column` | failed expectations on a read |
| `inputStatistics` | `rowCount`, `fileCount`, `size` | |

## Output facets (on `outputFacets`)

| Facet | Fields | Use |
|---|---|---|
| `outputStatistics` | `rowCount`, `size`, `fileCount` | zero rows on a COMPLETE run is the classic silent failure |

## Naming conventions

The pair `(namespace, name)` is the dataset identity across producers. Keep
the convention identical in Airflow, dbt and Spark, or lineage breaks into
disconnected nodes.

| Source | Namespace | Name |
|---|---|---|
| PostgreSQL, MySQL, Redshift, SQL Server | `postgres://host:5432`, `mysql://host:3306`, `redshift://cluster.region:5439`, `sqlserver://host:1433` | `schema.table` (Postgres: `database.schema.table` when several databases) |
| Snowflake | `snowflake://<account>.<region>.aws` (account locator) | `DATABASE.SCHEMA.TABLE` upper case |
| BigQuery | `bigquery` | `project.dataset.table` |
| S3, GCS, Azure Blob | `s3://bucket`, `gs://bucket`, `wasbs://container@account` | path without leading slash, usually the partition root, e.g. `exports/orders/` |
| HDFS | `hdfs://namenode:8020` | path |
| Kafka | `kafka://broker:9092` | topic |
| Hive, Glue | `hive://metastore:9083`, `arn:aws:glue:region:account` | `database.table` |
| Iceberg, Delta on object storage | the storage namespace | path, with `symlinks` to the table name |

Job namespaces: `airflow://<deployment>` or the value of
`[openlineage] namespace`, `dbt://<project>` via `OPENLINEAGE_NAMESPACE`,
`spark://<cluster>` via `spark.openlineage.namespace`.

## Minimal valid events

```json
{"eventType":"START","eventTime":"2026-03-07T06:20:00Z",
 "producer":"https://example.com/my-producer/1.0",
 "schemaURL":"https://openlineage.io/spec/2-0-2/OpenLineage.json#/definitions/RunEvent",
 "run":{"runId":"c2ab3f28-6f0e-4b4a-9f1b-0b1d4a4c1234"},
 "job":{"namespace":"dbt://analytics","name":"analytics.fct_orders"},
 "inputs":[{"namespace":"postgres://warehouse.internal:5432","name":"raw.orders"}],
 "outputs":[{"namespace":"postgres://warehouse.internal:5432","name":"analytics.fct_orders"}]}
```

The terminal event repeats `run.runId`, `job`, and may add `outputFacets`:

```json
{"eventType":"COMPLETE","eventTime":"2026-03-07T06:31:00Z","producer":"...","schemaURL":"...",
 "run":{"runId":"c2ab3f28-6f0e-4b4a-9f1b-0b1d4a4c1234"},
 "job":{"namespace":"dbt://analytics","name":"analytics.fct_orders"},
 "outputs":[{"namespace":"postgres://warehouse.internal:5432","name":"analytics.fct_orders",
   "outputFacets":{"outputStatistics":{"rowCount":48211}}}]}
```

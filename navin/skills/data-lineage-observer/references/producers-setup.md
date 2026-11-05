# Producer setup: Airflow, dbt, Spark, Flink, Great Expectations, Dagster

One rule for every producer: same `(namespace, name)` convention for datasets,
and the transport pointed at the same consumer. Read the config, never edit
it on a live scheduler without approval.

## Shared transport configuration

All Python producers read, in order: `OPENLINEAGE_CONFIG=/path/openlineage.yml`,
then `./openlineage.yml`, then environment variables.

```yaml
# openlineage.yml
transport:
  type: http
  url: http://marquez:5000
  endpoint: api/v1/lineage          # default
  auth:
    type: api_key
    apiKey: ${MARQUEZ_API_KEY}     # read from the environment
  timeout: 5
  compression: gzip
# alternatives: type: kafka (topic, config.bootstrap.servers), type: file (log_file_path, append),
# type: console, type: composite (transports: [...])
```

Environment equivalents: `OPENLINEAGE_URL`, `OPENLINEAGE_API_KEY`,
`OPENLINEAGE_ENDPOINT`, `OPENLINEAGE_NAMESPACE` (job namespace),
`OPENLINEAGE_DISABLED=true` to switch off.

For offline analysis use the `file` transport on a staging scheduler and feed
the resulting JSONL to `scripts/lineage_graph.py`.

## Apache Airflow (2.7+)

Package: `apache-airflow-providers-openlineage` (the old `openlineage-airflow`
plugin is for Airflow < 2.7 and is deprecated).

```ini
# airflow.cfg
[openlineage]
transport = {"type": "http", "url": "http://marquez:5000", "endpoint": "api/v1/lineage"}
namespace = airflow://prod
disabled = False
disabled_for_operators = airflow.operators.bash.BashOperator;airflow.operators.empty.EmptyOperator
include_full_task_info = False
execution_timeout = 10
selective_enable = False
debug_mode = False
```

Environment form: `AIRFLOW__OPENLINEAGE__TRANSPORT`,
`AIRFLOW__OPENLINEAGE__NAMESPACE`, `AIRFLOW__OPENLINEAGE__DISABLED`.

- Job name is `<dag_id>.<task_id>`; the DAG itself is emitted as a parent job
  (`jobType.jobType = DAG`) and tasks carry the `parent` run facet.
- Operators that expose lineage out of the box: `SQLExecuteQueryOperator`
  and subclasses (Postgres, Snowflake, BigQuery, Redshift, Trino),
  `S3CopyObjectOperator`, `GCSToBigQueryOperator`, `SageMaker*`, Python tasks
  through `inlets` and `outlets` (`Dataset("postgres://host:5432/raw.orders")`)
  or by implementing `get_openlineage_facets_on_complete(task_instance)`.
- Check it works: `airflow providers get apache-airflow-providers-openlineage`,
  then look in the task log for `OpenLineage` lines, and on Marquez
  `GET /api/v1/namespaces/airflow%3A%2F%2Fprod/jobs`.
- Common silent failures: `BashOperator` and `PythonOperator` without
  inlets or outlets produce jobs with no datasets; `disabled_for_operators`
  hides tasks; a wrong `namespace` splits the graph.

## dbt

Package: `openlineage-dbt`. Run models through the wrapper so manifest and
run results are parsed into events:

```bash
export OPENLINEAGE_URL=http://marquez:5000
export OPENLINEAGE_NAMESPACE=dbt://analytics
dbt-ol run --select tag:daily
dbt-ol test
dbt-ol build
```

- `dbt-ol` wraps `dbt`; it emits one parent run for the invocation and one
  job per model named `<database>.<schema>.<model>` (or `schema.model`
  depending on adapter), with the `sql` job facet and `columnLineage` when
  `dbt-ol` can parse it (1.20+ uses the SQL parser; otherwise column lineage
  is absent).
- Datasets use the adapter namespace: `postgres://host:5432`,
  `snowflake://<account>`, `bigquery`.
- Tests produce `dataQualityAssertions` on the tested model.
- Structured logs mode (`dbt-ol` 1.26+, `OPENLINEAGE_DBT_USE_STRUCTURED_LOGS=true`)
  streams events while the run progresses instead of after it.
- dbt Cloud: enable the OpenLineage integration in the account settings; the
  producer is `https://github.com/OpenLineage/OpenLineage/tree/.../integration/dbt`.

## Apache Spark

Jar: `io.openlineage:openlineage-spark_2.12:<version>` (or `_2.13`).

```bash
spark-submit \
  --packages io.openlineage:openlineage-spark_2.12:1.23.0 \
  --conf spark.extraListeners=io.openlineage.spark.agent.OpenLineageSparkListener \
  --conf spark.openlineage.transport.type=http \
  --conf spark.openlineage.transport.url=http://marquez:5000 \
  --conf spark.openlineage.transport.auth.type=api_key \
  --conf spark.openlineage.transport.auth.apiKey="$MARQUEZ_API_KEY" \
  --conf spark.openlineage.namespace=spark://emr-prod \
  --conf spark.openlineage.appName=export_orders_parquet \
  --conf spark.openlineage.facets.disabled='[spark_unknown;spark.logicalPlan]' \
  --conf spark.openlineage.columnLineage.datasetLineageEnabled=true \
  --conf spark.openlineage.jobName.appendDatasetName=false \
  app.py
```

- Job name is `<appName>.<execution_command>` (for example
  `export_orders_parquet.execute_insert_into_hadoop_fs_relation_command`);
  one Spark action is one run, with a `parent` facet pointing at the
  application run.
- Dataset names for object storage are the full path
  (`s3://bucket` namespace, `prefix/dt=2026-03-07` name); set
  `spark.openlineage.dataset.removePath.pattern` to strip partition folders
  so hourly writes map to one dataset.
- `spark.logicalPlan` facets are large: disable them in production.
- Databricks: install the jar as a cluster library and set the same confs in
  the cluster Spark config; use `spark.openlineage.transport.auth.apiKey
  {{secrets/scope/key}}`.
- Verify: the driver log prints `OpenLineageSparkListener` on start;
  `GET /api/v1/namespaces/spark%3A%2F%2Femr-prod/jobs` lists the jobs.

## Apache Flink

Jar `io.openlineage:openlineage-flink:<version>`; register
`OpenLineageFlinkJobListener` on the `StreamExecutionEnvironment`, configure
`openlineage.transport.*` in `openlineage.yml` or `flink-conf.yaml` keys
`execution.job-listeners`. Flink emits `START`, periodic `RUNNING` with
checkpoint facets, and the terminal state. Kafka datasets are
`kafka://broker:9092` plus topic name.

## Great Expectations

`openlineage-integration-common` with the `OpenLineageValidationAction` in
the checkpoint:

```yaml
action_list:
  - name: openlineage
    action:
      class_name: OpenLineageValidationAction
      module_name: openlineage.common.provider.great_expectations
      openlineage_host: http://marquez:5000
      openlineage_apiKey: ${MARQUEZ_API_KEY}
      openlineage_namespace: great_expectations://prod
      job_name: orders_suite
```

Emits one run per checkpoint with `dataQualityMetrics` and
`dataQualityAssertions` input facets on the validated dataset.

## Dagster

Package `dagster-openlineage` (community) or the native `dagster-airlift`
lineage; set `OPENLINEAGE_URL` and `OPENLINEAGE_NAMESPACE=dagster://prod`. Assets map
to datasets by `AssetKey` (`["raw", "orders"]` becomes `raw.orders`); set
`metadata={"openlineage": {"namespace": "postgres://host:5432"}}` on the asset
to override the namespace.

## Checklist after enabling a producer

1. One event with `eventType: START` and one terminal event per run id.
2. Dataset namespaces match across producers (grep the export for
   `"namespace":` and list distinct values).
3. No secrets in `environmentVariables`, `spark_properties` or `sql` facets.
4. `nominalTime` present on scheduled jobs, otherwise freshness checks fall
   back to `eventTime`.
5. `columnLineage` present where personal data flows; otherwise plan SQL
   parsing from the `sql` facet.

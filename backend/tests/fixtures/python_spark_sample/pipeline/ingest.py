"""Minimal Spark ingestion job used only by the SQL-lineage end-to-end
integration test - proves a real, full `index_repository` run produces the
Function -> READS -> DataTable and Function -> WRITES -> DataTable edges."""


def run_ingest():
    spark.sql(
        "INSERT INTO catalog.schema.customer_gold "
        "SELECT * FROM catalog.schema.customer_bronze"
    )

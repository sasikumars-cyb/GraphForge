"""Static registry of `.sql` filenames - proves the Python Module ->
LOADS_SQL -> SqlFile -> READS_FROM -> DataTable chain end-to-end."""

SQL_FILE_MAP = {
    "customers": "customers.sql",
}

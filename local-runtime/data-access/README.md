# Excel intake pilot — incomplete

This is a separate prototype for the proposed spreadsheet ingestion capability.
It is not installed by the local runtime and is not an operational MCP server.

The Rust CLI uses calamine to inspect workbook sheets and extract explicitly
contracted fields, with typed values, source hashes and cell addresses. TOML
declares the expected schema; bounded Rust regex validates string parameters.
It refuses formula-bearing input instead of silently treating cached formula
results as authoritative. Three Rust tests passed during development.

The SQL registry sketches the intended reviewed-query interface. DuckDB, MCP and
optional marimo are declarations in pyproject.toml, not a completed worker or a
verified dependency closure. There is no real client workbook fixture, Parquet
writer, query-approval/memoization workflow, client database connection, ODBC or
Oracle driver profile, Spark runtime or end-to-end proof. Those remain work.

Review the crate's intake bounds and contract interpretation before using it with
untrusted spreadsheets. File size and row limits are pilot policy choices, not a
complete defense against malicious compressed workbooks. Regex matching does not
authorize access to data. See the parent shift handoff for the larger architecture.

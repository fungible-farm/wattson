select cast(ts as timestamp) as date_day from generate_series(timestamp '2025-08-01', timestamp '2025-10-31', interval 1 day) t(ts)

-- time spine at minute grain covering the data window plus margin (2025-08-30 .. 2025-09-04)
select cast(ts as timestamp) as date_minute from generate_series(timestamp '2025-08-30 00:00:00', timestamp '2025-09-04 00:00:00', interval 1 minute) t(ts)

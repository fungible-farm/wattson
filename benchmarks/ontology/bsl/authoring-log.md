# Authoring log (candidate C, Boring Semantic Layer 0.3.18)

HONESTY NOTE: timestamps were NOT recorded contemporaneously with `date -u`; they are reconstructed from file mtimes (local time, UTC+11) and tool-call
timings. Treat the minutes as an estimate (+/- 30%).

| Piece | Local time (UTC+11) | UTC | Minutes |
| --- | --- | --- | --- |
| Read library source/docs in the image (yaml.py, query.py, predicate.py), build probe image | 14:02-14:11 | 03:02-03:11 | 9 |
| Semantic model `model.py` (4 semantic models over 5 tables, programmatic measures) + Containerfile, requirements | 14:11-14:14 | 03:11-03:14 | 3 |
| `runner.py` (describe, validate names/operators, execute, compiled SQL) + describe fix for joined models | 14:14-14:25 | 03:14-03:25 | 10 |
| Adapter `adapter_bsl.py` | ~14:25-14:28 | 03:25-03:28 | 3 |
| Gold specs, `run_spec`, stage reporting, M7/M8/M9 chaining attempts, `chains.py` | 14:35-14:52 | 03:35-03:52 | 17 |
| Total | | | 42 |

The semantic model itself (`model.py`, 106 lines) was written once, in one pass, before any question was run against it, and has not been changed since 14:14.

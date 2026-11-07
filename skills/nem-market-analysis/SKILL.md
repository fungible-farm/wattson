---
name: nem-market-analysis
description: Use when answering questions about NEM prices, demand, generation, interconnector flows or constraints from AEMO MMS data, or when an AEMO system-design or operations analysis needs numbers that must be reproducible and cited.
---

# NEM market analysis

The model plans; deterministic containers compute; every number is traceable to a command and a provenance record.

## Fetch (network, container only)

```sh
just nemosis-fetch DISPATCHPRICE "2025/09/01 00:00:00" "2025/09/02 00:00:00" REGIONID=SA1
```

This runs NEMOSIS (GPL-3.0) inside its own container and writes `out/nemosis/<TABLE>.parquet` plus `<TABLE>.provenance.json` (nemosis version, arguments, rows, sha256). Never import NEMOSIS into wattson code; read the parquet.

## Compute (no network)

Run ONE read-only SQL statement over the parquet or over `out/nem/nem.duckdb` (`just nem-data` builds it) with the `wattson-nem` container `query` entrypoint. Settlement time is the END of the interval. Do not compute in your head: if a figure is not in a SQL result, it is an estimate and must be labelled one.

## Activities this skill covers, and the tables behind each

| Activity | NEMOSIS tables | Status |
|---|---|---|
| Price and demand, spike and negative-price events | DISPATCHPRICE, DISPATCHREGIONSUM | fetch + reconcile validated |
| Fuel mix, capacity factor | DISPATCHLOAD, DISPATCH_UNIT_SCADA, static Generators and Scheduled Loads | planned |
| Interconnector flow and congestion | DISPATCHINTERCONNECTORRES | planned |
| Binding-constraint analysis | DISPATCHCONSTRAINT, GENCONDATA, SPD* | planned |
| Bid stack, supply curve (gap March 2021 to July 2024) | BIDPEROFFER_D, BIDDAYOFFER_D | planned |
| FCAS price and causer-pays | DISPATCHPRICE (FCAS columns), FCAS_4_SECOND | planned |
| Unit lookup, DUID to CSIRO synthetic network | DUDETAIL, DUDETAILSUMMARY | planned |

## Verify

`just nemosis-reconcile` compares NEMOSIS prices with the independent AEMO CSV data; PASS means zero differing intervals. The procedural chart is `docs/diagrams/nemosis-fetch.d2`.

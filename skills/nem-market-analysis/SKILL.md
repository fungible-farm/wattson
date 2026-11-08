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
| Interconnector flow and congestion | DISPATCHINTERCONNECTORRES | `just nem-interconnector` (chart `docs/diagrams/nem-interconnector.d2`) |
| Binding-constraint analysis | DISPATCHCONSTRAINT, GENCONDATA, SPD* | planned |
| Bid stack, supply curve (gap March 2021 to July 2024) | BIDPEROFFER_D, BIDDAYOFFER_D (+ DISPATCHPRICE, DUDETAILSUMMARY, DISPATCHREGIONSUM) | `just nem-bidstack` (chart `docs/diagrams/nem-bidstack.d2`) |
| FCAS price | DISPATCHPRICE (FCAS columns) | `just nem-fcas` (chart `docs/diagrams/nem-fcas.d2`); causer-pays (FCAS_4_SECOND) not buildable now: NEMOSIS returned NoDataToReturn for four 10-minute windows (2025-09-01, 2026-09-01, 2026-10-20, 2026-11-01), so there is no data to validate against |
| Unit lookup, DUID to region and station | DUDETAILSUMMARY (+ DISPATCHLOAD for coverage) | `just nem-unit-lookup` (chart `docs/diagrams/nem-unit-lookup.d2`); mapping onto the CSIRO synthetic network is still planned |

## Not done, and why

- DUID to the CSIRO synthetic network: the CSIRO network is synthetic and publishes no DUID mapping, so any mapping would be a capacity-rank heuristic with nothing to validate against. Not built.
- Fuel mix and capacity factor: needs AEMO's registration workbook, which AEMO serves only to a browser; a person must save it (see `scripts/nemosis_fetch.py`, branch `feat/nem-fuel-mix`).

## Verify

`just nemosis-reconcile` compares NEMOSIS prices with the independent AEMO CSV data; PASS means zero differing intervals. The procedural chart is `docs/diagrams/nemosis-fetch.d2`.

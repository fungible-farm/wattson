#!/usr/bin/env python3
"""Emit models/semantic_models.yml from a column catalogue (the five NEMOSIS tables, schema file only). Stdlib only.
Rule: every numeric fact column gets measures, every key/category column a dimension/entity, one simple metric per measure."""
import json
import os

CORE = ("sum", "average", "max", "min")
LESS = ("average", "max")
PRICE = ("average", "max", "min")
# table -> (primary time column, entities [(name, expr, type)], categorical dims, [(column, aggs, description)])
T = {
 "dispatchload": ("SETTLEMENTDATE", [("duid", "DUID", "foreign")], ["INTERVENTION", "DISPATCHMODE", "AGCSTATUS", "SEMIDISPATCHCAP"],
   [("TOTALCLEARED", CORE, "dispatched MW, negative when charging/consuming"), ("INITIALMW", CORE, "initial MW"), ("AVAILABILITY", CORE, "MW availability"),
    ("RAMPUPRATE", LESS, "ramp up MW/min"), ("RAMPDOWNRATE", LESS, "ramp down MW/min")] +
   [(c, LESS, "FCAS enablement MW") for c in ("LOWER5MIN", "LOWER60SEC", "LOWER6SEC", "LOWER1SEC", "RAISE5MIN", "RAISE60SEC", "RAISE6SEC", "RAISE1SEC", "LOWERREG", "RAISEREG",
    "RAISEREGENABLEMENTMAX", "RAISEREGENABLEMENTMIN", "LOWERREGENABLEMENTMAX", "LOWERREGENABLEMENTMIN")]),
 "dispatchprice": ("SETTLEMENTDATE", [("region", "REGIONID", "foreign")], ["INTERVENTION", "PRICE_STATUS"],
   [(c, PRICE, "regional price $/MWh" if c == "RRP" else "FCAS price $/MW") for c in ("RRP", "RAISE6SECRRP", "RAISE60SECRRP", "RAISE5MINRRP", "RAISEREGRRP", "LOWER6SECRRP", "LOWER60SECRRP", "LOWER5MINRRP", "LOWERREGRRP")]),
 "dispatchregionsum": ("SETTLEMENTDATE", [("region", "REGIONID", "foreign")], ["INTERVENTION", "DISPATCHINTERVAL"],
   [("TOTALDEMAND", CORE, "regional demand MW"), ("NETINTERCHANGE", CORE, "net interchange MW, positive = exporting"), ("AVAILABLEGENERATION", LESS, "MW"),
    ("AVAILABLELOAD", LESS, "MW"), ("DEMANDFORECAST", LESS, "MW"), ("DISPATCHABLEGENERATION", LESS, "MW"), ("DISPATCHABLELOAD", LESS, "MW"), ("EXCESSGENERATION", LESS, "MW"),
    ("INITIALSUPPLY", LESS, "MW"), ("CLEAREDSUPPLY", LESS, "MW"), ("TOTALINTERMITTENTGENERATION", LESS, "MW"), ("DEMAND_AND_NONSCHEDGEN", LESS, "MW"), ("UIGF", LESS, "MW"),
    ("SEMISCHEDULE_CLEAREDMW", LESS, "MW"), ("SEMISCHEDULE_COMPLIANCEMW", LESS, "MW")]),
 "dispatchinterconnectorres": ("SETTLEMENTDATE", [("interconnector", "INTERCONNECTORID", "foreign")], ["INTERVENTION", "DISPATCHINTERVAL"],
   [("MWFLOW", CORE, "flow MW, positive = forward direction"), ("MWLOSSES", CORE, "losses MW"), ("METEREDMWFLOW", LESS, "metered flow MW"), ("EXPORTLIMIT", LESS, "export limit MW"), ("IMPORTLIMIT", LESS, "import limit MW")]),
 "dudetailsummary": ("START_DATE", [("duid", "DUID", "primary"), ("station", "STATIONID", "foreign"), ("region", "REGIONID", "foreign"), ("participant", "PARTICIPANTID", "foreign")],
   ["DISPATCHTYPE", "CONNECTIONPOINTID", "STARTTYPE", "SCHEDULE_TYPE", "REGIONID", "STATIONID", "PARTICIPANTID"],
   [("TRANSMISSIONLOSSFACTOR", LESS, "loss factor"), ("DISTRIBUTIONLOSSFACTOR", LESS, "loss factor"), ("MAX_RAMP_RATE_UP", LESS, "MW/min"), ("MAX_RAMP_RATE_DOWN", LESS, "MW/min")]),
}
TIMES = {"dudetailsummary": ["END_DATE", "LASTCHANGED"]}
DESC = {"REGIONID": "NEM region id: NSW1, QLD1, SA1, TAS1, VIC1", "STATIONID": "power station id", "INTERVENTION": "0 = normal dispatch run (the only one loaded)"}
AGGN = {"sum": "total", "average": "avg", "max": "max", "min": "min"}
out = ["semantic_models:"]
metrics = ["metrics:"]
for t, (tc, ents, cats, facts) in T.items():
    out += [f"  - name: {t}", f"    model: ref('{t}')", f"    description: AEMO {t.upper()} table (NEMOSIS), 2025-09-01; intervention 0 only.", *([f"    primary_entity: {t}_record"] if t != "dudetailsummary" else []), "    defaults:", f"      agg_time_dimension: {tc.lower()}",
            "    entities:"]
    for n, e, ty in ents:
        out += [f"      - name: {n}", f"        type: {ty}", f"        expr: {e}"]
    out += ["    dimensions:", f"      - name: {tc.lower()}", "        type: time", "        description: \"END of the 5-minute dispatch interval\"", "        type_params:", "          time_granularity: minute"]
    for x in TIMES.get(t, []):
        out += [f"      - name: {x.lower()}", "        type: time", "        type_params:", "          time_granularity: minute"]
    for c in cats:
        out += [f"      - name: {c.lower()}", "        type: categorical", f"        expr: {c}"] + ([f"        description: {json.dumps(DESC[c])}"] if c in DESC else [])
    out += ["    measures:"]
    ms = []
    keycol = ents[0][1]
    ms.append((f"{t}_row_count", "sum", "1", f"number of {t} rows"))
    ms.append((f"{t}_{ents[0][0]}_distinct_count", "count_distinct", keycol, f"distinct {ents[0][0]} values"))
    if t == "dispatchload":
        ms.append(("dispatchload_interval_distinct_count", "count_distinct", "SETTLEMENTDATE", "distinct 5-minute intervals"))
        ms.append(("dispatchload_cleared_energy_mwh", "sum", "TOTALCLEARED * 5.0 / 60.0", "net energy MWh = MW * 5/60, negatives included"))
    if t == "dudetailsummary":
        for n, e, ty in ents[1:]:
            ms.append((f"dudetailsummary_{n}_distinct_count", "count_distinct", e, f"distinct {n} values"))
    for col, aggs, d in facts:
        for a in aggs:
            ms.append((f"{t}_{col.lower()}_{AGGN[a]}", a, col, f"{AGGN[a]} of {col} ({d})"))
    for name, agg, expr, d in ms:
        out += [f"      - name: {name}", f"        agg: {agg}", f"        expr: {expr}", f"        description: {json.dumps(d)}"]
        metrics += [f"  - name: {name}", f"    label: {name}", f"    description: {d}", "    type: simple", "    type_params:", f"      measure: {name}"]
for n, col, src in (("region", "REGIONID", "dispatchregionsum"), ("station", "STATIONID", "dudetailsummary"), ("interconnector", "INTERCONNECTORID", "dispatchinterconnectorres")):
    out[1:1] = [f"  - name: {n}_dim", f"    model: ref('dim_{n}')", f"    description: key table of distinct {n} ids", "    entities:", f"      - name: {n}", "        type: primary", f"        expr: {col}",
                "    dimensions:", f"      - name: {col.lower()}", "        type: categorical", f"        expr: {col}"] + ([f"        description: {json.dumps(DESC[col])}"] if col in DESC else [])
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "semantic_models.yml"), "w").write("\n".join(out + [""] + metrics) + "\n")

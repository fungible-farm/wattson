"""Semantic model (Boring Semantic Layer 0.3.18, Python API) over the five NEMOSIS tables mounted at /nemosis.

Authored from configs/nem-multitable-schema.v2.txt and the parquet column lists only. General rules, no question-specific measures:
  * every key / time / name column is a dimension; every numeric fact column gets avg/min/max (and sum where a sum is physically meaningful);
  * counts and distinct counts on the key columns; MWh energy = MW * 5/60 as stated in the schema;
  * the schema's standing rule INTERVENTION = 0 is a model-level filter on the fact tables.
"""
import ibis

NEMOSIS = "/nemosis"
from boring_semantic_layer import to_semantic_table, time_dimension  # noqa: E402
from ibis import _  # noqa: E402

ROOTS = {}  # root semantic tables by name (used by the runner to describe joined models)


def T(col):
    return time_dimension(getattr(_, col), f"{col}: END of the 5-minute interval", smallest_time_grain="TIME_GRAIN_SECOND")


def _agg(col, kinds):
    out = {}
    for k in kinds:
        out[f"{col}_{k}"] = {
            "sum": getattr(_, col).sum(), "avg": getattr(_, col).mean(),
            "min": getattr(_, col).min(), "max": getattr(_, col).max(),
        }[k]
    return out


def _measures(cols_summable, cols_other):
    m = {}
    for c in cols_summable:
        m.update(_agg(c, ["sum", "avg", "min", "max"]))
    for c in cols_other:
        m.update(_agg(c, ["avg", "min", "max"]))
    return m


def build(con=None):
    con = con or ibis.duckdb.connect()
    t = {n: con.read_parquet(f"{NEMOSIS}/{n.upper()}.parquet", table_name=n).rename(str.lower)
         for n in ("dispatchload", "dudetailsummary", "dispatchprice", "dispatchregionsum", "dispatchinterconnectorres")}

    # --- unit dispatch (one row per unit per 5-min interval) ---
    dl = to_semantic_table(t["dispatchload"].filter(_.intervention == 0), name="dispatchload",
                           description="Dispatch targets per unit (DUID) per 5-minute interval; TOTALCLEARED MW may be negative (charging).")
    dl = dl.with_dimensions(
        settlementdate=T('settlementdate'), duid=_.duid, intervention=_.intervention,
        dispatchmode=_.dispatchmode, agcstatus=_.agcstatus, semidispatchcap=_.semidispatchcap,
    ).with_measures(
        row_count=_.count(), unit_count=_.duid.nunique(), interval_count=_.settlementdate.nunique(),
        totalcleared_energy_mwh=_.totalcleared.sum() * 5 / 60,
        availability_energy_mwh=_.availability.sum() * 5 / 60,
        **_measures(["totalcleared", "availability", "initialmw", "lower5min", "lower60sec", "lower6sec", "lower1sec", "raise5min", "raise60sec",
                     "raise6sec", "raise1sec", "lowerreg", "raisereg"],
                    ["rampdownrate", "rampuprate", "raiseregenablementmax", "raiseregenablementmin", "lowerregenablementmax", "lowerregenablementmin"]),
    )

    du = to_semantic_table(t["dudetailsummary"], name="dudetailsummary",
                           description="Unit details: maps each DUID to its region, station and participant (one row per DUID in this data).")
    du = du.with_dimensions(
        duid=_.duid, regionid=_.regionid, stationid=_.stationid, participantid=_.participantid, dispatchtype=_.dispatchtype,
        connectionpointid=_.connectionpointid, starttype=_.starttype, schedule_type=_.schedule_type,
        start_date=_.start_date, end_date=_.end_date,
    ).with_measures(
        duid_count=_.duid.nunique(), station_count=_.stationid.nunique(), region_count=_.regionid.nunique(),
        participant_count=_.participantid.nunique(), row_count=_.count(),
        **_measures([], ["transmissionlossfactor", "distributionlossfactor", "max_ramp_rate_up", "max_ramp_rate_down"]),
    )

    # relationship: each dispatch row has at most one unit-details row (join on DUID)
    unit_dispatch = dl.join_one(du, on=lambda l, r: l.duid == r.duid)

    # --- region price ---
    pr = to_semantic_table(t["dispatchprice"].filter(_.intervention == 0), name="dispatchprice",
                           description="Regional reference price (RRP, $/MWh) and FCAS prices per region per 5-minute interval.")
    pr = pr.with_dimensions(settlementdate=T('settlementdate'), regionid=_.regionid, intervention=_.intervention, price_status=_.price_status
                            ).with_measures(
        row_count=_.count(), interval_count=_.settlementdate.nunique(), region_count=_.regionid.nunique(),
        **_measures([], ["rrp", "raise6secrrp", "raise60secrrp", "raise5minrrp", "raiseregrrp", "lower6secrrp", "lower60secrrp", "lower5minrrp", "lowerregrrp"]),
    )

    rs = to_semantic_table(t["dispatchregionsum"].filter(_.intervention == 0), name="dispatchregionsum",
                           description="Regional demand and supply summary in MW per region per interval; NETINTERCHANGE positive = exporting.")
    rs = rs.with_dimensions(settlementdate=T('settlementdate'), regionid=_.regionid, intervention=_.intervention, dispatchinterval=_.dispatchinterval
                            ).with_measures(
        row_count=_.count(), interval_count=_.settlementdate.nunique(), region_count=_.regionid.nunique(),
        **_measures(["totaldemand", "netinterchange", "availablegeneration", "availableload", "demandforecast", "dispatchablegeneration",
                     "dispatchableload", "excessgeneration", "initialsupply", "clearedsupply", "totalintermittentgeneration",
                     "demand_and_nonschedgen", "uigf", "semischedule_clearedmw", "semischedule_compliancemw",
                     "lower5minlocaldispatch", "lower60seclocaldispatch", "lower6seclocaldispatch", "raise5minlocaldispatch",
                     "raise60seclocaldispatch", "raise6seclocaldispatch", "lowerreglocaldispatch", "raisereglocaldispatch"], []),
    )
    # relationship: price and region summary share (settlementdate, regionid, intervention)
    regional = pr.join_one(rs, on=lambda l, r: (l.settlementdate == r.settlementdate) & (l.regionid == r.regionid) & (l.intervention == r.intervention))

    ic = to_semantic_table(t["dispatchinterconnectorres"].filter(_.intervention == 0), name="dispatchinterconnectorres",
                           description="Interconnector flow in MW per interconnector per interval; positive MWFLOW is the forward direction.")
    ic = ic.with_dimensions(settlementdate=T('settlementdate'), interconnectorid=_.interconnectorid, intervention=_.intervention, dispatchinterval=_.dispatchinterval
                            ).with_measures(
        row_count=_.count(), interval_count=_.settlementdate.nunique(), interconnector_count=_.interconnectorid.nunique(),
        **_measures(["mwflow", "mwlosses", "meteredmwflow"], ["exportlimit", "importlimit"]),
    )
    ROOTS.update(dispatchload=dl, dudetailsummary=du, dispatchprice=pr, dispatchregionsum=rs, dispatchinterconnectorres=ic)
    return {"unit_dispatch": unit_dispatch, "unit_details": du, "regional_price_demand": regional, "interconnector_flow": ic}

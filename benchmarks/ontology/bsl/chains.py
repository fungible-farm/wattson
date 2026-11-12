"""Multi-step / post-aggregation programs written against BSL's Python API (chained semantic expressions).
They are NOT expressible in the single JSON query spec {dimensions, measures, filters, order_by, limit}; they exist for Path B (model-free) to show what
the library itself can do. Each is authored from the question text and the semantic model only. Glue code that is plain Python (not BSL) is marked GLUE."""
import ibis


def _sql(expr):
    return ibis.to_sql(expr.to_untagged(), dialect="duckdb")


def m7(models):
    # step 1: interval of the highest SA1 price (semantic query); step 2: SA1 unit with the highest TOTALCLEARED in that interval (semantic query)
    step1 = models["regional_price_demand"].query(
        dimensions=["dispatchprice.settlementdate"], measures=["dispatchprice.rrp_max"],
        filters=[{"field": "dispatchprice.regionid", "operator": "=", "value": "SA1"}],
        order_by=[("dispatchprice.rrp_max", "desc")], limit=1)
    interval = str(step1.execute().iloc[0, 0])  # GLUE: value hand-off between two semantic queries
    step2 = models["unit_dispatch"].query(
        dimensions=["dudetailsummary.duid"], measures=["dispatchload.totalcleared_max"],
        filters=[{"field": "dudetailsummary.regionid", "operator": "=", "value": "SA1"},
                 {"field": "dispatchload.settlementdate", "operator": "=", "value": interval}],
        order_by=[("dispatchload.totalcleared_max", "desc")], limit=1)
    df = step2.execute()
    return df.values.tolist(), [_sql(step1), _sql(step2)]


def m8(models):
    # share of NSW1 net energy held by its five largest units: one semantic chain (group, mutate share, order, limit, aggregate)
    e = "dispatchload.totalcleared_energy_mwh"
    base = models["unit_dispatch"].query(dimensions=["dudetailsummary.duid"], measures=[e],
                                         filters=[{"field": "dudetailsummary.regionid", "operator": "=", "value": "NSW1"}])
    chain = (base.mutate(share=lambda t: t[e] / t[e].sum()).order_by(ibis.desc(e)).limit(5)
             .group_by().aggregate(lambda t: t.share.sum()))
    return chain.execute().values.tolist(), [_sql(chain)]


def m9(models):
    # intervals where |sum of NETINTERCHANGE over regions| > 50: aggregate by interval, filter on the aggregate, count the intervals
    c = "dispatchregionsum.netinterchange_sum"
    chain = (models["regional_price_demand"].query(dimensions=["dispatchregionsum.settlementdate"], measures=[c])
             .filter(lambda t: t[c].abs() > 50).group_by().aggregate(lambda t: t.count()))
    return chain.execute().values.tolist(), [_sql(chain)]


PROGRAMS = {"M7": m7, "M8": m8, "M9": m9}

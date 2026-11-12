"""Container entrypoint: `describe` prints the model description derived from the BSL objects; `query` reads one JSON spec on stdin,
validates every name against the model, executes through BSL's query API and prints {columns, rows, sql} or {error}. No network, read-only data."""
import json
import sys

sys.path.insert(0, "/opt/bsl")
import ibis  # noqa: E402
import chains  # noqa: E402,F401
import model as semantic  # noqa: E402

OPS = {"=", "!=", ">", ">=", "<", "<=", "like", "not like", "ilike", "not ilike", "in", "not in", "is null", "is not null"}


def raw_columns(m):
    """Source columns that are neither dimensions nor measures (usable in filters). Joined models use `<root>.<column>` names."""
    known = set(m.get_dimensions()) | set(m.get_measures()) | set(m.get_calculated_measures())
    prefixes = sorted({k.split(".")[0] for k in known if "." in k})
    if not prefixes:
        return [c for c in m.schema.names if c not in known]
    out = []
    for p in prefixes:
        root = semantic.ROOTS[p]
        rk = set(root.get_dimensions()) | set(root.get_measures())
        out += [f"{p}.{c}" for c in root.schema.names if c not in rk]
    return out


def dtype(m, name):
    if name in m.schema.names:
        return str(m.schema[name])
    p, _, rest = name.partition(".")
    return str(semantic.ROOTS[p].schema[rest]) if p in semantic.ROOTS else "?"


def describe(models):
    out = []
    for name, m in models.items():
        out.append({
            "model": name,
            "dimensions": {d: dtype(m, d) for d in m.get_dimensions()},
            "measures": {x: dtype(m, x) for x in m.get_measures()},
            "filter_only_columns": {c: dtype(m, c) for c in raw_columns(m)},
        })
    return out


def check_filter(f, allowed, errors):
    if not isinstance(f, dict):
        errors.append(f"filter must be an object: {f!r}")
        return
    op = f.get("operator")
    if op in ("AND", "OR"):
        conds = f.get("conditions")
        if not isinstance(conds, list) or not conds:
            errors.append("compound filter needs conditions")
        for c in conds or []:
            check_filter(c, allowed, errors)
        return
    if op not in OPS:
        errors.append(f"unsupported operator {op!r}")
    if f.get("field") not in allowed:
        errors.append(f"unknown filter field {f.get('field')!r}")
    if op in ("in", "not in") and not isinstance(f.get("values"), list):
        errors.append("in/not in needs a values list")


def run(models, spec):
    if "program" in spec:  # Path B only: chained BSL Python-API programs (chains.py), not model-producible
        import chains
        try:
            rows, sqls = chains.PROGRAMS[spec["program"]](models)
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {str(e)[:400]}", "stage": "compile"}
        return {"columns": [], "rows": json.loads(json.dumps(rows, default=str)), "sql": "\n;\n".join(sqls)}
    name = spec.get("model")
    if name not in models:
        return {"error": f"unknown model {name!r}; models: {sorted(models)}", "stage": "represent"}
    m = models[name]
    dims, meas = set(m.get_dimensions()), set(m.get_measures())
    allowed = dims | meas | set(raw_columns(m))
    errors = []
    d, ms = spec.get("dimensions") or [], spec.get("measures") or []
    errors += [f"unknown dimension {x!r}" for x in d if x not in dims]
    errors += [f"unknown measure {x!r}" for x in ms if x not in meas]
    if not d and not ms:
        errors.append("need at least one dimension or measure")
    filters = spec.get("filters") or []
    for f in filters:
        check_filter(f, allowed, errors)
    order = []
    for o in spec.get("order_by") or []:
        field, direction = (o[0], o[1]) if isinstance(o, (list, tuple)) and len(o) == 2 else (o, "asc")
        if field not in dims | meas or direction not in ("asc", "desc"):
            errors.append(f"bad order_by {o!r}")
        order.append((field, direction))
    limit = spec.get("limit")
    if limit is not None and (not isinstance(limit, int) or limit < 1 or limit > 1000):
        errors.append("limit must be an integer 1..1000")
    if errors:
        return {"error": "; ".join(errors), "stage": "represent"}
    try:
        q = m.query(dimensions=d, measures=ms, filters=filters, order_by=order or None, limit=limit)
        sql = ibis.to_sql(q.to_untagged(), dialect="duckdb")
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:400]}", "stage": "compile"}
    try:
        df = q.execute()
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:400]}", "stage": "execute", "sql": sql}
    return {"columns": list(df.columns), "rows": json.loads(df.to_json(orient="values", date_format="iso")), "sql": sql}


if __name__ == "__main__":
    models = semantic.build()
    if sys.argv[1] == "describe":
        print(json.dumps(describe(models)))
    else:
        try:
            print(json.dumps(run(models, json.loads(sys.stdin.read()))))
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"error": f"{type(e).__name__}: {str(e)[:400]}", "stage": "execute"}))

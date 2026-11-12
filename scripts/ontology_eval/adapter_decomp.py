#!/usr/bin/env python3
"""Decomposition adapter for candidate evaluation #128: the model emits a short JSON plan of dependent read-only SQL
steps over the governed views (benchmarks/ontology/decomp/views.sql); the executor validates and runs each step in the
no-network DuckDB container used by arm1, feeding each step's frozen result to later steps.

Path B (run_spec) takes the same plan shape and runs it with no model call at all.
"""
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import demo_timeseries_qa as arm1  # noqa: E402

BASE = os.environ.get("EVAL_BASE_URL", "http://127.0.0.1:8000/v1")
MODEL = os.environ.get("EVAL_MODEL", "qwen3.8-flash-next")
MAX_STEPS = 4
VIEW_DOC_MAX_CHARS = 6000
LAST_FINISH = None  # finish_reason of the most recent _chat call (set in _chat)

_V = None  # view -> [(column, type, comment)]


def _load_view_doc():
    """Column list + SQL comments of the governed views, read from the DuckDB file the executor uses (no hard-coded copy)."""
    global _V
    if _V is not None:
        return _V
    q = ("select table_name, column_name, data_type, coalesce(comment,'') from duckdb_columns() "
         "where schema_name='main' and table_name in (%s) order by table_name, column_index"
         % ",".join("'%s'" % v for v in ["interval_energy", "unit_dim", "unit_region", "price_interval",
                                         "interchange_interval", "region_reserve", "day_interval"]))
    r = arm1.run_sql(q)
    cols = {}
    for row in (r.get("rows") or []):
        cols.setdefault(row[0], []).append((row[1], row[2], row[3]))
    if not cols:
        sys.stderr.write("adapter_decomp: view doc is empty (%s) - every step will be rejected\n" % r.get("error"))
    _V = cols
    return _V


def view_doc():
    lines = []
    for name, cols in sorted(_load_view_doc().items()):
        lines.append(f"VIEW {name}(" + ", ".join(c for c, _, _ in cols) + ")")
        for c, t, comment in cols:
            lines.append(f"  {c} {t}" + (f"  -- {comment}" if comment else ""))
    text = "\n".join(lines)
    return text[:VIEW_DOC_MAX_CHARS]


PREAMBLE = (
    "You answer one question about NEM electricity settlement data for 1 September 2025 by DECOMPOSING it into a short "
    "plan of read-only SQL steps, each a single DuckDB SELECT over the governed views below. You may not write SQL against "
    "anything else.\n\n"
    "GOVERNED VIEWS (the only relations you may read; column comments carry the conventions):\n"
    "{views}\n\n"
    "PLAN FORMAT - reply with ONE JSON object and nothing else:\n"
    '  {"steps":[{"id":"s1","sql":"SELECT ..."},{"id":"s2","sql":"SELECT ... FROM s1 ..."}],"final":"s2"}\n'
    "  - at most 4 steps; each step is one SELECT or WITH statement (no semicolon, no keywords other than SELECT/WITH/AS/"
    "AND/OR/NOT/IN/IS/NULL/TRUE/FALSE/CASE/WHEN/THEN/ELSE/END/ON/USING/BY/JOIN/INNER/LEFT/RIGHT/FULL/CROSS/NATURAL/"
    "UNION/ALL/DISTINCT/ORDER/GROUP/HAVING/LIMIT/EXCEPT/INTERSECT/WINDOW/OVER/FILTER/LIKE/BETWEEN/CAST/INTERVAL/DESC/ASC); "
    "every aggregate needs a GROUP BY except when you aggregate the whole view.\n"
    "  - a later step reads an earlier step by its id as if it were a table; step ids must be s1, s2, s3, s4 in order and "
    "a step can only read steps before it.\n"
    "  - decompose only where it genuinely helps: one step when one query does it, otherwise the smallest number of steps "
    "that makes each stage explicit. The final step must return the answer: a single row holding every number or name the "
    "question asks for, at most 3 rows, no extra columns.\n"
    '  - if the question cannot be answered from these views (a column or concept that does not exist, a date outside '
    "1 September 2025) reply {\"refuse\":\"<why, in plain words>\"}.\n"
    '  - if the question is ambiguous (an undefined term with more than one defensible reading) reply '
    '{"clarify":"<the one question you would ask>"}.\n'
    "  - you may only read data; anything that changes data is refused by the executor, so refuse it yourself: "
    '{\"refuse\":\"...\"}.\n\n'
    "Question: ")


def plan_prompt(question):
    return PREAMBLE.replace("{views}", view_doc()) + question


# ---------------------------------------------------------------- executor
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
KW = {"select", "from", "where", "group", "by", "order", "having", "limit", "offset", "and", "or", "not", "in", "is",
      "null", "as", "on", "using", "join", "inner", "left", "right", "full", "outer", "cross", "natural", "union",
      "all", "distinct", "case", "when", "then", "else", "end", "with", "asc", "desc", "cast", "like", "between",
      "exists", "except", "intersect", "interval", "true", "false", "window", "over", "partition", "filter", "nulls",
      "first", "last", "any", "some", "filter", "distinct", "table", "values", "unnest", "lateral", "rows", "range"}
BANNED = re.compile(r"(?i)\b(insert|update|delete|drop|create|alter|attach|copy|export|import|install|load|call|pragma|"
                    "set|reset|truncate|vacuum|analyze|attach|detach|checkpoint|transaction|begin|commit|rollback|"
                    r"read_parquet|read_csv\w*|read_json\w*|parquet_scan|csv_scan|json_scan|glob|sniff_csv|load_extension|"
                    r"read_blob|read_text|from_parquet|from_csv|query_sql|duckdb_\w*|sqlite_\w*|postgres_\w*|mysql_\w*|"
                    r"iceberg_\w*|delta_scan|getenv|shell|exec|system|curl|urlopen|nextval|checkpoint)\b")
MAX_MATERISED_ROWS = 200


def _strip(sql):
    """Remove string literals (to '' / 0) and comments so identifier/keyword checks cannot be fooled by them."""
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append(" '' ")
            i = j + 1
        elif ch == '"':
            j = i + 1
            while j < n and sql[j] != '"':
                j += 1
            out.append(sql[i:j + 1])
            i = j + 1
        elif sql.startswith("--", i) or sql.startswith("/*", i):
            stop = sql.find("\n" if sql[i] == "-" else "*/", i)
            i = n if stop < 0 else stop + (1 if sql[i] == "-" else 2)
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def validate(sql, allowed):
    """Read-only guard: one statement, only allow-listed relations, no DDL/DML/file access. Returns an error string or None."""
    if sql is None or not sql.strip():
        return "empty SQL"
    if ";" in sql:
        return "only one statement is allowed (no ';')"
    if BANNED.search(sql):
        return "forbidden keyword: " + ", ".join(sorted({m.group(0).lower() for m in BANNED.finditer(sql)}))
    s = _strip(sql)
    if not re.match(r"(?is)^\s*(select|with)\b", s):
        return "must be a single SELECT or WITH statement"
    if re.search(r"(?is)\b(select|with)\b[^;]*\b(select|with)\b", s) and not re.match(r"(?is)^\s*with\b", s):
        pass  # subqueries are fine; statement count is already covered by ';' and the head keyword
    # relation references, with or without double quotes, minus CTE names and query aliases
    u = s.replace('"', "")
    refs = {m.group(1).lower() for m in re.finditer(r"(?is)\b(?:from|join)\s+([a-z_][\w.]*)", u)}
    cte = {m.group(1).lower() for m in re.finditer(r"(?is)(?:\bwith\b|,)\s*([a-z_]\w*)\s*(?:\([^()]*\))?\s+as\s*\(", u)}
    aliases = set()
    for m in re.finditer(r"(?is)\b(?:from|join)\s+([a-z_][\w.]*)\s+(?:as\s+)?([a-z_]\w*)\b", u):
        name, alias = m.group(1).lower(), m.group(2).lower()
        if alias != name and alias not in KW:
            aliases.add(alias)
    tables = {r.split(".")[-1] for r in refs} - cte - aliases
    bad = sorted(t for t in tables if t not in allowed)
    if bad:
        return "relation not in the allow-list: " + ", ".join(bad)
    return None


def _lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def relation_from_rows(rows, columns, step_id):
    """Turn a frozen step result into a relation expression, typing each column from its values."""
    n = max([len(r) for r in rows] + [len(columns)]) if rows else len(columns)
    columns = list(columns) + [f"column{i + 1}" for i in range(len(columns), n)]
    seen, uniq = {}, []
    for c in columns:
        if c.lower() in seen:
            seen[c.lower()] += 1
            uniq.append(f"{c}_{seen[c.lower()]}")
        else:
            seen[c.lower()] = 1
            uniq.append(c)
    types = []
    for i in range(n):
        vals = [r[i] for r in rows if i < len(r) and r[i] is not None]
        if not vals:
            types.append("VARCHAR")
        elif all(isinstance(v, bool) for v in vals):
            types.append("BOOLEAN")
        elif all(isinstance(v, int) for v in vals):
            types.append("BIGINT")
        elif all(isinstance(v, (int, float)) for v in vals):
            types.append("DOUBLE")
        elif all(isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?", v.strip())
                 for v in vals):
            types.append("TIMESTAMP")
        elif all(isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip()) for v in vals):
            types.append("DATE")
        else:
            types.append("VARCHAR")
    if not rows:
        cells = ", ".join(f"CAST(NULL AS {t}) AS {c}" for c, t in zip(uniq, types))
        return f"(SELECT {cells} WHERE 1 = 0)"
    vals = ",\n ".join("(" + ", ".join(_lit(r[i]) if i < len(r) else "NULL" for i in range(n)) + ")" for r in rows)
    casts = ", ".join(f"CAST({c} AS {t}) AS {c}" for c, t in zip(uniq, types))
    return f"(SELECT {casts} FROM (VALUES\n {vals}) AS _{step_id}({', '.join(uniq)}))"


def _wrap(prefix, sql):
    """Attach materialised earlier steps to a statement, merging with its own WITH clause if it has one."""
    if not prefix:
        return sql
    defs = ", ".join(f"{sid} AS {rel}" for sid, rel in prefix)
    m = re.match(r"(?is)^\s*with\s+(?:recursive\s+)?", sql)
    if m:
        return "WITH " + defs + ", " + sql[m.end():]
    return "WITH " + defs + " " + sql


def run_step(sql, prefix):
    """Run one step with earlier steps materialised; arm1.run_sql is the same no-network executor arm1 uses."""
    return arm1.run_sql(_wrap(prefix, sql))


def execute(spec, allowed_views):
    """Run a plan: {"steps":[{"id","sql"}],"final":"sN"}. Returns (rows, final_sql, step_records, error)."""
    steps = spec.get("steps") or []
    if not steps:
        return None, "", [], "plan has no steps"
    if len(steps) > MAX_STEPS:
        return None, "", [], f"plan has {len(steps)} steps, the limit is {MAX_STEPS}"
    prefix, records, by_id = [], [], {}
    for i, st in enumerate(steps, 1):
        sid = str(st.get("id") or f"s{i}")
        sql = st.get("sql")
        allowed = set(allowed_views) | set(by_id)
        err = validate(sql, allowed)
        rec = {"id": sid, "sql": sql, "validated": err is None}
        if err:
            rec["error"] = err
            records.append(rec)
            return None, "", records, f"step {sid}: {err}"
        r = run_step(sql, prefix)
        if r.get("error"):
            rec["error"] = r["error"]
            records.append(rec)
            return None, "", records, f"step {sid}: {r['error']}"
        rows = r.get("rows") or []
        cols = [c if isinstance(c, str) else str(c) for c in (r.get("columns") or [])]
        rec["rows"] = len(rows)
        rec["sha256"] = hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()
        if len(rows) > MAX_MATERISED_ROWS:
            rec["error"] = f"step returned {len(rows)} rows; only the first {MAX_MATERISED_ROWS} are passed on"
            rows = rows[:MAX_MATERISED_ROWS]
        records.append(rec)
        rel = relation_from_rows(rows, cols, sid)
        prefix = prefix + [(sid, rel)]
        by_id[sid] = rows
        final = str(spec.get("final") or steps[-1].get("id") or f"s{i}")
        if sid == final:
            return rows, _wrap(prefix[:-1], sql), records, None
    final = str(spec.get("final") or "")
    return None, "", records, f"final step {final!r} was never reached"


# ---------------------------------------------------------------- model call
def _chat(prompt):
    """One plan call. LAST_FINISH records the finish_reason so answer() can tell an empty reply from a truncated one."""
    global LAST_FINISH
    body = json.dumps({"model": MODEL, "temperature": 0, "seed": int(os.environ.get("EVAL_SEED", "0")),
                       "max_tokens": 8000, "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(f"{BASE}/chat/completions", data=body,
                                 headers={"content-type": "application/json"})
    choice = json.load(urllib.request.urlopen(req, timeout=290))["choices"][0]
    LAST_FINISH = choice.get("finish_reason")
    return choice["message"].get("content") or ""


def parse_plan(text):
    """Model output -> (spec, None) or (None, error). Accepts the JSON object wrapped in prose or a code fence."""
    if "CANNOT_ANSWER" in text.upper()[:200]:
        return None, "refused"
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    starts = [i for i in (t.find("{"),) if i >= 0]
    if not starts:
        return None, "no JSON object in the reply"
    for i in starts:
        for j in range(len(t), i, -1):
            try:
                obj = json.loads(t[i:j])
                break
            except Exception:
                continue
        else:
            continue
        if isinstance(obj, dict):
            return obj, None
    return None, "reply was not valid JSON"


def answer(question):
    views = sorted(_load_view_doc())
    prompt = plan_prompt(question)
    text = _chat(prompt)
    calls = 1
    if not text.strip() and LAST_FINISH == "length":
        return dict(rows=None, query="", calls=calls, stage="plan",
                    note="empty reply, finish_reason=length (model budget exhausted)")
    spec, perr = parse_plan(text)
    if perr:
        return dict(rows=None, query="", calls=calls, note=f"no usable plan ({perr}): {text.strip()[:240]}",
                    stage="plan")
    if isinstance(spec.get("refuse"), str):
        return dict(rows=None, query="", calls=calls, note=f"refused: {spec['refuse'][:280]}")
    if isinstance(spec.get("clarify"), str):
        return dict(rows=None, query="", calls=calls, note=f"clarify: {spec['clarify'][:280]}")
    rows, final_sql, records, err = execute(spec, views)
    if err:  # ONE repair call, with the error fed back
        repair = (prompt + "\n\nYour previous plan failed: " + err +
                  "\nReturn a corrected plan in the same JSON format (and nothing else).")
        text2 = _chat(repair)
        calls += 1
        spec2, perr2 = parse_plan(text2)
        if perr2 is None and isinstance(spec2.get("refuse"), str):
            return dict(rows=None, query=_query_of(records), calls=calls,
                        note=f"refused after error: {spec2['refuse'][:240]}", stage="plan")
        if perr2 is None and isinstance(spec2.get("clarify"), str):
            return dict(rows=None, query=_query_of(records), calls=calls,
                        note=f"clarify after error: {spec2['clarify'][:240]}", stage="plan")
        if perr2 is None:
            rows, final_sql, records2, err = execute(spec2, views)
            records = records + records2
        if err:
            return dict(rows=None, query=_query_of(records), calls=calls, note=f"repair failed: {err}",
                        stage=_layer(err),
                        error=err)
        spec = spec2
    note = " | ".join(f"{r['id']}:{r.get('rows', '?')}rows sha {str(r.get('sha256'))[:8]}"
                      + (f" ({r['error']})" if r.get("error") else "") for r in records)
    if not rows:
        return dict(rows=None, query=_query_of(records), calls=calls, note=f"empty answer | {note}", stage="execute")
    if len(rows) > 3:
        return dict(rows=None, query=_query_of(records), calls=calls,
                    note=f"answer is {len(rows)} rows, above the 3-row answer limit | {note}", stage="represent")
    return dict(rows=rows, query=_query_of(records), calls=calls, note=f"final: {final_sql[:400]} | {note}"[:400])


def _query_of(records):
    return " ;; ".join(f"{r['id']}: {r.get('sql')}" for r in records)


def _layer(err):
    e = (err or "").lower()
    if "allow-list" in e or "not in the allow" in e or "forbidden" in e or "must be a single" in e or ";" in e:
        return "compile"
    if "no steps" in e or "limit is" in e or "final step" in e:
        return "plan"
    return "execute"


def run_spec(spec):
    """Path B: the gold plan goes straight to the executor, no model call."""
    views = sorted(_load_view_doc())
    rows, final_sql, records, err = execute(spec, views)
    return dict(rows=rows, query=_query_of(records), calls=0,
                note=(err or final_sql[:200]), stage=_layer(err) if err else None,
                error=err or "")


# ------------------------------------------------------------------ setup / CLI
def _decomp_setup():
    import importlib.util
    path = os.path.join(REPO, "scripts", "ontology_eval", "decomp_setup.py")
    spec = importlib.util.spec_from_file_location("decomp_setup", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def setup():
    """Create the governed views if they are missing (idempotent, container-side, no host data access)."""
    mod = _decomp_setup()
    names = ",".join("'" + v + "'" for v in mod.VIEW_NAMES)
    r = arm1.run_sql("select table_name from information_schema.tables where table_schema='main' and table_name in ("
                     + names + ") order by 1")
    have = [x[0] for x in (r.get("rows") or [])]
    if len(have) == len(mod.VIEW_NAMES):
        return
    built = mod.build()
    if "error" in built or built.get("bad"):
        raise SystemExit(f"decomp views failed to build: {str(built)[:600]}")
    missing = [v for v in mod.VIEW_NAMES if v not in built.get("views", [])]
    if missing:
        raise SystemExit(f"decomp views missing after build: {missing}")


def _selftest():
    """Offline checks: executor, allow-list, materialisation, parse and the repair path. No model, no GPU.

    Views must already exist (python3 scripts/ontology_eval/decomp_setup.py).
    """
    views = sorted(_load_view_doc())
    plans = [
        ("one step", {"steps": [{"id": "s1", "sql": 'SELECT count(DISTINCT "DUID") FROM "unit_region" '
                                                    "WHERE \"REGIONID\" = 'SA1' AND \"TOTALCLEARED\" > 0"}]}, [[66]]),
        ("two steps, timestamp passed through", {"steps": [
            {"id": "s1", "sql": 'SELECT "SETTLEMENTDATE" FROM "price_interval" WHERE "REGIONID" = \'SA1\' '
                                'ORDER BY "RRP" DESC, "SETTLEMENTDATE" LIMIT 1'},
            {"id": "s2", "sql": 'SELECT l."DUID", l."TOTALCLEARED" FROM "unit_region" l JOIN s1 ON '
                                's1."SETTLEMENTDATE" = l."SETTLEMENTDATE" WHERE l."REGIONID" = \'SA1\' '
                                'ORDER BY l."TOTALCLEARED" DESC, l."DUID" LIMIT 1'}]}, [["PPCCGT", 406.69696]]),
        ("numeric step reused as a scalar", {"steps": [
            {"id": "s1", "sql": 'SELECT "DUID", sum("TOTALCLEARED") AS M FROM "unit_region" '
                                "WHERE \"REGIONID\" = 'NSW1' GROUP BY 1"},
            {"id": "s2", "sql": "SELECT round((SELECT sum(M) FROM (SELECT M FROM s1 ORDER BY M DESC LIMIT 5)) / "
                                "(SELECT sum(M) FROM s1), 3)"}]}, [[0.437]]),
        ("empty step is still a relation", {"steps": [
            {"id": "s1", "sql": 'SELECT "DUID" FROM "unit_dim" WHERE 1 = 0'},
            {"id": "s2", "sql": "SELECT count(*) FROM s1"}]}, [[0]]),
        ("text in a literal is not a relation", {"steps": [
            {"id": "s1", "sql": "SELECT 'from dispatchload' AS A"}]}, [["from dispatchload"]]),
    ]
    rejects = [
        ("raw table not in the allow-list", {"steps": [{"id": "s1", "sql": "SELECT * FROM dispatchload"}]}),
        ("not a SELECT", {"steps": [{"id": "s1", "sql": "DELETE FROM unit_dim WHERE 1 = 1"}]}),
        ("two statements", {"steps": [{"id": "s1", "sql": "SELECT 1 AS a; SELECT 2"}]}),
        ("file function", {"steps": [{"id": "s1", "sql": "SELECT * FROM read_parquet('/nemosis/X.parquet')"}]}),
        ("more than MAX_STEPS", {"steps": [{"id": f"s{i}", "sql": "SELECT 1 AS a"} for i in range(1, MAX_STEPS + 2)]}),
        ("final names a step that does not exist", {"steps": [{"id": "s1", "sql": "SELECT 1 AS a"}], "final": "s9"}),
    ]
    bad = 0
    for name, spec, want in plans:
        rows, _, records, err = execute(spec, views)
        ok = rows == want and len(records) == len(spec["steps"])
        bad += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'} execute  {name} | {str(rows)[:70]} | {str(err)[:80]}")
    for name, spec in rejects:
        rows, _, records, err = execute(spec, views)
        ok = err is not None
        bad += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'} reject  {name} | err: {str(err)[:100]}")
    for name, canned, want in [
            ("refuse", '{"refuse":"no battery state of charge in this corpus"}', None),
            ("clarify", '{"clarify":"which settlement date do you mean?"}', None),
            ("CANNOT_ANSWER", "CANNOT_ANSWER: not enough tables", None),
            ("prose-wrapped plan", 'Sure!\n```json\n{"steps":[{"id":"s1","sql":"SELECT 1 AS a"}]}\n```\n', {"steps":
                [{"id": "s1", "sql": "SELECT 1 AS a"}]}),
            ("no JSON at all", "I would join two tables.", None)]:
        spec, err = parse_plan(canned)
        ok = (spec is None) == (want is None) and (spec == want if want else True)
        bad += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'} parse    {name} | {str(spec or err)[:70]}")
    # repair path: first reply fails validation, second one is fine
    replies = ['{"steps":[{"id":"s1","sql":"SELECT * FROM dispatchload"}]}',
               '{"steps":[{"id":"s1","sql":"SELECT count(DISTINCT \\"DUID\\") FROM \\"unit_region\\" WHERE '
               '\\"REGIONID\\" = \'SA1\' AND \\"TOTALCLEARED\\" > 0"}]}']
    seen = {"n": 0}

    def fake_chat(prompt):
        r = replies[min(seen["n"], len(replies) - 1)]
        seen["n"] += 1
        return r

    real, arm1_run = _chat, None
    try:
        globals()["_chat"] = fake_chat
        out = answer("how many SA1 units generated?")
    finally:
        globals()["_chat"] = real
    ok = out["rows"] == [[66]] and out["calls"] == 2
    bad += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'} repair   one repair call used | calls={out['calls']} rows={str(out['rows'])[:30]}")
    print("SELFTEST", "OK" if not bad else f"{bad} FAILURES")
    return 1 if bad else 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--selftest":
        setup()
        sys.exit(_selftest())
    if a and a[0] == "--setup":
        setup()
        print("views:", sorted(_load_view_doc()))
        sys.exit(0)
    if a and a[0] == "--views":
        print(view_doc())
        sys.exit(0)
    if a and a[0] == "--plan":
        setup()
        print(json.dumps(answer(" ".join(a[1:])), indent=1, default=str)[:4000])
        sys.exit(0)
    print(__doc__)

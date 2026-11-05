//! One DSL example per command, lowered to executor requests. The requests are a fixture the
//! Python executor runs (`powercoder/commands.py`, `test_commands.py`), so the two sides stay in step.

use command_ir::{parse, to_python_json};

/// One example per PC-REQ-001 command.
pub const EXAMPLES: [&str; 10] = [
    "SOLVE snem-sa-2000 OUTAGE 94",
    "SIMULATE snem-sa-2000 load_scale 1.0375 OBSERVE 985",
    "RECALL SA1 FROM 2026-06-10T00:00 TO 2026-06-10T01:00 ASOF 2026-06-10T00:30",
    "RESOLVE /case/snem-sa-2000/bus/985/vn_kv",
    "ESTIMATE fixture-2bus-ok SEED 1",
    "OPTIMIZE ieee39-pab-stressed DC",
    "FORECAST SA1 MODEL seasonal-naive AT 2026-06-20T12:00 HORIZONS 1,12,48",
    "HINDCAST SA1 HORIZONS 12,48 FROMDAY 20 EVERY 288",
    "COMPARE snem-sa-2000 OUTAGES 94,9,97 BY max_line_loading",
    "EXPLAIN Bus 985 is at 165 kV (/case/snem-sa-2000/bus/985/vn_kv).",
];

fn lowered() -> String {
    let lines: Vec<String> = EXAMPLES.iter()
        .map(|e| to_python_json(&parse(e).unwrap_or_else(|err| panic!("{e}: {err}")).request().unwrap()))
        .collect();
    lines.join("\n") + "\n"
}

#[test]
fn every_command_lowers_to_the_committed_requests() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/requests.jsonl");
    if std::env::var("WRITE_FIXTURE").is_ok() {
        std::fs::write(path, lowered()).unwrap();
    }
    assert_eq!(lowered(), std::fs::read_to_string(path).unwrap());
}

#[test]
fn invalid_commands_are_refused_before_execution() {
    for bad in ["RECALL XX1 FROM 2026-06-10T00:00 TO 2026-06-10T01:00", "FORECAST SA1 MODEL oracle AT 2026-06-20T12:00 HORIZONS 1",
                "FORECAST SA1 MODEL persistence AT 2026-06-20T12:00 HORIZONS 999", "OPTIMIZE nowhere AC",
                "COMPARE snem-sa-2000 OUTAGES 1 BY max_line_loading", "RESOLVE case/x", "EXPLAIN    "] {
        assert!(parse(bad).is_err(), "{bad} should be refused");
    }
}

#[test]
fn lift_inverts_lower_and_applies_the_tools_static_rules() {
    use command_ir::{lift, parse};
    use serde_json::json;

    for dsl in ["SOLVE snem-sa-2000 OUTAGE 94", "SOLVE snem-sa-2000 OUTAGE 94 OBSERVE bus_voltage 985",
                "SIMULATE snem-sa-2000 load_scale 1.0375 OBSERVE 985", "SIMULATE snem-1803 line_rating 1.05 ELEMENT 7 OBSERVE 7", "SIMULATE ieee118 line_rating 1.05 ELEMENT 7 OBSERVE 7 LEVEL 1.04"] {
        let command = parse(dsl).unwrap();
        assert_eq!(lift(&command.lower().unwrap()).unwrap(), command, "{dsl}");
    }
    // The 0.1.0 report shape (placeholder scale, element and observation) still lifts: unused
    // arguments are accepted, so a model trained on it is not newly refused.
    let base = json!({"network_id": "snem-sa-2000", "operation": "none", "scale": 1, "element_id": 0,
                      "observation": "bus_voltage", "observation_id": 985, "max_iter": 50});
    assert!(lift(&base).is_ok());
    assert!(lift(&json!({"network_id": "snem-sa-2000", "operation": "none", "max_iter": 50})).is_ok(), "a bare report");
    let with = |k: &str, v: serde_json::Value| { let mut a = base.clone(); a[k] = v; a };
    let without = |k: &str| { let mut a = base.clone(); a.as_object_mut().unwrap().remove(k); a };
    for (bad, why) in [
        (with("scale", json!(0.95)), "none with scale != 1"),
        (with("contingency_line", json!("None")), "FM-7 string None"),
        (with("observation_id", json!(-1)), "negative id"),
        (with("observation_id", json!(985.0)), "float id"),
        (with("max_iter", json!(0)), "max_iter out of range"),
        (with("network_id", json!("ieee14")), "unknown network"),
        (with("extra", json!(1)), "unknown property"),
        (without("observation"), "an observation_id without its observation"),
        (without("max_iter"), "max_iter is required"),
        (with("operation", json!("load_scale")), "load_scale observes bus_voltage — ok"),
    ] {
        let ok = why.ends_with("ok");
        assert_eq!(lift(&bad).is_ok(), ok, "{why}");
    }
    let level = |op: &str, l: f64| json!({"network_id": "ieee118", "operation": op, "scale": 1.05, "element_id": 3,
        "observation": if op == "line_rating" { "line_loading" } else { "bus_voltage" }, "observation_id": 3, "max_iter": 50, "load_level": l});
    assert!(lift(&level("line_rating", 1.04)).is_ok(), "an operating point for a rating fit");
    assert!(lift(&level("line_rating", 1.3)).is_err(), "load_level out of range");
    assert!(lift(&level("load_scale", 1.04)).is_err(), "load_scale already scales every load");
    let mismatch = json!({"network_id": "snem-sa-2000", "operation": "line_rating", "scale": 1.0, "element_id": 3,
                          "observation": "bus_voltage", "observation_id": 3, "max_iter": 50});
    assert!(lift(&mismatch).is_err(), "line_rating must observe line_loading");
    let no_line = json!({"network_id": "snem-sa-2000", "operation": "line_rating", "scale": 1.0,
                         "observation": "line_loading", "observation_id": 3, "max_iter": 50});
    assert!(lift(&no_line).is_err(), "line_rating must name the line it modifies");
}

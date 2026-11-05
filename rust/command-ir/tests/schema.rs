//! The IR emits the served schema (FM-23: one source, so training and serving cannot skew),
//! and its lowering sends only the arguments an operation uses (IR 0.2.0).

use command_ir::{parse, study_trial_schema, to_python_json, Command, IrError, Modification, Observation, Simulate, Solve};

/// The schema Python serves (`studies.tool_schema()` loads this file). Regenerate it from the IR
/// with `WRITE_FIXTURE=1 cargo test -p command-ir`, never by hand.
const SERVED_PATH: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/served_run_study_trial.json");

#[test]
fn emitted_schema_is_byte_identical_to_the_served_one() {
    let emitted = to_python_json(&study_trial_schema());
    if std::env::var("WRITE_FIXTURE").is_ok() {
        std::fs::write(SERVED_PATH, format!("{emitted}\n")).unwrap();
    }
    assert_eq!(emitted, std::fs::read_to_string(SERVED_PATH).unwrap().trim_end());
}

#[test]
fn a_report_sends_no_placeholder_element_or_observation() {
    let report = Command::Solve(Solve { network: "snem-sa-2000".into(), contingency_line: Some(94), observe: None, max_iter: 50, load_level: None });
    assert_eq!(to_python_json(&report.lower().unwrap()),
               r#"{"network_id": "snem-sa-2000", "operation": "none", "contingency_line": 94, "max_iter": 50}"#);
    let observed = Command::Solve(Solve { observe: Some((Observation::BusVoltage, 985)),
                                          ..match report { Command::Solve(s) => s, _ => unreachable!() } });
    assert!(to_python_json(&observed.lower().unwrap()).contains(r#""observation": "bus_voltage", "observation_id": 985"#));
}

#[test]
fn simulate_observes_what_its_modification_moves_and_refuses_bad_scales() {
    let ok = Command::Simulate(Simulate { network: "snem-1803".into(), modification: Modification::LineRating, scale: 1.05,
                                          element_id: Some(7), observation_id: 7, contingency_line: None, max_iter: 50, load_level: None });
    assert!(to_python_json(&ok.lower().unwrap()).contains(r#""observation": "line_loading""#));
    let bad = Command::Simulate(Simulate { scale: 1.2, ..match ok.clone() { Command::Simulate(s) => s, _ => unreachable!() } });
    assert_eq!(bad.lower(), Err(IrError::ScaleOutOfRange(1.2)));
    let no_element = Command::Simulate(Simulate { element_id: None, ..match ok { Command::Simulate(s) => s, _ => unreachable!() } });
    assert!(no_element.lower().is_err(), "line_rating needs the line it modifies");
    let all_loads = parse("SIMULATE snem-sa-2000 load_scale 1.0375 OBSERVE 985").unwrap();
    assert!(!to_python_json(&all_loads.lower().unwrap()).contains("element_id"), "load scaling modifies every load");
}

#[test]
fn the_dsl_compiles_to_the_ir_and_rejects_what_the_tool_would() {
    assert_eq!(parse("SOLVE snem-sa-2000 OUTAGE 94").unwrap(),
               Command::Solve(Solve { network: "snem-sa-2000".into(), contingency_line: Some(94), observe: None, max_iter: 50, load_level: None }));
    assert!(matches!(parse("SOLVE snem-sa-2000 OUTAGE 94 OBSERVE bus_voltage 985").unwrap(),
                     Command::Solve(Solve { observe: Some((Observation::BusVoltage, 985)), .. })));
    assert!(matches!(parse("SIMULATE snem-sa-2000 load_scale 1.0375 OBSERVE 985").unwrap(), Command::Simulate(_)));
    assert!(matches!(parse("SOLVE elsewhere"), Err(IrError::UnknownNetwork(_))));
    assert!(matches!(parse("SIMULATE snem-sa-2000 load_scale 0.5 OBSERVE 985"), Err(IrError::ScaleOutOfRange(_))));
    assert!(matches!(parse("SOLVE snem-sa-2000 OBSERVE bus_voltage None"), Err(IrError::Syntax(_))));
    assert!(matches!(parse("DROP TABLE"), Err(IrError::Syntax(_))));
}

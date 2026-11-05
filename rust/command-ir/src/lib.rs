//! PowerCoder's typed command IR for PRD-0010 G1 (backlog 0015): the ten PC-REQ-001 commands.
//!
//! `SOLVE` and `SIMULATE` lower to today's `run_study_trial` tool (`powercoder/inference/studies.py`):
//!
//! - [`Solve`]: an AC power flow of the intact network or one N-1 outage (`operation = "none"`),
//!   optionally observing one element; a report sends no scale, element or observation (0.2.0);
//! - [`Simulate`]: one perturbation trial: scale all loads, one line's rating or one
//!   generator's dispatch, and observe the quantity that modification moves.
//!
//! [`lift`] is the inverse: a model's `run_study_trial` arguments are lifted into the IR, which is
//! how the protocol suite judges validity (`src/bin/lift.rs`). Every command, the other eight
//! included, compiles to an executor request ([`Command::request`], run by `powercoder/commands.py`).
//!
//! [`study_trial_schema`] emits the tool's JSON Schema, and [`to_python_json`] writes it with
//! Python `json.dumps` separators. Their output must be byte-identical to the schema the
//! evaluation serves (`tests/served_run_study_trial.json`, written from `studies.tool_schema()`),
//! so an IR-generated schema can never skew training against serving (AGENTS.md FM-23).
//! A minimal textual DSL ([`parse`]) compiles to the IR.

use serde_json::{json, Map, Value};
use std::fmt;

/// IR version; bump on any change to command shape or lowering.
pub const IR_VERSION: &str = "0.3.0";
/// The study tool's scale interval (`studies.SCALE_MIN`/`SCALE_MAX`).
pub const SCALE_MIN: f64 = 0.9;
pub const SCALE_MAX: f64 = 1.1;
/// The study tool's solver-iteration cap (`studies.MAX_ITERATIONS`).
pub const MAX_ITERATIONS: u32 = 100;
/// The operating point's load multiplier interval (`studies.LOAD_LEVEL_MIN`/`MAX`): the same interval
/// as `scale`, over which every served network's discovered elements were checked to solve.
pub const LOAD_LEVEL_MIN: f64 = 0.9;
pub const LOAD_LEVEL_MAX: f64 = 1.1;
/// Iterations the trained procedure asks for (`trajectories.TRIAL_MAX_ITER`).
pub const DEFAULT_MAX_ITER: u32 = 50;

/// The served tool description, verbatim (`studies.tool_schema`); the fixture test guards it.
pub const STUDY_TRIAL_DESCRIPTION: &str = "Run one fresh Newton-Raphson AC trial. Every response reports the network-wide results: convergence, iterations, min/max bus voltage, max line and transformer loading, and voltage violations. Use operation=none for base-case and N-1 reports; a report needs only network_id, max_iter and, for N-1, contingency_line. load_scale multiplies ALL loads' P/Q and observes bus_voltage; line_rating scales one line max_i_ka and observes line_loading; gen_dispatch scales one generator P and observes trafo_loading. A modification needs scale, observation and observation_id; line_rating and gen_dispatch also need element_id, the modified line/generator. Use only ids the task or a tool response gave you. Omit contingency_line (or pass null) for the intact network; an integer takes that line out of service, and 0 is a real line. load_level sets the operating point: every load's P/Q times load_level before the study (omit it for the base load; not with load_scale, which already scales every load). Returns actual measurements, not a fitted answer. status=constraint_violation with metric_valid=true is still a usable measurement: compare voltage_violations with base_case_voltage_violations, which the unmodified case already has.";

/// Networks the study tool serves, in its enum order (`powermcp.SERVED_NETWORKS`: the CSIRO cases,
/// pandapower's standard cases, then fixtures).
pub const NETWORKS: [&str; 7] = ["snem-sa-2000", "snem-1803", "ieee39", "ieee118", "pegase89", "fixture-2bus-ok",
                                 "fixture-2bus-nonconverged"];

/// What a trial observes.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Observation {
    BusVoltage,
    LineLoading,
    TrafoLoading,
}

impl Observation {
    pub const ALL: [Observation; 3] = [Observation::BusVoltage, Observation::LineLoading, Observation::TrafoLoading];

    pub fn as_str(self) -> &'static str {
        match self {
            Observation::BusVoltage => "bus_voltage",
            Observation::LineLoading => "line_loading",
            Observation::TrafoLoading => "trafo_loading",
        }
    }
}

/// What a SIMULATE trial changes. Each modification observes one quantity (`studies.py`'s rule).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Modification {
    LoadScale,
    LineRating,
    GenDispatch,
}

impl Modification {
    pub fn as_str(self) -> &'static str {
        match self {
            Modification::LoadScale => "load_scale",
            Modification::LineRating => "line_rating",
            Modification::GenDispatch => "gen_dispatch",
        }
    }

    /// The only observation the study tool accepts with this modification.
    pub fn observation(self) -> Observation {
        match self {
            Modification::LoadScale => Observation::BusVoltage,
            Modification::LineRating => Observation::LineLoading,
            Modification::GenDispatch => Observation::TrafoLoading,
        }
    }
}

/// An AC power flow, intact or with one line out. Its network-wide results always come back;
/// `observe` optionally reads one element too (IR 0.2.0: a report names no element).
#[derive(Clone, Debug, PartialEq)]
pub struct Solve {
    pub network: String,
    pub contingency_line: Option<u32>,
    pub observe: Option<(Observation, u32)>,
    pub max_iter: u32,
    /// Operating point: every load times this before the study (IR 0.3.0); None is the base load.
    pub load_level: Option<f64>,
}

/// One perturbation trial.
#[derive(Clone, Debug, PartialEq)]
pub struct Simulate {
    pub network: String,
    pub modification: Modification,
    pub scale: f64,
    /// The modified line or generator: required by line_rating and gen_dispatch, absent for
    /// load scaling, which scales every load.
    pub element_id: Option<u32>,
    pub observation_id: u32,
    pub contingency_line: Option<u32>,
    pub max_iter: u32,
    /// Operating point (IR 0.3.0); never with load scaling, which already scales every load.
    pub load_level: Option<f64>,
}

/// NEM regions with cached demand (``powercoder.forecasting.hindcast``).
pub const REGIONS: [&str; 5] = ["NSW1", "QLD1", "SA1", "TAS1", "VIC1"];
/// Forecasting baselines the harness serves (``hindcast.MODELS``).
pub const FORECAST_MODELS: [&str; 3] = ["persistence", "seasonal-naive", "seasonal-naive-empirical-quantiles"];
/// Networks with real limits and costs for OPTIMIZE (``engineering.cases``), besides the served ones.
pub const OPTIMIZE_CASES: [&str; 3] = ["ieee39-pab-stressed", "ieee118-restorebench", "pegase89-restorebench"];
/// The longest forecast horizon, in 5-minute intervals: one day.
pub const MAX_HORIZON: u32 = 288;
/// The most candidates one COMPARE may re-solve.
pub const MAX_CANDIDATES: usize = 10;

/// OPTIMIZE formulations (``dc_opf.run_dc_opf``, ``ac_opf.run_ac_opf``).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Formulation {
    Dc,
    Ac,
}

/// What COMPARE ranks candidate outages by.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CompareMetric {
    MaxLineLoading,
    MinBusVoltage,
}

/// A typed command: PRD-0010 PC-REQ-001's ten.
#[derive(Clone, Debug, PartialEq)]
pub enum Command {
    Solve(Solve),
    Simulate(Simulate),
    /// Observations as known at ``as_of`` (cached AEMO regional demand).
    Recall { region: String, from: String, to: String, as_of: String },
    /// A namespace path's value or children (``namespace.get``/``ls``).
    Resolve { path: String },
    /// WLS state estimation from synthetic measurements seeded by ``seed`` (G5 is blocked).
    Estimate { network: String, seed: u64 },
    Optimize { case: String, formulation: Formulation },
    Forecast { region: String, model: String, issue_time: String, horizons: Vec<u32> },
    /// Rolling-origin hindcast from ``start_day`` of the cached month, every ``every`` intervals.
    Hindcast { region: String, horizons: Vec<u32>, start_day: u32, every: u32 },
    Compare { network: String, candidate_lines: Vec<u32>, metric: CompareMetric },
    /// Resolve every cited number in a claim against the path it cites.
    Explain { claim: String },
}

/// Why a command was refused before reaching the tool.
#[derive(Debug, PartialEq)]
pub enum IrError {
    UnknownNetwork(String),
    Invalid(String),
    ScaleOutOfRange(f64),
    MaxIterOutOfRange(u32),
    Syntax(String),
}

impl fmt::Display for IrError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            IrError::UnknownNetwork(n) => write!(f, "unknown network {n:?}; served: {NETWORKS:?}"),
            IrError::Invalid(msg) => write!(f, "invalid: {msg}"),
            IrError::ScaleOutOfRange(s) => write!(f, "scale {s} outside [{SCALE_MIN}, {SCALE_MAX}]"),
            IrError::MaxIterOutOfRange(m) => write!(f, "max_iter {m} outside [1, {MAX_ITERATIONS}]"),
            IrError::Syntax(msg) => write!(f, "syntax: {msg}"),
        }
    }
}

fn check_common(network: &str, max_iter: u32) -> Result<(), IrError> {
    if !NETWORKS.contains(&network) {
        return Err(IrError::UnknownNetwork(network.to_string()));
    }
    if !(1..=MAX_ITERATIONS).contains(&max_iter) {
        return Err(IrError::MaxIterOutOfRange(max_iter));
    }
    Ok(())
}

fn check_load_level(level: Option<f64>) -> Result<(), IrError> {
    match level {
        Some(l) if !(l.is_finite() && (LOAD_LEVEL_MIN..=LOAD_LEVEL_MAX).contains(&l)) =>
            Err(IrError::Invalid(format!("load_level {l} outside [{LOAD_LEVEL_MIN}, {LOAD_LEVEL_MAX}]"))),
        _ => Ok(()),
    }
}

impl Command {
    /// Check what the executors would refuse, before any call.
    pub fn validate(&self) -> Result<(), IrError> {
        let invalid = |m: String| Err(IrError::Invalid(m));
        let iso = |t: &str| t.len() >= 16 && t.as_bytes()[4] == b'-' && t.as_bytes()[10] == b'T';
        let horizons_ok = |h: &Vec<u32>| !h.is_empty() && h.iter().all(|x| (1..=MAX_HORIZON).contains(x));
        match self {
            Command::Recall { region, from, to, as_of } => {
                if !REGIONS.contains(&region.as_str()) { return invalid(format!("region {region} not in {REGIONS:?}")); }
                if ![from, to, as_of].iter().all(|t| iso(t)) { return invalid("times must be ISO 8601 (YYYY-MM-DDTHH:MM)".into()); }
                if from > to { return invalid("from is after to".into()); }
                Ok(())
            }
            Command::Resolve { path } => if path.starts_with('/') { Ok(()) } else { invalid("a path starts with /".into()) },
            Command::Estimate { network, .. } => check_common(network, DEFAULT_MAX_ITER),
            Command::Optimize { case, .. } => {
                if NETWORKS.contains(&case.as_str()) || OPTIMIZE_CASES.contains(&case.as_str()) { Ok(()) }
                else { invalid(format!("case {case} is neither served nor in {OPTIMIZE_CASES:?}")) }
            }
            Command::Forecast { region, model, issue_time, horizons } => {
                if !REGIONS.contains(&region.as_str()) { return invalid(format!("region {region} not in {REGIONS:?}")); }
                if !FORECAST_MODELS.contains(&model.as_str()) { return invalid(format!("model {model} not in {FORECAST_MODELS:?}")); }
                if !iso(issue_time) { return invalid("issue_time must be ISO 8601".into()); }
                if !horizons_ok(horizons) { return invalid(format!("horizons must be 1..={MAX_HORIZON}")); }
                Ok(())
            }
            Command::Hindcast { region, horizons, every, .. } => {
                if !REGIONS.contains(&region.as_str()) { return invalid(format!("region {region} not in {REGIONS:?}")); }
                if !horizons_ok(horizons) || *every == 0 { return invalid("horizons 1..=288 and every >= 1".into()); }
                Ok(())
            }
            Command::Compare { network, candidate_lines, .. } => {
                check_common(network, DEFAULT_MAX_ITER)?;
                if !(2..=MAX_CANDIDATES).contains(&candidate_lines.len()) {
                    return invalid(format!("2..={MAX_CANDIDATES} candidates"));
                }
                Ok(())
            }
            Command::Explain { claim } => if claim.trim().is_empty() { invalid("empty claim".into()) } else { Ok(()) },
            Command::Solve(s) => {
                check_common(&s.network, s.max_iter)?;
                check_load_level(s.load_level)
            }
            Command::Simulate(s) => {
                check_common(&s.network, s.max_iter)?;
                check_load_level(s.load_level)?;
                if s.modification == Modification::LoadScale && s.load_level.is_some() {
                    return invalid("load_scale already scales every load; omit load_level".into());
                }
                if s.modification != Modification::LoadScale && s.element_id.is_none() {
                    return invalid(format!("{} requires element_id", s.modification.as_str()));
                }
                if !(s.scale.is_finite() && (SCALE_MIN..=SCALE_MAX).contains(&s.scale)) {
                    return Err(IrError::ScaleOutOfRange(s.scale));
                }
                Ok(())
            }
        }
    }

    /// The command's name in the DSL.
    pub fn name(&self) -> &'static str {
        match self {
            Command::Solve(_) => "SOLVE", Command::Simulate(_) => "SIMULATE", Command::Recall { .. } => "RECALL",
            Command::Resolve { .. } => "RESOLVE", Command::Estimate { .. } => "ESTIMATE", Command::Optimize { .. } => "OPTIMIZE",
            Command::Forecast { .. } => "FORECAST", Command::Hindcast { .. } => "HINDCAST", Command::Compare { .. } => "COMPARE",
            Command::Explain { .. } => "EXPLAIN",
        }
    }

    /// The executor request: ``{"command": NAME, "args": {...}}``. SOLVE and SIMULATE carry the
    /// `run_study_trial` arguments of [`Command::lower`]; the Python executor
    /// (`powercoder/commands.py`) runs every command.
    pub fn request(&self) -> Result<Value, IrError> {
        self.validate()?;
        let args = match self {
            Command::Solve(_) | Command::Simulate(_) => self.lower()?,
            Command::Recall { region, from, to, as_of } => json!({"region": region, "from": from, "to": to, "as_of": as_of}),
            Command::Resolve { path } => json!({"path": path}),
            Command::Estimate { network, seed } => json!({"network_id": network, "seed": seed}),
            Command::Optimize { case, formulation } =>
                json!({"case": case, "formulation": if *formulation == Formulation::Ac { "ac" } else { "dc" }}),
            Command::Forecast { region, model, issue_time, horizons } =>
                json!({"region": region, "model": model, "issue_time": issue_time, "horizons": horizons}),
            Command::Hindcast { region, horizons, start_day, every } =>
                json!({"region": region, "horizons": horizons, "start_day": start_day, "every": every}),
            Command::Compare { network, candidate_lines, metric } => json!({"network_id": network, "candidate_lines": candidate_lines,
                "metric": if *metric == CompareMetric::MinBusVoltage { "min_bus_voltage" } else { "max_line_loading" }}),
            Command::Explain { claim } => json!({"claim": claim}),
        };
        Ok(json!({"ir_version": IR_VERSION, "command": self.name(), "args": args}))
    }

    /// The `run_study_trial` arguments, in schema property order. `contingency_line` is
    /// omitted for the intact network, as the trained procedure omits it (FM-18).
    pub fn lower(&self) -> Result<Value, IrError> {
        self.validate()?;
        let mut args = Map::new();
        let (network, operation, scale, element_id, observe, contingency, max_iter, level) = match self {
            Command::Solve(s) => (&s.network, "none", None, None, s.observe, s.contingency_line, s.max_iter, s.load_level),
            Command::Simulate(s) => (&s.network, s.modification.as_str(), Some(s.scale), s.element_id,
                                     Some((s.modification.observation(), s.observation_id)), s.contingency_line, s.max_iter,
                                     s.load_level),
            other => return Err(IrError::Invalid(format!("{} does not lower to run_study_trial", other.name()))),
        };
        // An argument the operation does not use is left out, never filled with a placeholder.
        args.insert("network_id".into(), json!(network));
        args.insert("operation".into(), json!(operation));
        if let Some(scale) = scale {
            args.insert("scale".into(), json!(scale));
        }
        if let Some(element) = element_id {
            args.insert("element_id".into(), json!(element));
        }
        if let Some((observation, id)) = observe {
            args.insert("observation".into(), json!(observation.as_str()));
            args.insert("observation_id".into(), json!(id));
        }
        if let Some(line) = contingency {
            args.insert("contingency_line".into(), json!(line));
        }
        args.insert("max_iter".into(), json!(max_iter));
        if let Some(level) = level {
            args.insert("load_level".into(), json!(level));
        }
        Ok(Value::Object(args))
    }
}

/// The served `run_study_trial` tool definition, generated from the IR's own constants.
pub fn study_trial_schema() -> Value {
    let operations: Vec<&str> = std::iter::once("none")
        .chain([Modification::LoadScale, Modification::LineRating, Modification::GenDispatch].map(Modification::as_str))
        .collect();
    let observations: Vec<&str> = Observation::ALL.iter().map(|o| o.as_str()).collect();
    json!({
        "type": "function",
        "function": {
            "name": "run_study_trial",
            "description": STUDY_TRIAL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "additionalProperties": false,
                "properties": {
                    "network_id": {"type": "string", "enum": NETWORKS},
                    "operation": {"type": "string", "enum": operations},
                    "scale": {"type": "number", "minimum": SCALE_MIN, "maximum": SCALE_MAX},
                    "element_id": {"type": "integer"},
                    "observation": {"type": "string", "enum": observations},
                    "observation_id": {"type": "integer"},
                    "contingency_line": {"type": ["integer", "null"]},
                    "max_iter": {"type": "integer", "minimum": 1, "maximum": MAX_ITERATIONS},
                    "load_level": {"type": "number", "minimum": LOAD_LEVEL_MIN, "maximum": LOAD_LEVEL_MAX}
                },
                "required": ["network_id", "operation", "max_iter"]
            }
        }
    })
}

/// Python `json.dumps` default formatting: `", "` between items, `": "` after keys.
struct PythonFormatter;

impl serde_json::ser::Formatter for PythonFormatter {
    fn begin_array_value<W: ?Sized + std::io::Write>(&mut self, writer: &mut W, first: bool) -> std::io::Result<()> {
        if first { Ok(()) } else { writer.write_all(b", ") }
    }
    fn begin_object_key<W: ?Sized + std::io::Write>(&mut self, writer: &mut W, first: bool) -> std::io::Result<()> {
        if first { Ok(()) } else { writer.write_all(b", ") }
    }
    fn begin_object_value<W: ?Sized + std::io::Write>(&mut self, writer: &mut W) -> std::io::Result<()> {
        writer.write_all(b": ")
    }
}

/// Serialize as Python's `json.dumps(value)` does, so bytes compare with the served schema.
pub fn to_python_json(value: &Value) -> String {
    let mut out = Vec::new();
    let mut ser = serde_json::Serializer::with_formatter(&mut out, PythonFormatter);
    serde::Serialize::serialize(value, &mut ser).expect("serializing a Value cannot fail");
    String::from_utf8(out).expect("serde_json writes UTF-8")
}

/// Lift a model's `run_study_trial` arguments into the IR: the inverse of [`Command::lower`].
///
/// It applies the tool's static rules: exactly the schema's properties, integer ids, scale in
/// range, `operation = "none"` only with `scale = 1`, and the one observation each modification
/// allows (`studies.run_study_trial`). Whether an id exists in the network is the tool's check,
/// not the protocol's. `element_id` is ignored by SOLVE, as the tool ignores it.
pub fn lift(args: &Value) -> Result<Command, IrError> {
    let invalid = |m: &str| IrError::Invalid(m.to_string());
    let obj = args.as_object().ok_or_else(|| invalid("arguments are not an object"))?;
    const PROPERTIES: [&str; 9] = ["network_id", "operation", "scale", "element_id", "observation", "observation_id",
                                   "contingency_line", "max_iter", "load_level"];
    if let Some(extra) = obj.keys().find(|k| !PROPERTIES.contains(&k.as_str())) {
        return Err(IrError::Invalid(format!("unknown property {extra}")));
    }
    let text = |k: &str| obj.get(k).and_then(Value::as_str).ok_or_else(|| IrError::Invalid(format!("{k} must be a string")));
    // as_u64 rejects floats, negatives and booleans, as the tool's integer check does.
    let id = |k: &str| obj.get(k).and_then(Value::as_u64).and_then(|v| u32::try_from(v).ok())
        .ok_or_else(|| IrError::Invalid(format!("{k} must be a non-negative integer")));
    let network = text("network_id")?.to_string();
    let operation = text("operation")?;
    let present = |k: &str| !matches!(obj.get(k), None | Some(Value::Null));
    let optional_id = |k: &str| if present(k) { id(k).map(Some) } else { Ok(None) };
    let observation = if present("observation") {
        Some(match text("observation")? {
            "bus_voltage" => Observation::BusVoltage,
            "line_loading" => Observation::LineLoading,
            "trafo_loading" => Observation::TrafoLoading,
            other => return Err(IrError::Invalid(format!("unknown observation {other}"))),
        })
    } else { None };
    let scale = if present("scale") {
        Some(obj.get("scale").filter(|v| v.is_number()).and_then(Value::as_f64).ok_or_else(|| invalid("scale must be a number"))?)
    } else { None };
    // element_id is accepted and ignored where the operation does not use it, as the tool does.
    let load_level = if present("load_level") {
        Some(obj.get("load_level").filter(|v| v.is_number()).and_then(Value::as_f64).ok_or_else(|| invalid("load_level must be a number"))?)
    } else { None };
    let element_id = optional_id("element_id")?;
    let observation_id = optional_id("observation_id")?;
    let max_iter = id("max_iter")?;
    let contingency_line = match obj.get("contingency_line") {
        None | Some(Value::Null) => None,
        Some(_) => Some(id("contingency_line")?),
    };
    let command = if operation == "none" {
        if scale.is_some_and(|s| s != 1.0) {
            return Err(invalid("operation=none requires scale=1"));
        }
        let observe = match (observation, observation_id) {
            (Some(o), Some(i)) => Some((o, i)),
            (None, Some(_)) => return Err(invalid("operation=none requires observation with observation_id")),
            _ => None,
        };
        Command::Solve(Solve { network, contingency_line, observe, max_iter, load_level })
    } else {
        let modification = match operation {
            "load_scale" => Modification::LoadScale,
            "line_rating" => Modification::LineRating,
            "gen_dispatch" => Modification::GenDispatch,
            other => return Err(IrError::Invalid(format!("unknown operation {other}"))),
        };
        let scale = scale.ok_or_else(|| IrError::Invalid(format!("operation={operation} requires scale")))?;
        let observation_id = observation_id.ok_or_else(|| IrError::Invalid(format!("operation={operation} requires observation_id")))?;
        if observation != Some(modification.observation()) {
            return Err(IrError::Invalid(format!("{operation} observes {}", modification.observation().as_str())));
        }
        let element_id = if modification == Modification::LoadScale { None } else { element_id };
        Command::Simulate(Simulate { network, modification, scale, element_id, observation_id, contingency_line, max_iter, load_level })
    };
    command.validate()?;
    Ok(command)
}

/// Parse the textual DSL into a command.
///
/// ```text
/// SOLVE <network> [OUTAGE <line>] [OBSERVE <bus_voltage|line_loading|trafo_loading> <id>] [MAXITER <n>] [LEVEL <x>]
/// SIMULATE <network> <load_scale|line_rating|gen_dispatch> <scale> [ELEMENT <id>] OBSERVE <id>
///          [OUTAGE <line>] [MAXITER <n>]
/// ```
pub fn parse(text: &str) -> Result<Command, IrError> {
    let words: Vec<&str> = text.split_whitespace().collect();
    let syntax = |m: &str| IrError::Syntax(m.to_string());
    let number = |w: Option<&&str>, what: &str| -> Result<u32, IrError> {
        w.ok_or_else(|| syntax(&format!("{what} needs a value")))?
            .parse::<u32>()
            .map_err(|_| syntax(&format!("{what} must be a non-negative integer")))
    };
    let keyword = |key: &str| words.iter().position(|w| w.eq_ignore_ascii_case(key));
    let value_after = |key: &str| keyword(key).map(|i| words.get(i + 1));
    let network = words.get(1).ok_or_else(|| syntax("missing network"))?.to_string();
    let outage = match value_after("OUTAGE") { Some(w) => Some(number(w, "OUTAGE")?), None => None };
    let max_iter = match value_after("MAXITER") { Some(w) => number(w, "MAXITER")?, None => DEFAULT_MAX_ITER };
    let load_level = match value_after("LEVEL") {
        Some(w) => Some(w.ok_or_else(|| syntax("LEVEL needs a value"))?.parse::<f64>().map_err(|_| syntax("LEVEL must be a number"))?),
        None => None,
    };
    let command = match words.first().map(|w| w.to_ascii_uppercase()) {
        Some(ref verb) if verb == "SOLVE" => {
            let observe = match keyword("OBSERVE") {
                None => None,
                Some(i) => {
                    let kind = words.get(i + 1).ok_or_else(|| syntax("OBSERVE needs a kind"))?;
                    let observation = Observation::ALL.into_iter().find(|o| o.as_str() == *kind)
                        .ok_or_else(|| syntax(&format!("unknown observation {kind}")))?;
                    Some((observation, number(words.get(i + 2), "OBSERVE")?))
                }
            };
            Command::Solve(Solve { network, contingency_line: outage, observe, max_iter, load_level })
        }
        Some(ref verb) if verb == "SIMULATE" => {
            let kind = words.get(2).ok_or_else(|| syntax("SIMULATE needs a modification"))?;
            let modification = [Modification::LoadScale, Modification::LineRating, Modification::GenDispatch]
                .into_iter().find(|m| m.as_str() == *kind)
                .ok_or_else(|| syntax(&format!("unknown modification {kind}")))?;
            let scale = words.get(3).ok_or_else(|| syntax("SIMULATE needs a scale"))?
                .parse::<f64>().map_err(|_| syntax("scale must be a number"))?;
            let element_id = match value_after("ELEMENT") { Some(w) => Some(number(w, "ELEMENT")?), None => None };
            let observation_id = number(value_after("OBSERVE").flatten(), "OBSERVE")?;
            Command::Simulate(Simulate { network, modification, scale, element_id, observation_id,
                                         contingency_line: outage, max_iter, load_level })
        }
        Some(ref verb) if verb == "RECALL" => {
            // RECALL <region> FROM <t> TO <t> [ASOF <t>]   (ASOF defaults to TO)
            let get = |k: &str| value_after(k).flatten().map(|w| w.to_string()).ok_or_else(|| syntax(&format!("RECALL needs {k} <time>")));
            let to = get("TO")?;
            Command::Recall { region: network, from: get("FROM")?, as_of: value_after("ASOF").flatten().map(|w| w.to_string()).unwrap_or(to.clone()), to }
        }
        Some(ref verb) if verb == "RESOLVE" => Command::Resolve { path: network },
        Some(ref verb) if verb == "ESTIMATE" => {
            let seed = match value_after("SEED") { Some(w) => number(w, "SEED")? as u64, None => 1 };
            Command::Estimate { network, seed }
        }
        Some(ref verb) if verb == "OPTIMIZE" => {
            let formulation = match words.get(2).map(|w| w.to_ascii_uppercase()) {
                Some(ref f) if f == "AC" => Formulation::Ac, Some(ref f) if f == "DC" => Formulation::Dc,
                _ => return Err(syntax("OPTIMIZE <case> <DC|AC>")),
            };
            Command::Optimize { case: network, formulation }
        }
        Some(ref verb) if verb == "FORECAST" || verb == "HINDCAST" => {
            let horizons: Vec<u32> = value_after("HORIZONS").flatten().ok_or_else(|| syntax("needs HORIZONS h1,h2,..."))?
                .split(',').map(|h| h.parse::<u32>().map_err(|_| syntax("horizons are integers"))).collect::<Result<_, _>>()?;
            if verb == "FORECAST" {
                let model = value_after("MODEL").flatten().ok_or_else(|| syntax("FORECAST needs MODEL <name>"))?.to_string();
                let issue_time = value_after("AT").flatten().ok_or_else(|| syntax("FORECAST needs AT <issue time>"))?.to_string();
                Command::Forecast { region: network, model, issue_time, horizons }
            } else {
                let start_day = match value_after("FROMDAY") { Some(w) => number(w, "FROMDAY")?, None => 9 };
                let every = match value_after("EVERY") { Some(w) => number(w, "EVERY")?, None => 12 };
                Command::Hindcast { region: network, horizons, start_day, every }
            }
        }
        Some(ref verb) if verb == "COMPARE" => {
            let lines: Vec<u32> = value_after("OUTAGES").flatten().ok_or_else(|| syntax("COMPARE needs OUTAGES l1,l2,..."))?
                .split(',').map(|h| h.parse::<u32>().map_err(|_| syntax("lines are integers"))).collect::<Result<_, _>>()?;
            let metric = match value_after("BY").flatten().map(|w| w.to_ascii_lowercase()) {
                Some(ref m) if m == "min_bus_voltage" => CompareMetric::MinBusVoltage,
                Some(ref m) if m == "max_line_loading" => CompareMetric::MaxLineLoading,
                _ => return Err(syntax("COMPARE needs BY <max_line_loading|min_bus_voltage>")),
            };
            Command::Compare { network, candidate_lines: lines, metric }
        }
        Some(ref verb) if verb == "EXPLAIN" => {
            let claim = text.trim_start()[7..].trim().to_string();
            Command::Explain { claim }
        }
        _ => return Err(syntax("a command starts with one of SOLVE SIMULATE RECALL RESOLVE ESTIMATE OPTIMIZE FORECAST HINDCAST COMPARE EXPLAIN")),
    };
    command.validate()?;
    Ok(command)
}

//! Lift `run_study_trial` argument objects (JSON lines on stdin) into the IR, one verdict per line:
//! `{"valid": true, "command": "SOLVE"}` or `{"valid": false, "error": "..."}`. A line that is
//! not JSON is invalid (`syntax`). Used by `powercoder/inference/protocol_suite.py`.

use std::io::{self, BufRead, Write};

use command_ir::{lift, IrError};
use serde_json::{json, Value};

fn main() {
    let stdout = io::stdout();
    let mut out = stdout.lock();
    for line in io::stdin().lock().lines() {
        let line = line.expect("stdin is readable");
        let verdict = match serde_json::from_str::<Value>(&line).map_err(|e| IrError::Syntax(e.to_string())).and_then(|v| lift(&v)) {
            Ok(command) => json!({"valid": true, "command": command.name()}),
            Err(error) => json!({"valid": false, "error": error.to_string()}),
        };
        writeln!(out, "{verdict}").expect("stdout is writable");
    }
}

//! Read-only, typed Excel extraction. TOML is configuration, SQL is the query DSL;
//! this crate does not introduce an expression language or evaluate Excel formulas.

use calamine::{Data, Reader, open_workbook_auto_from_rs};
use clap::{Parser, Subcommand};
use regex::Regex;
use serde::Deserialize;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, fs, io::Cursor, path::{Path, PathBuf}};

// Pilot intake limits: intentionally smaller than Excel's maximum worksheet size.
const MAX_WORKBOOK_BYTES: u64 = 64 * 1024 * 1024;
const MAX_DATA_ROWS: u32 = 100_000;
// Microsoft's XLSX format permits at most 16,384 columns.
const XLSX_COLUMNS: u32 = 16_384;
// Policy limits bound regex compilation and untrusted parameter input.
const MAX_PATTERN_BYTES: usize = 1024;
const MAX_PARAMETER_BYTES: usize = 256;
// Spreadsheet coordinates in contracts and evidence are one-based.
const EXCEL_ORIGIN: u32 = 1;

#[derive(Parser)]
#[command(version, about = "Read-only Excel intake and approved parameter checks")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Discover actual worksheet names and dimensions; does not infer a contract.
    Inspect { workbook: PathBuf },
    /// Extract only fields declared by the reviewed TOML contract.
    Extract { workbook: PathBuf, contract: PathBuf },
    /// Apply a bounded, fully anchored Rust regex to a string parameter.
    Validate { pattern: String, value: String },
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Contract {
    version: u32,
    id: String,
    sheet_pattern: String,
    header_row: u32,
    columns: Vec<Column>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Column {
    name: String,
    header_pattern: String,
    kind: String,
}

/// Hash exact source bytes: a changed workbook or contract cannot reuse its old path.
fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

/// Compile a bounded regex with an entire-string match, not a substring policy.
fn anchored(pattern: &str) -> Result<Regex, String> {
    if pattern.len() > MAX_PATTERN_BYTES {
        return Err("pattern exceeds pilot policy limit".into());
    }
    Regex::new(&format!(r"\A(?:{pattern})\z")).map_err(|e| e.to_string())
}

/// Validate one string parameter with Rust's non-backtracking regex implementation.
fn validate(pattern: &str, value: &str) -> Result<bool, String> {
    if value.len() > MAX_PARAMETER_BYTES {
        return Err("parameter exceeds pilot policy limit".into());
    }
    Ok(anchored(pattern)?.is_match(value))
}

/// Read one bounded snapshot into memory; the parser and hash use the same bytes.
fn source(path: &Path) -> Result<Vec<u8>, String> {
    if fs::metadata(path).map_err(|e| e.to_string())?.len() > MAX_WORKBOOK_BYTES {
        return Err("workbook exceeds 64 MiB pilot intake limit".into());
    }
    let bytes = fs::read(path).map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_WORKBOOK_BYTES {
        return Err("workbook grew beyond intake limit".into());
    }
    Ok(bytes)
}

/// Convert a zero-based column to its familiar A1 column letters.
fn column_letters(column: u32) -> String {
    // Excel uses bijective base 26: A..Z, AA..AZ, ...
    const ALPHABET: u32 = 26;
    let mut index = column + EXCEL_ORIGIN;
    let mut letters = Vec::new();
    while index > 0 {
        let digit = (index - EXCEL_ORIGIN) % ALPHABET;
        letters.push((b'A' + digit as u8) as char);
        index = (index - EXCEL_ORIGIN) / ALPHABET;
    }
    letters.iter().rev().collect()
}

/// Convert a declared field without guessing units, formulas, dates, or null meaning.
fn typed(cell: &Data, kind: &str) -> Result<Value, String> {
    if matches!(cell, Data::Empty) {
        return Ok(Value::Null);
    }
    match (cell, kind) {
        (Data::String(text), "string") => Ok(json!(text)),
        (Data::Float(value), "number") if value.is_finite() => Ok(json!(value)),
        (Data::Int(value), "number") => Ok(json!(value)),
        (Data::Bool(value), "boolean") => Ok(json!(value)),
        _ => Err(format!("expected {kind}, found {cell:?}; review the contract")),
    }
}

/// Discover actual sheet names/dimensions for a human to author a reviewed contract.
fn inspect(path: &Path) -> Result<Value, String> {
    let bytes = source(path)?;
    let hash = digest(&bytes);
    let mut book = open_workbook_auto_from_rs(Cursor::new(bytes)).map_err(|e| e.to_string())?;
    let mut sheets = Vec::new();
    for name in book.sheet_names().to_vec() {
        let range = book.worksheet_range(&name).map_err(|e| e.to_string())?;
        sheets.push(json!({"name": name, "start": range.start(), "end": range.end()}));
    }
    Ok(json!({"source_sha256": hash, "sheets": sheets, "coordinates": "zero-based dimensions"}))
}

/// Extract one unambiguously matched sheet and its explicitly typed columns.
///
/// Returns rows with source hash, absolute sheet/row/cell addresses, and a reusable
/// resolved-layout map. Rejects formulas rather than silently trusting cached values.
fn extract(path: &Path, contract_path: &Path) -> Result<Value, String> {
    let contract_bytes = fs::read(contract_path).map_err(|e| e.to_string())?;
    let contract: Contract = toml::from_str(std::str::from_utf8(&contract_bytes).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    if contract.version != EXCEL_ORIGIN || contract.header_row < EXCEL_ORIGIN || contract.columns.is_empty() {
        return Err("contract v1 needs a positive header_row and at least one column".into());
    }
    let mut names = BTreeSet::new();
    for column in &contract.columns {
        if !validate("[a-z][a-z0-9_]*", &column.name)? || !names.insert(&column.name) {
            return Err("column names must be unique simple lowercase SQL identifiers".into());
        }
        if !["string", "number", "boolean"].contains(&column.kind.as_str()) {
            return Err("contract kind must be string, number, or boolean".into());
        }
    }
    let bytes = source(path)?;
    let hash = digest(&bytes);
    let mut book = open_workbook_auto_from_rs(Cursor::new(bytes)).map_err(|e| e.to_string())?;
    let sheet_regex = anchored(&contract.sheet_pattern)?;
    let sheets: Vec<_> = book.sheet_names().iter().filter(|name| sheet_regex.is_match(name)).cloned().collect();
    if sheets.len() != EXCEL_ORIGIN as usize {
        return Err(format!("expected one sheet match, found {}; discovery/review required", sheets.len()));
    }
    let sheet = &sheets[0];
    let range = book.worksheet_range(sheet).map_err(|e| e.to_string())?;
    let formulas = book.worksheet_formula(sheet).map_err(|e| e.to_string())?;
    let (end_row, end_column) = range.end().ok_or("worksheet is empty")?;
    if end_row > MAX_DATA_ROWS || end_column >= XLSX_COLUMNS {
        return Err("worksheet dimensions exceed pilot extraction limit".into());
    }
    let header_row = contract.header_row - EXCEL_ORIGIN;
    let mut layout = Vec::new();
    let mut positions = BTreeSet::new();
    for field in &contract.columns {
        let pattern = anchored(&field.header_pattern)?;
        let hits: Vec<_> = (0..=end_column).filter(|col| {
            range.get_value((header_row, *col)).is_some_and(|v| pattern.is_match(&v.to_string()))
        }).collect();
        if hits.len() != EXCEL_ORIGIN as usize || !positions.insert(hits[0]) {
            return Err(format!("ambiguous/missing header for {}; discovery/review required", field.name));
        }
        layout.push(hits[0]);
    }
    let mut rows = Vec::new();
    for row in (header_row + EXCEL_ORIGIN)..=end_row {
        if layout.iter().all(|col| matches!(range.get_value((row, *col)), None | Some(Data::Empty))) {
            continue;
        }
        let mut values = Map::new();
        let mut cells = Map::new();
        for (field, col) in contract.columns.iter().zip(&layout) {
            if formulas.get_value((row, *col)).is_some_and(|formula| !formula.is_empty()) {
                return Err(format!("formula at {sheet}!{}{}: recalculation is not available", column_letters(*col), row + EXCEL_ORIGIN));
            }
            let cell = range.get_value((row, *col)).unwrap_or(&Data::Empty);
            values.insert(field.name.clone(), typed(cell, &field.kind)?);
            cells.insert(field.name.clone(), json!(format!("{}{}", column_letters(*col), row + EXCEL_ORIGIN)));
        }
        values.insert("_source_sha256".into(), json!(hash));
        values.insert("_sheet".into(), json!(sheet));
        values.insert("_excel_row".into(), json!(row + EXCEL_ORIGIN));
        // JSON text stays portable across Parquet readers with different struct support.
        values.insert("_cells".into(), json!(serde_json::to_string(&cells).map_err(|e| e.to_string())?));
        rows.push(Value::Object(values));
    }
    if rows.is_empty() { return Err("no data rows matched the contract".into()); }
    Ok(json!({
        "extractor_version": env!("CARGO_PKG_VERSION"), "contract_id": contract.id,
        "source_sha256": hash, "contract_sha256": digest(&contract_bytes),
        "sheet": sheet, "header_row": contract.header_row, "columns": layout,
        "rows": rows, "formula_policy": "reject", "row_count": rows.len()
    }))
}

fn main() {
    let result = match Cli::parse().command {
        Command::Inspect { workbook } => inspect(&workbook),
        Command::Extract { workbook, contract } => extract(&workbook, &contract),
        Command::Validate { pattern, value } => validate(&pattern, &value).map(|valid| json!({"valid": valid})),
    };
    match result {
        Ok(value) => println!("{}", serde_json::to_string(&value).expect("JSON serialization")),
        Err(error) => { eprintln!("{error}"); std::process::exit(1); }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn policy_is_whole_string_and_bounded() {
        assert!(validate("SA1|VIC1", "SA1").unwrap());
        assert!(!validate("SA1|VIC1", "SA1' OR 1=1").unwrap());
        assert!(!validate("SA1|VIC1", "xSA1").unwrap());
        assert!(validate("[", "SA1").is_err());
        assert!(validate(".*", &"x".repeat(MAX_PARAMETER_BYTES + 1)).is_err());
    }

    #[test]
    fn addresses_are_excel_coordinates() {
        assert_eq!(column_letters(0), "A");
        assert_eq!(column_letters(26), "AA");
        assert_eq!(column_letters(XLSX_COLUMNS - 1), "XFD");
    }

    #[test]
    fn values_are_typed_without_guessing() {
        assert!(typed(&Data::String("12 MW".into()), "number").is_err());
        assert_eq!(typed(&Data::Int(12), "number").unwrap(), json!(12));
        assert_eq!(typed(&Data::Empty, "number").unwrap(), Value::Null);
    }
}

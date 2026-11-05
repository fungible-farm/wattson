//! Regression for the exact path discovered by the watcher, including Justfile.
use just_mcp::executor::TaskExecutor;
use just_mcp::types::{ExecutionContext, ExecutionRequest};
use std::collections::HashMap;

#[tokio::test]
async fn executes_capitalized_justfile_with_a_different_working_directory() {
    // Keep the regression within the executor's normal allowed project root.
    let cwd = std::env::current_dir().unwrap();
    let project = tempfile::tempdir_in(&cwd).unwrap();
    let other = tempfile::tempdir_in(&cwd).unwrap();
    let justfile = project.path().join("Justfile");
    std::fs::write(&justfile, "hello name=\"World\":\n    @echo {{name}}\n").unwrap();
    let request = ExecutionRequest {
        tool_name: format!("hello_{}", justfile.display()),
        parameters: HashMap::from([("name".to_string(), serde_json::json!("Ada"))]),
        context: ExecutionContext {
            working_directory: Some(other.path().to_string_lossy().to_string()),
            ..Default::default()
        },
    };
    let result = TaskExecutor::new().execute(request).await.unwrap();
    assert!(result.success, "{}", result.stderr);
    assert_eq!(result.stdout.trim(), "Ada");
}

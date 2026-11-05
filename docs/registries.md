# Gateway Registries

Four YAML files in `registries/` declare what the gateway exposes. Each is
validated by a JSON Schema in `registries/schema/` and rendered into
agentgateway config fragments that the gateway reloads by file watch.

## backends.yaml

Model serving endpoints. Entry fields: `name`, `kind` (llama-swap|candle|vllm|hosted),
`base_url`, optional `key_file` (path, never a literal key), optional `spend_cap_usd`
(required when `kind` is `hosted`).

**Writable by:** developer, operator

## tools.yaml

MCP endpoints available to agents. Entry fields: `name`, `mcp_url` (http URL),
`writable_by` (list of roles), `description`.

**Writable by:** developer, operator

## skills.yaml

Agent skill definitions. Entry fields: `name`, `path` (repo-relative file that
must exist), `description`.

**Writable by:** developer

## prompts.yaml

Prompt templates and recipes. Entry fields: `name`, `path` (must exist),
`writable_by` (list; recipes are writable only by listed roles), `description`.

**Writable by:** developer (config files), operator (some recipes)

## Validation and rendering

```bash
just registry-check          # validate all four registries
python3 scripts/registry_check.py --render  # validate + print gateway config fragments
```

The validation script checks: JSON Schema conformance, unique entry names,
referenced file paths exist, hosted backends have spend caps, no literal
secrets (rejects fields named api_key, apiKey, token, secret).

## Reload story

1. Edit a registry YAML file.
2. Run `just registry-check` to validate.
3. Run `python3 scripts/registry_check.py --render` to see the rendered gateway
   config fragments (mcp targets, llm models).
4. Write the rendered fragments to the gateway config directory.
5. The gateway's file watch picks up the change and reloads. Harness config
   (the agent's own setup) is unchanged.

## Live reload (verified)

`just gateway-reload-check` starts two throwaway MCP servers and a throwaway agentgateway, lists tools at one
fixed URL, adds a tool entry, re-renders, and lists again at the same URL. Observed: `tools-before:
['alpha_ping']` -> `tools-after: ['a_alpha_ping', 'b_beta_ping']`, container `StartedAt` unchanged.
Note the contract consequence: with one target the gateway serves bare tool names; with two or more it prefixes
`<target>_<tool>`, so adding the second tool server renames the first server's tools. Clients must not hard-code names.

## Not done


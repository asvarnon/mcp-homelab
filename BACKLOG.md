# mcp-homelab Backlog

## Low Priority

### R6: JSONC regex fragile with URLs

**File:** `mcp_homelab/setup/client_setup.py` — `_load_json()`
**Found:** March 15, 2026 (Codex review)

The JSONC comment stripping (`re.sub(r"//.*", "", text)`) will mangle URLs like `"https://example.com"` into `"https:`. Current risk is low — MCP config files contain local paths, not URLs. If a future MCP transport uses HTTP URLs in config, this will break.

**Options:**
- Add `json5` or `commentjson` dependency for proper JSONC parsing
- Accept current risk (document the limitation)

# mcp-homelab Backlog

## Open

### Dockerfile not fully implemented

Stub only — needs design work before use.

---

## Completed

### ~~R6: JSONC regex fragile with URLs~~

**Fixed:** March 15, 2026

The JSONC comment stripping regex in `client_setup.py` was updated to be URL-safe. Added tests to verify URLs containing `//` are not mangled. See `tests/unit/test_client_setup.py`.

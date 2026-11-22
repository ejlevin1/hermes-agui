# AGENTS.md — hermes-agui

- Python 3.14, uv. Tests: `uv run -q -p 3.14 pytest -q` (targeted files in lanes).
- Package `hermes_agui/` is generic: it must never import or name a host app (no "items" code here).
- Hermes internals: only public APIs (plugin context, `tools.interrupt`, `gateway.session_context` getters, approval
  context). Never read or write private registry fields (they differ between Hermes builds).
- Synthetic data only in tests and fixtures; never commit secrets.

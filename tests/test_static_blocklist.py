"""Keep the static-mode blocklist honest.

The `unsupported` map in backend/generate_client_tools.py is the single source
of truth for which tools are greyed out in the browser (Pyodide) build. There
is no runtime capability probe: a tool missing from that map renders as
clickable and only throws "Local processing failed" when a user runs it —
exactly the regression class documented in docs/static-wasm-limitations.md.

These tests diff that map against what static analysis of tools.py says each
tool actually needs, so adding a tool that shells out to `run([...])` or
imports a non-wasm package fails CI until it is blocklisted (or genuinely
wasm-safe).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"

# Packages with no usable wasm build. A tool importing any of these (even
# lazily, inside its function body) cannot run under Pyodide.
#
# Deliberately NOT here: openpyxl and python-pptx — pure Python, installed
# best-effort via micropip at runtime, so pdf-to-excel / pdf-to-powerpoint
# stay enabled in static mode.
NON_WASM_MODULES = {"weasyprint", "pdf2docx", "pyhanko", "ocrmypdf", "smtp_manager"}


def test_smtp_diagnostics_are_wired_in_both_deployment_modes_and_ui():
    python_smtp = (BACKEND / "smtp_manager.py").read_text(encoding="utf-8")
    worker = (BACKEND / "static" / "squish-email-worker.js").read_text(encoding="utf-8")
    ui = (BACKEND / "static" / "index.html").read_text(encoding="utf-8")

    for source in (python_smtp, worker):
        assert "credentials_rejected" in source
        assert "auth_not_supported" in source
        assert "diagnostic_id" in source
        assert "relay_response" in source
    assert "function renderSmtpDiagnostic" in ui
    assert "Technical details" in ui
    assert "textContent = fields.map" in ui  # relay text must not be inserted as HTML


def _unsupported_map() -> dict[str, str]:
    """Extract the literal `unsupported` dict from generate_client_tools.py
    without executing it."""
    tree = ast.parse((BACKEND / "generate_client_tools.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "unsupported"
                   for t in node.targets):
                return ast.literal_eval(node.value)
    pytest.fail("no `unsupported` map found in generate_client_tools.py")


def _static_fn_overrides() -> dict[str, str]:
    """Read browser-only function substitutions from the generator."""
    tree = ast.parse((BACKEND / "generate_client_tools.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "static_fn_overrides"
                   for t in node.targets):
                return ast.literal_eval(node.value)
    return {}


def _static_worker_handlers() -> set[str]:
    """Read tools implemented directly by the browser processing worker."""
    tree = ast.parse((BACKEND / "generate_client_tools.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "static_worker_handlers"
                   for t in node.targets):
                return ast.literal_eval(node.value)
    return set()


def _function_graph() -> tuple[dict[str, bool], dict[str, set[str]]]:
    """Static analysis of tools.py's top-level functions.

    Returns (directly_native, calls) where directly_native[name] is True when
    the function body itself shells out via run(...) or imports a non-wasm
    module, and calls[name] is the set of same-module functions it invokes
    (used for the transitive closure).
    """
    tree = ast.parse((BACKEND / "tools.py").read_text())
    directly_native: dict[str, bool] = {}
    calls: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        native = False
        invoked: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                if sub.func.id == "run":
                    native = True
                elif sub.func.id != node.name:
                    invoked.add(sub.func.id)
            elif isinstance(sub, ast.Import):
                if {a.name.split(".")[0] for a in sub.names} & NON_WASM_MODULES:
                    native = True
            elif isinstance(sub, ast.ImportFrom) and sub.module:
                if sub.module.split(".")[0] in NON_WASM_MODULES:
                    native = True
        directly_native[node.name] = native
        calls[node.name] = invoked

    # Transitive closure: a tool is native if anything it calls (directly or
    # through helpers) is native.
    def needs_native(name: str, seen: frozenset[str] = frozenset()) -> bool:
        if name in seen:  # cycle guard
            return False
        if directly_native.get(name, False):
            return True
        return any(needs_native(c, seen | {name}) for c in calls.get(name, ()))

    closure = {name: needs_native(name) for name in directly_native}
    return closure, calls


def test_every_native_tool_is_blocklisted():
    import tools

    unsupported = _unsupported_map()
    overrides = _static_fn_overrides()
    worker_handlers = _static_worker_handlers()
    closure, _ = _function_graph()

    unblocked = {
        t.key: overrides.get(t.key, t.fn.__name__)
        for t in tools.TOOLS
        if closure.get(overrides.get(t.key, t.fn.__name__), False)
        and t.key not in unsupported
        and t.key not in worker_handlers
    }
    assert not unblocked, (
        "these tools shell out or import non-wasm packages but are missing "
        f"from generate_client_tools.py's `unsupported` map: {unblocked}. "
        "Add them there or they will crash at run time in static mode."
    )


def test_blocklist_keys_exist_in_registry():
    import tools

    registry_keys = {t.key for t in tools.TOOLS}
    stale = set(_unsupported_map()) - registry_keys
    assert not stale, (
        f"`unsupported` lists keys that are not in the tool registry: {stale} "
        "(renamed or removed tool? the entry is dead)"
    )


def test_static_function_overrides_are_real_and_wasm_safe():
    import tools

    registry_keys = {t.key for t in tools.TOOLS}
    overrides = _static_fn_overrides()
    closure, _ = _function_graph()
    assert set(overrides) <= registry_keys
    missing = {name for name in overrides.values() if not hasattr(tools, name)}
    assert not missing, f"static override functions do not exist: {missing}"
    native = {key: fn for key, fn in overrides.items() if closure.get(fn, False)}
    assert not native, f"static overrides still need native engines: {native}"


def test_static_worker_handlers_are_explicit_and_real():
    import tools

    handlers = _static_worker_handlers()
    registry_keys = {t.key for t in tools.TOOLS}
    assert handlers <= registry_keys
    template = (BACKEND / "client_tools_worker.template.js").read_text()
    missing = {key for key in handlers if f"key === '{key}'" not in template}
    assert not missing, f"static worker handlers have no dispatch branch: {missing}"


def test_blocklist_only_covers_native_tools():
    """The inverse direction, as an early-warning: if a blocklisted tool no
    longer detects as native, either the analysis missed a path (extend it)
    or the tool was ported and can come OFF the blocklist."""
    import tools

    unsupported = _unsupported_map()
    closure, _ = _function_graph()

    fn_by_key = {t.key: t.fn.__name__ for t in tools.TOOLS}
    not_detected = {k for k, reason in unsupported.items()
                    if k in fn_by_key and not closure.get(fn_by_key[k], False)}
    assert not not_detected, (
        f"blocklisted but not detected as native by static analysis: "
        f"{sorted(not_detected)} — check whether these can be enabled in "
        "static mode, or extend NON_WASM_MODULES/_function_graph"
    )

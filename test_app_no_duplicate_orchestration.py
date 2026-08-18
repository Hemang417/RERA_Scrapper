"""
Stops app.py drifting back into a second, divergent copy of the pipeline.

The bug this exists for: app.py used to re-implement main.py's acquisition
stages inline -- its own resolve, its own token handling, its own 401/403
retry, its own category fetch, document download and promoter portfolio,
roughly 160 lines of it. Two copies of the same logic in one repo means one
of them is always the stale one, and the PRD's own FR-OPS-06 requires the
Streamlit UI to wrap the same functions the CLI uses with "no duplicated
logic".

Both entry points now call states.get_adapter(...).acquire(). This guard
makes that structural rather than a convention someone has to remember: if
app.py imports the scraping modules again, it is almost certainly because
someone started re-implementing acquisition inside it, and the suite fails
at that moment rather than months later when the two copies disagree.

Checked by parsing the import statements, not by grepping text -- a mention
in a comment or docstring is fine, an actual import is not.

Run directly: python test_app_no_duplicate_orchestration.py
"""

import ast
import io

_APP = "app.py"

# Modules only the acquisition layer has any business importing. app.py
# should reach all of them THROUGH the adapter.
_FORBIDDEN = ("api_client", "session_auth", "promoter_portfolio")

# Importing this is the positive signal that app.py still goes through the
# seam. Without it, the test could pass simply because app.py was gutted,
# renamed, or emptied -- the vacuous pass that test_guardrails_doc.py exists
# to warn about.
_REQUIRED = "states"


def _imported_module_names(path):
    with io.open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


def test_app_does_not_import_the_acquisition_modules():
    imported = _imported_module_names(_APP)
    leaked = sorted(set(_FORBIDDEN) & imported)
    assert not leaked, (
        f"{_APP} imports {leaked}, which only the acquisition layer should touch. "
        f"If acquisition logic is being added back into the UI, put it on the state "
        f"adapter instead (states/adapter_maharashtra.py) so the CLI and the UI keep "
        f"sharing one implementation."
    )
    print("test_app_does_not_import_the_acquisition_modules: PASS")


def test_app_still_goes_through_the_state_seam():
    """Anti-vacuous-pass guard: without this, deleting app.py's body would
    make the test above pass perfectly."""
    imported = _imported_module_names(_APP)
    assert _REQUIRED in imported, (
        f"{_APP} no longer imports {_REQUIRED!r}. Either it stopped using the state "
        f"adapter, or this guard is now checking a file that does nothing."
    )
    source = io.open(_APP, "r", encoding="utf-8").read()
    assert ".acquire(" in source, f"{_APP} imports states but never calls acquire()"
    print("test_app_still_goes_through_the_state_seam: PASS")


def test_both_entry_points_call_the_same_acquire():
    """main.py and app.py must reach acquisition the same way."""
    for path in ("main.py", _APP):
        source = io.open(path, "r", encoding="utf-8").read()
        assert "get_adapter(" in source, f"{path} does not resolve an adapter"
        assert ".acquire(" in source, f"{path} does not call acquire()"
    print("test_both_entry_points_call_the_same_acquire: PASS")


if __name__ == "__main__":
    test_app_does_not_import_the_acquisition_modules()
    test_app_still_goes_through_the_state_seam()
    test_both_entry_points_call_the_same_acquire()
    print("\nAll tests passed.")

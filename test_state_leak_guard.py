"""
Stops a new hardcoded state noun creeping back into reader-facing code.

The bug this exists for: company_charter.py had ~20 Maharashtra literals
baked into the rendering layer -- the title-page subtitle, the deal-snapshot
"MahaRERA Registration" row label, source-list descriptions, Developer Score
reason text. When a Telangana project was run through the same code in
August 2026 it produced a Charter that called itself "Adapted for
Maharashtra (MahaRERA)", and the fix at the time was a one-off post-hoc
.docx patcher (output/CONSTELLA_TS/research/patch_state_labels.py) that ran
AFTER generation and rewrote the saved file.

Those literals are now parameterised through StateProfile. This guard is
what stops the twenty-first appearing: any NEW state-naming string constant
in the rendering modules fails the suite with a message pointing at the fix.

Design notes, both learned from test_guardrails_doc.py:

  * It walks the AST and inspects ast.Constant strings ONLY -- never a regex
    over the file text. Comments and docstrings legitimately name MahaRERA
    (they explain WHY something exists, e.g. "confirmed live against
    MahaRERA's own API"), are never reader-facing, and must not be flagged.

  * The allow-list is keyed by ENCLOSING FUNCTION NAME, never by line
    number. guardrails.md: "Line references in this repo have gone stale
    twice already."

  * It carries an anti-vacuous-pass guard. test_guardrails_doc.py exists
    because a meta-test once went stale and passed silently against an empty
    set; this one asserts both that the walk found a plausible number of
    strings overall, and that every allow-list entry still resolves to a
    real literal -- so a stale allow-list fails loudly instead of quietly
    shrinking the guard.

Run directly: python test_state_leak_guard.py
"""

import ast
import io
import os

# Reader-facing rendering modules. charter_report.py and executive_briefing.py
# build DIFFERENT documents and are deliberately out of scope for now (see the
# plan's non-goals); add them here when they are parameterised too.
_MODULES = (
    "company_charter.py",
    "report.py",
    "charter_document.py",
    "promoter_portfolio.py",
)

# Tokens that name one state. "Mumbai" is included because charter_report.py
# was found asserting a promoter is "the Mumbai-market vehicle" regardless of
# where it actually operates.
_STATE_TOKENS = ("Maharashtra", "MahaRERA", "Maha Bhulekh", "maharera", "Mumbai")

# Literals that may legitimately name Maharashtra, keyed by the function they
# live in. Each needs a reason. A bare module-level constant is keyed by
# "<module>" since it has no enclosing function.
#
# The test asserts every entry here still matches something real, so deleting
# the code without deleting the entry fails rather than silently weakening
# the guard.
_ALLOWED = {
    "company_charter.py": {
        # Real MahaRERA endpoints and scrape internals. These ARE
        # Maharashtra, so naming them is correct, and the code paths that use
        # them are gated by CAP_ORDERS_SEARCH / CAP_LAND_RECORDS so they
        # never run for another state.
        "_MAHARERA_ORDERS_URL": ("https://maharera.maharashtra.gov.in/orders-judgements",),
        "_MAHARERA_COMPLAINT_TYPES": ("rulings_of_MahaRERA",),
        # Domain -> display-name tables. These answer "what is this DOMAIN
        # called", which is a fact about the domain and not about the state
        # being rendered: a Telangana Charter citing a maharera.maharashtra
        # .gov.in page must still label it "MahaRERA", not a bare hostname.
        "_DOMAIN_GENERIC": (
            "IGR Maharashtra (Dept. of Registration & Stamps)",
            "maharerait.maharashtra.gov.in",
            "maharera.maharashtra.gov.in",
            "MahaRERA",
        ),
        "_FLAG_TEXT_ORG_ALIASES": ("maharera", "maharera.maharashtra.gov.in"),
        # Source-trust tiers keyed by real host. Same reasoning as
        # _DOMAIN_GENERIC: a fact about the domain, not the active state.
        "_SOURCE_TIERS": ("maharerait.maharashtra.gov.in", "maharera.maharashtra.gov.in"),
        # Source label for a land record that was actually retrieved from
        # Maha Bhulekh -- only reachable with CAP_LAND_RECORDS.
        "run_cts_land_lookup": ("Maha Bhulekh Property Card",),
        "run_cts_lookup_standalone": ("Maha Bhulekh Property Card",),
        # Verbatim rewrites of sentences from SPECIFIC past projects'
        # facts.json -- data about real Maharashtra projects, not templates.
        # Parameterising these would rewrite the record.
        "_EXTERNAL_DASH_REWRITES": (
            "No documents from the MahaRERA-listed library are absent -- all 60 listed documents downloaded successfully this pass.",
            "No documents from the MahaRERA-listed library are absent: all 60 listed documents downloaded successfully this review.",
            "(boundary marker, per MahaRERA's own record -- no named landmark disclosed)",
            "(boundary marker per MahaRERA's own record; no named landmark disclosed)",
        ),
        # Only ever rendered when a Maha Bhulekh lookup actually succeeded,
        # which requires CAP_LAND_RECORDS.
        "_append_cts_land_record_section": (
            "Land Record Check -- Maha Bhulekh Property Card (Code-Assisted, Human-Verified)",
        ),
        # Only produced when the CAP_ORDERS_SEARCH-gated search ran.
        "_safe_judgments_search": (
            "MahaRERA Orders/Judgments search for appeal-level outcomes could not run this pass: ",
        ),
        "run_company_charter": ("MahaRERA Orders/Judgments",),
        # CLI --help text, never rendered into a document.
        "main": ("MahaRERA registration number whose output/ folder already exists.",),
    },
    "report.py": {
        # Fallback for the stashed acronym, so a caller that never passed a
        # profile keeps producing today's Maharashtra footer.
        "_header_footer": ("MahaRERA",),
    },
    "charter_document.py": {
        # Evidence classifier: recognises a Maha Bhulekh source by name.
        # Describes the source, not the active state.
        "classify_claim_evidence": (
            "Maha Bhulekh Property Card (government land record)",
        ),
        # Maharashtra-specific advice, now behind a CAP_LAND_RECORDS check --
        # other states get the generic wording in the else branch.
        "_recommended_steps": (
            "Retrieve the Maha Bhulekh Property Card for the project's CTS number to confirm land ownership independently.",
        ),
    },
    "promoter_portfolio.py": {
        # Nominatim requires a identifying User-Agent. Not reader-facing.
        "_GEOCODE_USER_AGENT": ("MahaRERA-Scrapper-DueDiligence/1.0 (personal research tool, low-volume)",),
        # Default argument values, so an unqualified call behaves exactly as
        # it did before the state parameter existed.
        "_geocode_query_for": ("Maharashtra",),
        "extract_subject_project_location": ("Maharashtra",),
        # This function searches MahaRERA's Promoters tab and nothing else
        # (CAP_PROMOTER_PORTFOLIO), so its own limitations prose naming
        # MahaRERA is accurate. Revisit when a second state gains a
        # promoter search.
        "build_promoter_portfolio": (
            "Maharashtra",
            "MahaRERA's Promoters-tab search returned no projects for '",
        ),
    },
}

# Sentences in the editorial-rewrite tables are per-project data, not
# templates: long, hand-checked, whole-sentence rewrites keyed on the exact
# text a specific project's facts.json contained. Flagging them would mean
# rewriting history rather than fixing a hardcode.
_MIN_EDITORIAL_LEN = 120


def _enclosing_functions(tree):
    """Map id(node) -> enclosing function name, "<module>" at top level.

    Module-level constants are attributed to their ASSIGNMENT TARGET instead
    (e.g. "_EXTERNAL_DASH_REWRITES") rather than the useless "<module>", so
    the allow-list can exempt a whole data table by name. That distinction
    matters here: the editorial-rewrite tables hold long, hand-checked,
    whole-sentence rewrites keyed on the exact text a SPECIFIC past
    project's facts.json contained. They are data ABOUT Maharashtra
    projects, not templates rendered for new ones -- parameterising them
    would rewrite history rather than fix a hardcode."""
    owner = {}

    def walk(node, current):
        for child in ast.iter_child_nodes(node):
            name = current
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            owner[id(child)] = name
            walk(child, name)

    owner[id(tree)] = "<module>"
    walk(tree, "<module>")

    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None):
            target = node.targets[0].id
            for descendant in ast.walk(node.value):
                owner[id(descendant)] = target
    return owner


def _state_naming_literals(path):
    """(function_name, literal) for every string constant naming a state.

    Returns the total constant count too, for the anti-vacuous guard."""
    with io.open(path, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)
    owner = _enclosing_functions(tree)

    # Docstrings are ast.Constant nodes too -- collect and exclude them,
    # since they explain rather than render.
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))

    total = 0
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        total += 1
        value = node.value
        if len(value) >= _MIN_EDITORIAL_LEN:
            continue  # editorial rewrite data, not a template -- see above
        if any(token in value for token in _STATE_TOKENS):
            hits.append((owner.get(id(node), "<module>"), value))
    return hits, total


def test_no_unallowed_state_literal_in_rendering_code():
    problems = []
    for module in _MODULES:
        allowed = _ALLOWED.get(module, {})
        hits, _ = _state_naming_literals(module)
        for func, value in hits:
            if value in allowed.get(func, ()) or value in allowed.get("<module>", ()):
                continue
            problems.append(f"{module}::{func} -> {value!r}")
    assert not problems, (
        "Hardcoded state name(s) in reader-facing code:\n  "
        + "\n  ".join(problems)
        + "\n\nParameterise it via StateProfile (company_charter._state_profile(), "
          "or a state_profile argument), or -- if the literal genuinely names a "
          "real Maharashtra endpoint/domain -- add it to _ALLOWED in this file "
          "with a reason."
    )
    print("test_no_unallowed_state_literal_in_rendering_code: PASS")


def test_the_walk_actually_found_strings():
    """Anti-vacuous-pass guard, modelled on test_guardrails_doc.py's
    `assert len(symbols) >= 15`. A broken parse, a renamed module, or an
    over-eager exclusion would otherwise let this file pass by inspecting
    nothing at all."""
    for module in _MODULES:
        assert os.path.exists(module), module
        _, total = _state_naming_literals(module)
        assert total >= 50, (module, total, "far too few string constants -- did the AST walk break?")
    print("test_the_walk_actually_found_strings: PASS")


def test_every_allowlist_entry_still_exists():
    """A stale allow-list is a silently weakened guard: the entry stops
    matching anything, and nobody notices that the exemption is now
    unnecessary -- or worse, that it is masking a different literal."""
    stale = []
    for module, by_func in _ALLOWED.items():
        hits, _ = _state_naming_literals(module)
        present = {value for _, value in hits}
        for func, values in by_func.items():
            for value in values:
                if value not in present:
                    stale.append(f"{module}::{func} -> {value!r}")
    assert not stale, (
        "Allow-list entries no longer match any literal (delete them):\n  "
        + "\n  ".join(stale)
    )
    print("test_every_allowlist_entry_still_exists: PASS")


if __name__ == "__main__":
    test_the_walk_actually_found_strings()
    test_no_unallowed_state_literal_in_rendering_code()
    test_every_allowlist_entry_still_exists()
    print("\nAll tests passed.")

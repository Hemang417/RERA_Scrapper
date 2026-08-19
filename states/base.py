"""
State-RERA declarations: what varies between India's ~30 RERA jurisdictions,
and nothing else.

RERA is a state subject -- there is no national project registry or API (the
Ministry's own rera.mohua.gov.in is a policy site with an aggregate weekly
tracker, no project search). So supporting a second state means supporting a
second portal, not pointing an existing client at a second host.

TWO SEAMS, deliberately separate:

  StateProfile   -- pure data. State name, regulator, acronym, reg-no shape,
                    portal domains, capability set. Serialises into
                    facts["state"] and run_meta.json, so it travels with the
                    document and a later render can read it back off disk.

  StateAdapter   -- behaviour. One method, acquire(), covering what main.py
                    calls stages 1-6. Only main.py/app.py ever hold one.

They must stay separate because a Charter can be rendered with NO adapter at
all: run_company_charter(pre_built_facts=..., category_data={}) built the
CONSTELLA (Telangana) Charter end-to-end with no portal, no browser and no
token. If the reader-facing labels lived on the adapter object, that path
would require instantiating a scraper just to render a hand-built facts dict.

WHY typing.Protocol AND NOT AN ABC: this repo has no inheritance anywhere --
every `class` outside these declarations is an underscore-prefixed test fake.
A Protocol is structural, so ts_rera_client.py (free functions, its own
exception hierarchy, no config import) can be wrapped by an adapter that
never imports this module. An ABC would force a rewrite to subclass.

WHY acquire() IS ONE COARSE CALL rather than resolve()/authenticate()/
fetch()/download(): Telangana CANNOT split resolve from auth -- TS-RERA
CAPTCHA-gates the search itself -- so a fine-grained protocol would force a
fake authenticate() returning None. And the orchestration BETWEEN MahaRERA's
stages is itself MahaRERA-specific (the 401/403 retry at main.py reads
NO_AUTH_CATEGORIES and re-solves a MahaRERA CAPTCHA); leaving it in main.py
makes every future adapter inherit logic that cannot apply to it.

CAPABILITIES, NOT STUB METHODS: a state declares what it HAS. A state
lacking something omits the capability AND returns the empty value
(category_data={}, documents_manifest=[], promoter_portfolio=None) plus an
honest sentence in AcquisitionResult.notes. Never a stub raising
NotImplementedError, never a fabricated shape. This generalises the one
declarative seam that already existed: config.NO_AUTH_CATEGORIES, read by
main.py to decide which categories to even attempt.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol


# --- Capabilities ----------------------------------------------------------
#
# Each names something a state's public portal either offers or does not.
# Checked with profile.can(...) BEFORE doing anything shaped like it, so a
# state that lacks one never has MahaRERA-only work run against it.

CAP_LOOKUP_BY_REG_NO = "lookup_by_reg_no"
"""The portal can find a project FROM its registration number.

Not universal, despite being the obvious way in. Telangana's public record
does not even display its own registration number -- confirmed in our own
capture (output/CONSTELLA_TS/raw/ts_rera_project.json carries
official_ts_rera_registration_certificate_number: null and a gap note saying
the number lives behind a separate CAPTCHA-gated 'View Certificate' link).
TS-RERA search is BY PROJECT NAME only."""

CAP_CATEGORY_API = "category_api"
"""Per-category endpoints exist (MahaRERA's 9). Without this, category_data
is {} and the Charter renders from pre_built_facts or a flat raw_record."""

CAP_DOCUMENTS = "documents"
"""A downloadable document set exists. Telangana's guest view exposes only
the promoter's own submitted application -- no title report, no sanctioned
plans, no professional certificates."""

CAP_SEPARATE_AUTH = "separate_auth"
"""Resolve and authenticate are separable steps. False where the portal
CAPTCHA-gates the search itself."""

CAP_PROMOTER_PORTFOLIO = "promoter_portfolio"
"""A promoter-name search exists, returning that promoter's other projects
in the same shape as a project search."""

CAP_ORDERS_SEARCH = "orders_search"
"""THIS PIPELINE CAN SEARCH THIS AUTHORITY'S ORDERS/JUDGMENTS.

Read that literally. It does NOT mean "the authority publishes orders
somewhere" -- it means a scraper for THEM exists here, so the gated code can
safely run.

The distinction is not academic. This capability gates
company_charter._safe_judgments_search, which is MahaRERA's OWN Orders
search. Karnataka was first declared with it set true on the reasoning that
K-RERA does publish judgements (it does, at /viewAllJudgements) -- and a
Karnataka project promptly started firing a MahaRERA search, with retries,
against a portal that could never match it. Exactly the bug the Telangana
gate was added to prevent.

So: declare this ONLY when this pipeline has an orders search implemented
for that authority. Publishing orders you cannot yet read is an absence, and
belongs in AcquisitionResult.notes."""

CAP_LAND_RECORDS = "land_records"
"""THIS PIPELINE CAN QUERY THIS STATE'S LAND RECORDS. Same reading as
CAP_ORDERS_SEARCH above -- wired in here, not merely existing.
(Maharashtra's Maha Bhulekh is the only one wired in.)
Gating on this stops the mahabhumi district lookup running for states whose
districts it has never heard of."""

ALL_CAPABILITIES = frozenset({
    CAP_LOOKUP_BY_REG_NO,
    CAP_CATEGORY_API,
    CAP_DOCUMENTS,
    CAP_SEPARATE_AUTH,
    CAP_PROMOTER_PORTFOLIO,
    CAP_ORDERS_SEARCH,
    CAP_LAND_RECORDS,
})
"""Typo guard -- a capability string not in here is a bug, not a state that
happens to lack something. Asserted by test_state_profiles.py."""


# --- The profile -----------------------------------------------------------


@dataclass(frozen=True)
class StateProfile:
    """Everything the RENDERING layer needs to know about which state it is.

    Frozen because it is used as a module-level constant and must be safe to
    share across the two _fill_template calls (Internal then External) that
    run against the same facts dict.

    NOTE this is deliberately NOT sourced from the .docx template, even
    though the template still carries [State] / [State RERA acronym] /
    [Regulator] / [Local planning authority] placeholders. The template lives
    under gitignored output/ and is not in version control, so trusting its
    text as runtime data would let a hand-edit silently mis-render a state.
    The template's placeholders are documentation of intent, pinned by a
    test; the profile is the source of truth.
    """

    code: str
    """Short key: "MH", "TG", "GJ", "KA". Also what run_meta.json stores."""

    state_name: str
    """Full state name as a reader should see it: "Maharashtra"."""

    rera_acronym: str
    """How the authority is normally referred to: "MahaRERA", "TG-RERA"."""

    regulator_name: str
    """Full legal name of the authority, for first-use expansion."""

    planning_authority_label: str
    """What to call the local planning authority in prose. Maharashtra has
    many (MCGM/MMRDA/PMRDA/CIDCO...) so this stays generic there; Telangana's
    is effectively always HMDA in the districts we see."""

    reg_no_pattern: str
    """Regex matching this state's registration-number format.

    WARNING -- these are NOT globally unique. Maharashtra and Telangana both
    use P + 11 digits (P51800077150 vs P02400003865) and are separable only
    by the embedded district code, which is an empirical observation and not
    a published specification. See states/__init__.py::resolve_state."""

    portal_domains: tuple = ()
    """Hosts belonging to this authority. Used to recognise a source as
    coming from the state's own record -- charter_document's evidence
    classifier previously hardcoded `if "maharera" in lowered`, which
    silently downgraded a Telangana RERA source to 'stated only'."""

    domain_labels: tuple = ()
    """((domain, External-facing label), ...) -- state-specific rows for the
    External Sources list. Consulted before the shared national table."""

    capabilities: frozenset = frozenset()
    """Which of the CAP_* above this state's public portal actually offers."""

    def can(self, capability: str) -> bool:
        """True when this state offers `capability`.

        Raises on an unknown capability rather than returning False: a typo
        must not read as 'this state doesn't have it', which would silently
        skip work for every state."""
        if capability not in ALL_CAPABILITIES:
            raise ValueError(
                f"Unknown capability {capability!r} -- not in ALL_CAPABILITIES. "
                "Add it to states/base.py rather than passing a bare string."
            )
        return capability in self.capabilities

    def as_facts_dict(self) -> dict:
        """The reader-facing subset, for facts["state"].

        Only the five fields a rendered document or a later re-render needs.
        Capabilities and reg-no patterns are pipeline concerns and stay out
        of the persisted record."""
        return {
            "code": self.code,
            "state_name": self.state_name,
            "rera_acronym": self.rera_acronym,
            "regulator_name": self.regulator_name,
            "planning_authority_label": self.planning_authority_label,
        }


# --- The acquisition seam --------------------------------------------------


def storage_key(reg_no: str) -> str:
    """A filesystem-safe form of a registration number.

    MahaRERA's P51800077150 is safe by luck. Most other authorities' are
    not: Gujarat issues
        PR/GJ/SURAT/SURAT CITY/SUDA/PAA12907/120224/311228
    and Karnataka
        PRM/KA/RERA/1251/446/PR/220422/004789
    -- both full of path separators. Used unchanged as a directory name,
    the first one silently produced a SIX-LEVEL nested tree instead of one
    project folder, and would equally have broken the Charter's output
    filename.

    So AcquisitionResult.reg_no carries this safe key (it is documented as
    the primary key of output/<reg_no>/), and the authority-issued number
    travels separately in `registration_number` for citation and display.
    """
    safe = re.sub(r"[\/:*?\"<>|]+", "-", (reg_no or "").strip())
    safe = re.sub(r"\s+", "_", safe)
    safe = re.sub(r"-{2,}", "-", safe).strip("-_.")
    return safe or "UNKNOWN"


@dataclass
class AcquisitionResult:
    """What an adapter hands back: everything above this line is
    state-neutral, everything below it is the state's own business.

    The empty defaults are the point. A state without CAP_DOCUMENTS returns
    documents_manifest=[] and says so in `notes` -- it does not invent a
    manifest shape, and the Charter renders an honest absence rather than an
    empty Document Library table.
    """

    profile: StateProfile
    reg_no: str
    """Primary key of output/<reg_no>/. For a state without
    CAP_LOOKUP_BY_REG_NO the adapter DERIVES one and records in `notes` that
    it is pipeline-assigned, not regulator-issued."""

    registration_number: str | None = None
    """The authority-issued registration number, when it differs from
    `reg_no` -- which is the filesystem key (see storage_key). None means
    the two are the same, as they are for MahaRERA."""

    project_id: str | None = None
    detail_url: str | None = None
    category_data: dict = field(default_factory=dict)
    documents_manifest: list = field(default_factory=list)
    documents_dir: str | None = None
    complaint_orders_manifest: list = field(default_factory=list)
    complaint_orders_dir: str | None = None
    promoter_name: str | None = None
    promoter_portfolio: dict | None = None
    raw_record: dict | None = None
    """A flat scrape for states with no category API (Telangana's
    PrintPreview parse). None where CAP_CATEGORY_API applies."""

    auth_source: str = "none"
    """Existing main.py vocabulary: explicit | cached | fresh_browser | none."""

    categories_not_published: set = field(default_factory=set)
    """Categories this AUTHORITY does not publish at all, as opposed to ones
    whose fetch failed. Both arrive downstream as None, and conflating them
    is a reporting lie: GujRERA's run summary reported spocs / sro_details /
    complaints / appeals as "FAILED" when nothing had failed -- Gujarat
    simply has no such endpoint. "Failed" invites a retry and suggests the
    data exists; "not published" is the actual finding."""

    notes: list = field(default_factory=list)
    """Honest 'this state cannot X' sentences, folded into the Charter's
    gaps/absence notes. THE most important field for a thin state -- it is
    what keeps a missing capability visible instead of looking like a clean
    check."""


class ProgressReporter(Protocol):
    """How an adapter talks to whoever invoked it.

    Exists because acquire() absorbs the multi-candidate disambiguation that
    is input() in main.py and st.radio in app.py -- the one genuinely
    interactive step in the acquisition layer.
    """

    def info(self, msg: str) -> None: ...
    def warn(self, msg: str) -> None: ...
    def ok(self, msg: str) -> None: ...
    def choose(self, prompt: str, options: list) -> int | None:
        """Index of the chosen option, or None for 'cannot ask'.

        None is NOT an error -- the CLI returns it when stdin is not a TTY,
        preserving main.py's existing exit-code-2 behaviour, and Streamlit
        returns it because it cannot block (it re-runs top-to-bottom and
        drives selection through session_state instead)."""
        ...


@dataclass
class AcquisitionContext:
    """Everything acquire() needs that is not the query itself."""

    output_dir: str
    reporter: ProgressReporter
    headed: bool = False
    explicit_token: str | None = None
    no_auto_auth: bool = False
    captcha_timeout: int = 300
    project_id_override: str | None = None
    prior: object | None = None
    """run_archive's prior-run snapshot, as a plain dict with keys
    manifest / documents_dir / complaint_orders_manifest /
    complaint_orders_dir. Usually supplied by on_resolved rather than set
    directly."""

    on_resolved: object | None = None
    """Optional callback, invoked as on_resolved(reg_no) as soon as the
    project resolves and BEFORE anything is fetched or written. Returns the
    `prior` dict described above.

    This exists to solve one ordering constraint without breaking the
    single-coarse-call design: archiving the previous run is keyed on
    reg_no, so it cannot happen before resolve; but archiving is entirely
    state-neutral (run_archive) and belongs to the caller, not to a state
    adapter. The callback lets main.py do its archiving at exactly the right
    moment while acquire() stays one call."""


class StateAdapter(Protocol):
    """One method. No optional methods, no NotImplementedError stubs.

    A state that cannot do something declares that through
    profile.capabilities and returns the empty value -- see AcquisitionResult.
    """

    profile: StateProfile

    def acquire(self, query: str, ctx: AcquisitionContext) -> AcquisitionResult: ...


class StateAcquisitionError(Exception):
    """Base for everything an adapter can fail with.

    Callers catch THIS, so a new failure mode added later degrades cleanly
    instead of escaping as a traceback. That is not hypothetical: a
    K-RERA run crashed with a raw requests.exceptions.ConnectionError
    straight past main.py, because main.py only caught
    StateResolutionError and a portal outage is not a resolution failure."""


class StateResolutionError(StateAcquisitionError):
    """Could not find the project on the state's portal. Adapters wrap their
    own portal-specific errors (resolver.ProjectNotFoundError,
    ts_rera_client.TSReraNotFoundError) in this at their own boundary --
    deliberately NOT by making those subclass this, so both modules stay
    usable standalone."""


class StateAuthError(StateAcquisitionError):
    """CAPTCHA/token acquisition failed. Same wrapping discipline."""


class StateFetchError(StateAcquisitionError):
    """The authority's portal could not be reached, or kept failing.

    Distinct from StateResolutionError on purpose: "the portal is down" and
    "this project does not exist there" call for completely different
    responses from whoever reads the message, and only one of them is worth
    retrying later."""


def fetch_with_retry(call, *, what, attempts=3, base_delay=3.0, reporter=None):
    """Runs `call()`, retrying transient network failures with backoff.

    Indian state portals are not highly available. K-RERA's search page is a
    6.3 MB response and the single most failure-prone request in that
    adapter; one dropped connection used to kill an entire run before a
    single byte was written. This is deliberately NOT a general-purpose
    retry: it retries CONNECTION-level failures only, because an HTTP 404 or
    a bad parse will fail identically however many times it is repeated.

    Raises StateFetchError once the attempts are exhausted, so the caller
    gets a clean, catchable failure with the portal named."""
    import time

    last = None
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as e:  # noqa: BLE001 -- narrowed just below
            name = type(e).__name__
            transient = (
                "ConnectionError" in name
                or "Timeout" in name
                or "RemoteDisconnected" in name
                or "ProtocolError" in name
                or "SSLError" in name
            )
            if not transient:
                raise
            last = e
            if attempt < attempts and reporter is not None:
                reporter.warn(
                    f"{what} failed ({name}), attempt {attempt}/{attempts} -- retrying."
                )
            if attempt < attempts:
                time.sleep(base_delay * attempt)

    raise StateFetchError(
        f"{what} failed after {attempts} attempts ({type(last).__name__}: {last}). "
        f"The portal appears to be unreachable right now; this is not a problem with "
        f"the project or the registration number."
    )

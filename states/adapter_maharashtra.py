"""
The MahaRERA acquisition adapter -- main.py's stages 1-6, moved verbatim.

Kept in its own module, separate from maharashtra.py's PROFILE, for the same
reason StateProfile and StateAdapter are separate types: the profile is pure
data that a render needs, and importing it must never drag in Playwright,
requests, or the whole scraping stack. `run_company_charter(pre_built_facts=
...)` renders a Charter with no adapter at all, and that path must stay cheap.

The behaviour below is deliberately unchanged from main.py. The riskiest
fragment is the 401/403 retry in acquire(): it invalidates the token cache,
re-solves a MahaRERA CAPTCHA, and re-fetches only the categories MahaRERA
serves behind auth. That logic is MahaRERA-specific -- NO_AUTH_CATEGORIES is
a statement about this portal, not a general truth -- which is exactly why it
belongs on the adapter rather than in main().
"""

import json
import os
import re

import requests

import api_client
import config
import promoter_portfolio as promoter_portfolio_mod
import resolver
import session_auth
import token_cache

from .base import AcquisitionResult, StateResolutionError
from .maharashtra import PROFILE

_AUTH_SOURCE_LABELS = {
    "explicit": "explicit --token",
    "cached": "cached guest token",
    "fresh_browser": "fresh browser CAPTCHA solve",
    "none": "no session (public endpoints only)",
}

_REG_NO_RE = re.compile(PROFILE.reg_no_pattern, re.IGNORECASE)


def _describe(candidate) -> str:
    bits = [candidate.reg_no or candidate.project_id]
    for extra in (candidate.project_name, candidate.promoter_name, candidate.district):
        if extra:
            bits.append(extra)
    return " | ".join(bits)


def _extract_promoter_name(category_data: dict):
    """partners.promoterDetails.promoterName, falling back to the projects
    payload -- the same two paths main.py used."""
    partners = category_data.get("partners") or {}
    if isinstance(partners, dict):
        details = partners.get("promoterDetails")
        if isinstance(details, dict):
            name = (details.get("promoterName") or "").strip()
            if name:
                return name
    projects = category_data.get("projects") or {}
    if isinstance(projects, dict):
        name = (projects.get("promoterName") or "").strip()
        if name:
            return name
    return None


def search_promoter_projects(name, reporter=None):
    """Projects on MahaRERA registered under a promoter matching `name`.

    Wraps the existing Promoters-tab search. This is the one state whose
    promoter search needs a headless BROWSER rather than an HTTP call, so a
    sweep across many entities is materially slower here than elsewhere --
    the sweep bounds its fan-out for that reason.
    """
    import resolver

    candidates = resolver.search_promoters(name, headless=True)
    return [
        {"reg_no": c.reg_no, "project_name": c.project_name,
         "promoter_name": c.promoter_name, "project_id": c.project_id}
        for c in candidates
    ]


class _NullReporter:
    """A reporter for callers that have none. The sweep runs across many
    entities and states; per-request chatter from each would bury the
    result."""

    def info(self, *a, **k): pass
    def warn(self, *a, **k): pass
    def ok(self, *a, **k): pass
    def choose(self, *a, **k): return None


def _complaint_count(complaints):
    """How many complaints the complaints payload records, or None.

    None and 0 must stay distinguishable: one means the category could not
    be read, the other that MahaRERA published none. Returning 0 for an
    unread category is the false clean record this pipeline keeps guarding
    against."""
    if not isinstance(complaints, dict):
        return None
    details = complaints.get("complaintDetails")
    if details is None:
        # The key is present and null on a project with no complaints --
        # confirmed on a real capture -- so this IS a published zero, not a
        # failure to read. A missing payload never reaches here: the caller
        # passes None only when the fetch itself failed.
        return 0
    if isinstance(details, list):
        return len(details)
    return None


def fetch_project_summary(project_ref, reporter=None):
    """The diligence-relevant fields of ONE MahaRERA project.

    The sweep alone only proves a project EXISTS. This opens it, which is
    where what a reader actually needs lives: how much of it is sold,
    whether the registration has lapsed, its complaint count, and -- when a
    session is already to hand -- its promoter of record.

    IT WILL NEVER SOLVE A CAPTCHA, AND THAT SHAPES WHAT IT CAN RETURN.
    MahaRERA serves only `projects` and `complaints` without a session
    (config.NO_AUTH_CATEGORIES); everything else needs a guest token minted
    by a human solving a CAPTCHA in a real browser. A sweep can touch a
    dozen projects, so minting one per project would demand a dozen human
    solves to answer a background question -- unacceptable, and it would
    make the sweep unrunnable unattended. So this uses a token only if one
    is ALREADY cached from this run's own acquire (free, no solve) and
    otherwise reports the auth-gated fields as unread rather than absent.

    THE PROMOTER NAME IS THE FIELD THAT SUFFERS. It lives on `partners`,
    which is auth-gated -- the no-auth `projects` payload carries
    `promoterName: null`, confirmed on a real capture. group_sweep
    .enrich_projects confirms or refutes a candidate by comparing that name
    against the entity that matched, so without a cached token a MahaRERA
    hit stays UNCONFIRMED. `promoter_name_source` says which happened, so
    "unconfirmed" is never mistaken for "refuted".

    Never raises: one unreachable project must not sink a sweep.
    """
    reporter = reporter or _NullReporter()
    if not project_ref:
        return {"opened": False, "note": "No MahaRERA project id was carried on this row."}

    def _category(name, token, session):
        try:
            return api_client.fetch_category(name, str(project_ref), session, token), None
        except api_client.CategoryFetchError as e:
            return None, e

    with requests.Session() as session:
        projects, error = _category("projects", None, session)
        if projects is None:
            return {"opened": False,
                    "note": (f"This project's MahaRERA record could not be read "
                             f"({error}).")}

        complaints, complaints_error = _category("complaints", None, session)

        # Opportunistic only. load_valid() returns a token only when one is
        # already cached and unexpired -- it never mints one, so this cannot
        # block on a human.
        token = token_cache.load_valid()
        partners = appeals = None
        if token:
            partners, _ = _category("partners", token, session)
            appeals, _ = _category("appeals", token, session)

    if not isinstance(projects, dict):
        return {"opened": False,
                "note": "MahaRERA returned no general-details payload for this project."}

    promoter_name = ""
    promoter_name_source = None
    if partners is not None:
        promoter_name = _extract_promoter_name({"partners": partners, "projects": projects}) or ""
        promoter_name_source = "partners record (cached session)" if promoter_name else None

    notes = []
    if not promoter_name:
        notes.append(
            "This project's promoter of record could not be read: MahaRERA publishes it only on "
            "the partners record, which sits behind its CAPTCHA-gated session, and no session was "
            "already cached. The project is therefore neither confirmed nor refuted as this "
            "group's -- it must not be read as either."
        )
    if complaints is None:
        notes.append(
            f"This project's MahaRERA complaint category could not be read "
            f"({complaints_error}), so its complaint count is UNKNOWN. It must not be read as zero."
        )
    if projects.get("isProjectLapsed"):
        notes.append("MahaRERA records this project's registration as LAPSED.")

    total_units = projects.get("totalNumberOfUnits")
    sold_units = projects.get("totalNumberOfSoldUnits")
    return {
        "opened": True,
        "promoter_name": promoter_name,
        "promoter_name_source": promoter_name_source,
        # MahaRERA's own promoter key, and the one join this pipeline has
        # that is not a name: it is stable across a promoter's projects, so
        # two projects sharing it are the same promoter on the authority's
        # own records. See docs/PAN_INDIA_PROGRESS.md's join-key table.
        "user_profile_id": projects.get("userProfileId"),
        "project_name": projects.get("projectName") or "",
        "reg_no": projects.get("projectRegistartionNo") or "",
        "status": projects.get("projectCurrentStatus") or projects.get("projectStatusName") or "",
        "project_type": projects.get("projectTypeName") or "",
        "registration_lapsed": bool(projects.get("isProjectLapsed")),
        "rera_registration_date": projects.get("reraRegistrationDate"),
        "proposed_completion_date": projects.get("projectProposeComplitionDate"),
        "original_proposed_completion_date": projects.get("originalProjectProposeCompletionDate"),
        "units_total": total_units,
        "units_sold": sold_units,
        "total_complaints_count": _complaint_count(complaints),
        # None means the category was never asked for (no cached session);
        # an empty list means MahaRERA published none. Kept distinct for the
        # same reason the complaint count is.
        "total_appeals_count": (len(appeals) if isinstance(appeals, list) else None),
        "authenticated_fields_read": bool(token),
        "notes": notes,
    }


class MaharashtraAdapter:
    """StateAdapter for MahaRERA.

    Structural, not inherited -- see states/base.StateAdapter for why the
    contract is a Protocol rather than a base class."""

    profile = PROFILE

    def resolve_and_auth(self, query, ctx):
        """Stages 1-2, exposed separately so the --verify diagnostic can
        reach a project_id and token without paying for a full scrape it
        would then throw away."""
        project_id, detail_url, reg_no = self._resolve(query, ctx)
        token, auth_source = self._ensure_token(project_id, ctx)
        ctx.reporter.info(f"Auth source: {_AUTH_SOURCE_LABELS[auth_source]}")
        return project_id, detail_url, reg_no, token, auth_source

    def verify_endpoints(self, project_id, raw_dir, token):
        """The --verify developer diagnostic. Only meaningful for a state
        with CAP_CATEGORY_API, so it is not part of the StateAdapter
        Protocol -- callers check the capability first."""
        import discover
        return discover.verify_endpoints(project_id, raw_dir, token)

    # -- stage 1: resolve ---------------------------------------------------
    def _resolve(self, query, ctx):
        is_reg_no = bool(_REG_NO_RE.match(query))

        # --project-id exists for projects MahaRERA's own search can no
        # longer find; it skips the search entirely.
        #
        # Not gated on is_reg_no: app.py resolves interactively across
        # Streamlit reruns and then calls acquire() with the project_id it
        # already has, and a candidate whose reg_no was missing falls back
        # to its project_id as the key -- which is not P+11 digits. Requiring
        # the reg-no shape here would send that case back through a search
        # it has already done.
        if ctx.project_id_override:
            return (
                ctx.project_id_override,
                config.DETAIL_VIEW_URL_TEMPLATE.format(ctx.project_id_override),
                query.upper(),
            )

        if is_reg_no:
            try:
                project_id, detail_url = resolver.resolve_project_id(query, headless=not ctx.headed)
            except resolver.ProjectNotFoundError as e:
                raise StateResolutionError(str(e)) from e
            return project_id, detail_url, query.upper()

        ctx.reporter.info(f"Searching MahaRERA for a project named '{query}'...")
        candidates = resolver.search_projects(query, headless=not ctx.headed)
        if not candidates:
            raise StateResolutionError(f"No MahaRERA project found matching '{query}'.")

        if len(candidates) == 1:
            chosen = candidates[0]
        else:
            index = ctx.reporter.choose(
                f"{len(candidates)} projects match '{query}'. Which one?",
                [_describe(c) for c in candidates],
            )
            # None means "cannot ask" -- a non-TTY CLI run, or Streamlit,
            # which cannot block. Preserves main.py's old exit-code-2 path.
            if index is None:
                raise StateResolutionError(
                    f"{len(candidates)} projects match '{query}' and the choice could not be "
                    f"made non-interactively. Re-run with the exact registration number, "
                    f"or --project-id."
                )
            chosen = candidates[index]

        ctx.reporter.ok(f"Selected: {_describe(chosen)}")
        return chosen.project_id, chosen.detail_url, (chosen.reg_no or chosen.project_id)

    # -- stage 2: auth ------------------------------------------------------
    def _ensure_token(self, project_id, ctx):
        if ctx.explicit_token:
            return ctx.explicit_token, "explicit"
        if ctx.no_auto_auth:
            return None, "none"
        cached = token_cache.load_valid()
        if cached:
            return cached, "cached"
        try:
            token = session_auth.acquire_token_via_browser(
                project_id, timeout_seconds=ctx.captcha_timeout
            )
            return token, "fresh_browser"
        except (session_auth.CaptchaTimeoutError, session_auth.BrowserClosedError) as e:
            # Never fatal: two endpoints still work without a session, and a
            # degraded run beats no run.
            ctx.reporter.warn(f"Could not capture a session ({e}) -- continuing unauthenticated.")
            return None, "none"

    # -- stages 1-6 ---------------------------------------------------------
    def acquire(self, query, ctx):
        project_id, detail_url, reg_no, token, auth_source = self.resolve_and_auth(query, ctx)

        # The caller archives the previous run here -- after reg_no exists,
        # before anything is written under it. See AcquisitionContext
        # .on_resolved for why this is a callback rather than a step either
        # side of acquire().
        prior = ctx.prior or {}
        if ctx.on_resolved is not None:
            prior = ctx.on_resolved(reg_no) or {}

        project_out_dir = os.path.join(ctx.output_dir, reg_no)
        raw_dir = os.path.join(project_out_dir, "raw")
        documents_dir = os.path.join(project_out_dir, "documents")
        complaint_orders_dir = os.path.join(project_out_dir, "complaint_orders")
        os.makedirs(project_out_dir, exist_ok=True)

        prior_manifest = prior.get("manifest")
        prior_documents_dir = prior.get("documents_dir")
        prior_co_manifest = prior.get("complaint_orders_manifest")
        prior_co_dir = prior.get("complaint_orders_dir")

        # -- stage 4: the 9 categories --------------------------------------
        ctx.reporter.info("Fetching all categories...")
        errors = {}
        category_data = api_client.fetch_all_categories(project_id, raw_dir, token, errors_out=errors)

        gated_failed = [
            cat for cat, err in errors.items()
            if cat not in config.NO_AUTH_CATEGORIES and err.status_code in (401, 403)
        ]
        if gated_failed and auth_source != "none":
            ctx.reporter.warn(
                f"{len(gated_failed)} categor(ies) came back unauthorized despite having a "
                f"session -- refreshing it and retrying: {', '.join(gated_failed)}"
            )
            token_cache.invalidate()
            try:
                token = session_auth.acquire_token_via_browser(
                    project_id, timeout_seconds=ctx.captcha_timeout
                )
                auth_source = "fresh_browser"
                category_data.update(
                    api_client.fetch_all_categories(
                        project_id, raw_dir, token, categories=gated_failed
                    )
                )
            except (session_auth.CaptchaTimeoutError, session_auth.BrowserClosedError) as e:
                ctx.reporter.warn(
                    f"Retry session capture failed ({e}) -- leaving those categories as failed."
                )

        # -- stage 5: documents ---------------------------------------------
        ctx.reporter.info("Downloading documents...")
        documents_manifest = api_client.download_documents(
            category_data.get("documents"), documents_dir, token, project_id,
            prior_manifest=prior_manifest, prior_documents_dir=prior_documents_dir,
        )
        downloaded = sum(1 for d in documents_manifest if d["status"] == "downloaded")
        reused = sum(1 for d in documents_manifest if d["status"] == "reused")
        ctx.reporter.ok(
            f"{downloaded + reused}/{len(documents_manifest)} document(s) available "
            f"({downloaded} downloaded, {reused} reused from the previous run)."
        )

        ctx.reporter.info("Downloading complaint order PDFs...")
        try:
            complaint_orders_manifest = api_client.download_complaint_orders(
                category_data.get("complaints"), complaint_orders_dir, token, project_id,
                prior_manifest=prior_co_manifest, prior_documents_dir=prior_co_dir,
            )
            co_downloaded = sum(1 for d in complaint_orders_manifest if d["status"] == "downloaded")
            co_reused = sum(1 for d in complaint_orders_manifest if d["status"] == "reused")
            ctx.reporter.ok(
                f"{co_downloaded + co_reused}/{len(complaint_orders_manifest)} complaint order(s) "
                f"available ({co_downloaded} downloaded, {co_reused} reused from the previous run)."
            )
        except Exception as e:
            # Broad on purpose: a DMS outage or an unexpected complaints.json
            # shape must not take down an otherwise-good run.
            ctx.reporter.warn(f"Complaint order download failed ({e}) -- continuing without it.")
            complaint_orders_manifest = []

        with open(os.path.join(project_out_dir, "complaint_orders_manifest.json"), "w", encoding="utf-8") as f:
            json.dump(complaint_orders_manifest, f, indent=2, ensure_ascii=False)

        # -- stage 6: promoter portfolio ------------------------------------
        promoter_name = _extract_promoter_name(category_data)
        portfolio = None
        notes = []
        if promoter_name:
            ctx.reporter.info(f"Building promoter portfolio for '{promoter_name}'...")
            try:
                with requests.Session() as portfolio_session:
                    portfolio = promoter_portfolio_mod.build_promoter_portfolio(
                        promoter_name, portfolio_session, token, headless=not ctx.headed,
                        subject_project_partners_data=category_data.get("partners"),
                        subject_reg_no=reg_no, state_profile=PROFILE,
                    )
                promoter_dir = os.path.join(project_out_dir, "promoter")
                os.makedirs(promoter_dir, exist_ok=True)
                with open(os.path.join(promoter_dir, "portfolio.json"), "w", encoding="utf-8") as f:
                    json.dump(portfolio, f, indent=2, ensure_ascii=False)
                totals = portfolio["totals"]
                ctx.reporter.ok(
                    f"Promoter portfolio: {totals['total_projects']} project(s), "
                    f"{totals['total_complaints']} complaint(s), {totals['total_appeals']} appeal(s)."
                )
            except Exception as e:
                # Broad on purpose: opens its own Playwright browser and must
                # never be fatal to the main run.
                ctx.reporter.warn(f"Promoter portfolio build failed ({e}) -- continuing without it.")
        else:
            ctx.reporter.warn("Could not determine promoter name -- skipping promoter portfolio.")
            notes.append(
                "No promoter name could be read from this project's partner record, so no "
                "promoter portfolio was built for this run."
            )

        return AcquisitionResult(
            profile=PROFILE,
            reg_no=reg_no,
            project_id=project_id,
            detail_url=detail_url,
            category_data=category_data,
            documents_manifest=documents_manifest,
            documents_dir=documents_dir,
            complaint_orders_manifest=complaint_orders_manifest,
            complaint_orders_dir=complaint_orders_dir,
            promoter_name=promoter_name,
            promoter_portfolio=portfolio,
            auth_source=auth_source,
            notes=notes,
        )


ADAPTER = MaharashtraAdapter()

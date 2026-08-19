"""
Derives a promoter's corporate GROUP from the project entity.

The ask this answers: given "Pranami Neev Realty Limited" on a RERA record,
which other companies belong to the same group? That is a brand-name-shaped
question, and answering it naively is dangerous.

    Searching "PRANAMI" against the MCA mirrors returns, live:
        PRANAMI HYDRO POWER PRIVATE LIMITED   (Delhi, hydro power)
        PRANAMICS ENTERPRISES PRIVATE LIMITED (Karnataka)
        PRANAMI CASTINGS PRIVATE LIMITED      (Maharashtra, castings)
        PRANAMI FOUNDATION                    (Gujarat, non-profit)

    None of those is plausibly the same group as a Mumbai real-estate
    promoter. A name match is a COINCIDENCE OF WORDS, not a corporate
    relationship, and asserting one in a due-diligence document would be a
    fabrication -- the reader would reasonably take "group company" to mean
    a verified link.

So this module keeps the two ideas strictly apart:

    PROPOSE by name.  CONFIRM by a hard link.

A candidate is promoted to `confirmed` only when it shares a DIRECTOR, a
REGISTERED OFFICE, or a FILED subsidiary/associate/JV relationship with the
subject -- the three signals company_charter.find_group_companies_by_cin
already extracts from the MCA mirrors. Anything matching on name alone stays
in `proposed`, labelled as unconfirmed, and the Charter must present it that
way or not at all.

This is the same discipline find_group_companies_by_cin's own docstring
applies when it refuses to scrape a directory listing as a related-party set:
"scraping it as a related-party set would invent links rather than find
them."

Both real cases this repo has seen resolve correctly under that rule. The
CONSTELLA run's Speed Group cluster (Speed Ventures, Speed Housing Ventures,
Speed Vidyut Venture, Speed Echelon Infra) shares two founding partners and a
Banjara Hills address -- confirmed. The similarly named "Speed Infra Builders
LLP" shares neither, and the human analyst explicitly refused to merge it in.
This module makes that judgement reproducible.
"""

import re

import requests

import config

# Legal-form words. Stripped when working out the brand token, never treated
# as part of the brand itself -- otherwise "PRIVATE" would be a brand and
# match half the register.
_LEGAL_FORMS = {
    "PRIVATE", "PVT", "LIMITED", "LTD", "LLP", "LLC", "COMPANY", "CO",
    "INCORPORATED", "INC", "CORPORATION", "CORP", "AND", "&",
}

# First tokens too common to identify a group on their own. A search for
# these returns hundreds of unrelated companies, so the brand is taken from
# the next token instead and the ambiguity is recorded.
_HONORIFIC_PREFIXES = {
    "SHREE", "SHRI", "SRI", "SREE", "M", "MS", "M/S", "THE", "NEW", "SAI",
    "JAI", "OM", "SHIV", "GURU", "MAA", "MATA",
}

_MIN_BRAND_LENGTH = 4

_INSTAFINANCIALS_NAME_SEARCH = "https://www.instafinancials.com/ajax-caller.aspx/GetCompanyNames"


def normalise(name: str) -> str:
    """Upper-case, punctuation-stripped, single-spaced. Used for every
    comparison so "Pranami  Neev Realty Ltd." and "PRANAMI NEEV REALTY LTD"
    are the same string."""
    cleaned = re.sub(r"[^A-Za-z0-9& ]+", " ", (name or "").upper())
    return " ".join(cleaned.split())


def brand_token(name: str) -> tuple:
    """(token, note) -- the word most likely to identify the group.

    Returns (None, reason) when no usable brand can be extracted, which is a
    legitimate outcome: a promoter called "REALTY VENTURES PRIVATE LIMITED"
    has no distinguishing brand word, and searching "REALTY" would return
    noise rather than a group.
    """
    words = [w for w in normalise(name).split() if w not in _LEGAL_FORMS]
    if not words:
        return None, "no usable words in the promoter name"

    skipped = []
    for word in words:
        if word in _HONORIFIC_PREFIXES:
            skipped.append(word)
            continue
        if len(word) < _MIN_BRAND_LENGTH:
            skipped.append(word)
            continue
        note = None
        if skipped:
            note = (
                f"Brand read as {word!r}; the leading word(s) {', '.join(skipped)} are too "
                f"common to identify a group on their own."
            )
        return word, note

    return None, (
        f"No word in {name!r} is distinctive enough to search on -- every token is a "
        f"legal form or a common prefix."
    )


def search_companies_by_name(brand: str, timeout: int = None) -> list:
    """Companies whose name contains `brand`, from InstaFinancials'
    autocomplete (mode SCBN -- the name-search counterpart of the SCBC
    CIN lookup company_charter already uses).

    Returns [{"name", "cin", "status"}]. These are CANDIDATES ONLY. Nothing
    here has been shown to relate to anything; see the module docstring.
    """
    if not brand or not brand.strip():
        return []
    resp = requests.post(
        _INSTAFINANCIALS_NAME_SEARCH,
        json={"strSearch": brand.strip(), "mode": "SCBN"},
        headers=getattr(__import__("company_charter"), "_INSTAFINANCIALS_HEADERS"),
        timeout=timeout or config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()

    out = []
    for record in resp.json().get("d", []) or []:
        parts = record.split(";")
        if len(parts) < 7:
            continue
        out.append({"name": parts[1].strip(), "cin": parts[2].strip(), "status": parts[6].strip()})
    return out


# Hard links are not equally strong, and treating them as one class
# overstates a group. For the Pranami subject, 28 of 65 confirmed entities
# share ONLY a registered office -- and a registered-office service address
# in Mumbai routinely hosts dozens of unrelated companies. A shared director
# is a person sitting on two boards; a shared postcode is a landlord.
LINK_DECLARED = "declared"      # filed subsidiary / associate / JV
LINK_DIRECTOR = "director"      # a named person on both boards
LINK_ADDRESS_ONLY = "address-only"

_STRENGTH_ORDER = {LINK_DECLARED: 0, LINK_DIRECTOR: 1, LINK_ADDRESS_ONLY: 2}


def link_strength(basis: list) -> str:
    """Strongest signal among an entity's bases.

    Ordering is deliberate: a FILED relationship is the company's own
    declaration to the registrar, a SHARED DIRECTOR is a verifiable person
    on both boards, and a SHARED ADDRESS is neither -- it is consistent with
    a group, and equally consistent with a shared registered-office
    provider."""
    joined = " ".join(basis or []).lower()
    if "subsidiary" in joined or "associate" in joined or "jv" in joined:
        return LINK_DECLARED
    if "shared director" in joined:
        return LINK_DIRECTOR
    return LINK_ADDRESS_ONLY


def _hard_links(group_check: dict) -> dict:
    """CIN -> basis list, for entities the MCA mirrors tie to the subject by
    a concrete signal. This is the confirmation set."""
    links = {}
    for entry in (group_check or {}).get("companies") or []:
        cin = (entry.get("cin") or "").strip().upper()
        if cin:
            links[cin] = entry
    return links


def build_entity_graph(
    subject_name: str,
    subject_cin: str = None,
    group_check: dict = None,
    proposer=None,
) -> dict:
    """The group around `subject_name`, split into confirmed and proposed.

    `group_check` is company_charter.find_group_companies_by_cin's result --
    the hard-link set. `proposer` is the name-search callable, defaulting to
    the real one; injected so this is testable with no network, matching the
    pattern company_charter.run_finding_research(facts, researcher=None)
    already uses.

    Returns:
        {
          "subject": {"name", "cin"},
          "brand_token": str | None,
          "confirmed": [ {name, cin, basis[], shares_brand} ],
          "proposed":  [ {name, cin, status, why_unconfirmed} ],
          "limitations": [str],
          "notes": [str],
        }

    `confirmed` carries EVERY hard-linked entity, whether or not it shares
    the brand -- a group company trading under a different name is still a
    group company. `proposed` carries brand matches with no hard link, and
    every one of them says why it is unconfirmed.
    """
    proposer = proposer or search_companies_by_name
    subject_norm = normalise(subject_name)
    subject_cin = (subject_cin or "").strip().upper()

    brand, brand_note = brand_token(subject_name)
    linked = _hard_links(group_check)

    confirmed = []
    for cin, entry in linked.items():
        name = entry.get("name") or ""
        basis = list(entry.get("basis") or [])
        confirmed.append({
            "name": name,
            "cin": cin,
            "basis": basis,
            "link_strength": link_strength(basis),
            "shares_brand": bool(brand and brand in normalise(name).split()),
        })
    # Strongest link first, then brand match, then name -- so a reader
    # scanning the top of the list sees the best-evidenced entities.
    confirmed.sort(key=lambda e: (
        _STRENGTH_ORDER.get(e["link_strength"], 9), not e["shares_brand"], e["name"]
    ))

    limitations = []
    notes = []
    if brand_note:
        notes.append(brand_note)

    proposed = []
    if brand:
        try:
            candidates = proposer(brand)
        except Exception as e:
            candidates = []
            limitations.append(
                f"The brand-name search for {brand!r} could not run this pass ({e}), so no "
                f"name-matched candidates were considered. Only entities linked by a shared "
                f"director, registered office or filed relationship appear below."
            )
        for candidate in candidates:
            cin = (candidate.get("cin") or "").strip().upper()
            if cin and (cin == subject_cin or cin in linked):
                continue  # already the subject, or already confirmed
            if normalise(candidate.get("name")) == subject_norm:
                continue
            proposed.append({
                "name": candidate.get("name"),
                "cin": cin,
                "status": candidate.get("status"),
                "why_unconfirmed": (
                    f"Shares the brand word {brand!r} with the promoter, but the corporate "
                    f"registry shows no shared director, no shared registered office and no "
                    f"filed subsidiary/associate/JV relationship with it. A shared name is "
                    f"not a corporate relationship."
                ),
            })
    else:
        limitations.append(
            brand_note or f"No brand word could be extracted from {subject_name!r}."
        )

    limitations.append(
        "Entities are confirmed ONLY by a shared director, a shared registered office, or a "
        "filed subsidiary/associate/JV relationship. Name similarity alone proposes a "
        "candidate; it never confirms one."
    )
    address_only = [e for e in confirmed if e["link_strength"] == LINK_ADDRESS_ONLY]
    if address_only:
        limitations.append(
            f"{len(address_only)} of {len(confirmed)} confirmed entities are tied to the "
            f"promoter ONLY by a shared registered office. That is the weakest of the three "
            f"signals -- a registered-office service provider can host many unrelated "
            f"companies at one address -- and those entities should not be read as group "
            f"members on that basis alone."
        )
    if proposed:
        limitations.append(
            f"{len(proposed)} name-matched candidate(s) are listed as unconfirmed and must "
            f"not be presented as group companies without independent verification."
        )
    if group_check is not None and not group_check.get("found"):
        limitations.append(
            "The corporate-registry crosswalk did not run for this promoter, so NOTHING here "
            "could be confirmed -- the absence of confirmed entities is not evidence the "
            "promoter has none."
        )

    undisclosed = (group_check or {}).get("undisclosed_relationship_counts") or {}
    for relationship, count in undisclosed.items():
        if count:
            notes.append(
                f"{count} {relationship} relationship(s) exist on the registry record but "
                f"their counterparties' identities are paywalled, so they are counted here "
                f"rather than named."
            )

    return {
        "subject": {"name": subject_name, "cin": subject_cin or None},
        "brand_token": brand,
        "confirmed": confirmed,
        "proposed": proposed,
        "limitations": limitations,
        "notes": notes,
    }


def entity_names_for_sweep(
    graph: dict,
    include_proposed: bool = False,
    min_strength: str = LINK_DIRECTOR,
) -> list:
    """The entity names a group-wide sweep (RERA portfolio, litigation, GST)
    should iterate.

    Defaults to the subject plus confirmed entities linked by a DIRECTOR or
    a FILED relationship -- address-only links are excluded, because folding
    a co-tenant's litigation into this promoter's track record would be a
    serious misattribution.

    `include_proposed` is off by default for the same reason, only more so:
    a name match is not a relationship at all.
    """
    ceiling = _STRENGTH_ORDER.get(min_strength, 1)
    names = [graph["subject"]["name"]]
    names += [
        e["name"] for e in graph.get("confirmed") or []
        if e.get("name") and _STRENGTH_ORDER.get(e.get("link_strength"), 9) <= ceiling
    ]
    if include_proposed:
        names += [e["name"] for e in graph.get("proposed") or [] if e.get("name")]

    seen, out = set(), []
    for name in names:
        key = normalise(name)
        if key and key not in seen:
            seen.add(key)
            out.append(name)
    return out

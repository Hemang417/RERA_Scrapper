"""
Generic research-preparation layer for the Company Charter pipeline
(charter_report.py). Turns a facts.json into:

  1. a director roster (current + any past director material enough to
     warrant their own subsection under The Promoters), computed
     deterministically from data already in facts.json -- no web research
     needed for this part;
  2. ready-to-send research-agent PROMPTS for each director and for the
     company/group background, parameterised entirely from that facts.json
     (no project-specific hardcoding anywhere in this module -- verified by
     the same "grep for a project name" check charter_report.py itself was
     held to);
  3. a parser that turns an agent's structured JSON reply back into the
     exact shape charter_report.build_charter_report()'s `research` dict
     expects, and an assembler that merges everything together.

WHY THIS SPLIT: spawning a research agent is a tool call only an LLM
orchestrator can make (Claude Code, or whatever hosts it) -- it cannot be
done from inside a plain Python module. Everything that CAN be deterministic
lives here instead, so the only judgment a future run still needs is "does
this specific search result belong in the document" -- and even that is now
answered by the agent itself (via the requested JSON shape and the find-first
instruction baked into every prompt), not re-transcribed by hand afterwards
the way this session's first pass did it.

RECIPE for a future run (interactive or hosted), see run_charter_pipeline.py
for the fully worked version:
  1. roster = build_roster(facts)
  2. prompts = build_all_prompts(facts, roster)
  3. For each entry in prompts, spawn one Agent tool call with that prompt
     text (in parallel -- they are independent). Collect each agent's final
     text response.
  4. parsed = {key: parse_agent_json(text) for key, text in responses.items()}
  5. research = assemble_research(facts, roster, parsed)
  6. Call charter_report.build_charter_report(...) once per doc_variant.
"""

from __future__ import annotations

import json
import re

import company_charter as cc

# ---------------------------------------------------------------------------
# 1. Director roster -- fully deterministic, from facts.json alone.
# ---------------------------------------------------------------------------

# A past director needs at least this many of their own related-entity links
# before they earn a subsection under The Promoters (see the spec's "material
# footprint" judgment call). Configurable per engagement if a different bar
# is wanted; this default matches the threshold that put Vijay Kumar Mohta in
# and left no one else in from the same past-directors list.
DEFAULT_PAST_DIRECTOR_LINK_THRESHOLD = 5

# How many of a person's own linked entities to name in their research
# prompt, so the agent can use them to disambiguate a common name against
# same-name unrelated people -- not meant to be exhaustive, just enough
# context to anchor identity.
_SAMPLE_LINKED_ENTITIES = 8


def _linked_entity_names(person_name: str, companies: list) -> list:
    person_name = (person_name or "").lower()
    return [
        c.get("name") for c in companies
        if any(person_name in (basis or "").lower() for basis in (c.get("basis") or []))
        and c.get("name")
    ]


def _disclosed(value: str | None) -> str | None:
    """MCA-mirror registries (ZaubaCorp/Tofler) use a bare "-" as their own
    placeholder for a blank field, same meaning as None/"" -- normalise all
    three to None so callers can apply one real fallback instead of a raw
    "-" leaking into rendered prose."""
    value = (value or "").strip()
    return value if value and value != "-" else None


def _director_entry(raw: dict, companies: list, cessation: str | None = None) -> dict:
    name = cc._normalise_entity_name(raw.get("Director Name") or "")
    linked = _linked_entity_names(name, companies)
    return {
        "name": name,
        "din": _disclosed(raw.get("DIN")) or "not disclosed",
        "appointment_date": _disclosed(raw.get("Appointment Date")) or "not disclosed",
        "designation": raw.get("Designation") or "",
        # Deliberately NOT normalised through _disclosed here (unlike din/
        # appointment_date above): build_director_prompt's role_note branches
        # past-vs-current on this field's truthiness, and a past director
        # whose Cessation happens to be the registry's own "-" placeholder is
        # still a past director -- collapsing "-" to None would misclassify
        # them as current. Display-site code cleans "-" up for prose instead.
        "cessation_date": cessation,
        "other_directorships_count": len(linked),
        "sample_linked_entities": linked[:_SAMPLE_LINKED_ENTITIES],
    }


def build_roster(facts: dict, past_director_link_threshold: int = DEFAULT_PAST_DIRECTOR_LINK_THRESHOLD) -> dict:
    """Returns {"current": [...], "material_past": [...]}, each a list of
    director-entry dicts (see _director_entry). A past director already
    covered by a current designation is never double-listed."""
    profile = facts.get("company_profile_check") or {}
    group = facts.get("group_companies_check") or {}
    companies = group.get("companies") or []

    current_raw = profile.get("current_directors") or []
    current = [_director_entry(d, companies) for d in current_raw]
    current_names = {d["name"] for d in current}

    past_raw = profile.get("past_directors") or []
    seen_past_names = set()
    material_past = []
    for d in past_raw:
        name = cc._normalise_entity_name(d.get("Director Name") or "")
        if name in current_names or name in seen_past_names:
            continue
        seen_past_names.add(name)
        entry = _director_entry(d, companies, cessation=d.get("Cessation"))
        if entry["other_directorships_count"] >= past_director_link_threshold:
            material_past.append(entry)
    return {"current": current, "material_past": material_past}


# ---------------------------------------------------------------------------
# 2. Prompt generation -- parameterised from facts.json, no hardcoded names.
# ---------------------------------------------------------------------------

_DIRECTOR_JSON_SCHEMA = """{
  "education": [{"text": "...", "source": "https://..."}],
  "career": [{"text": "...", "source": "https://..."}],
  "positive": [{"text": "...", "source": "https://..."}],
  "adverse": [{"text": "...", "source": "https://..."}],
  "identity_notes": [{"text": "...", "source": "https://..." }]
}"""

_GROUP_JSON_SCHEMA = """{
  "track_record_corroboration": [{"text": "...", "source": "https://..."}],
  "corporate_litigation": [{"text": "...", "source": "https://..."}],
  "collateral_discrepancies": [{"text": "...", "source": "https://..."}],
  "past_director_findings": {
    "<Past Director Name>": [{"text": "...", "source": "https://..."}]
  }
}"""

_JSON_OUTPUT_INSTRUCTIONS = """
IMPORTANT RULES:
- Cite every factual claim with the URL you found it at, inside the JSON "source" field.
- Do NOT fabricate a plausible-sounding finding. If a category turns up nothing after a genuine
  search, leave that array EMPTY -- do not add a sentence saying nothing was found; an empty array
  IS the "nothing found" signal the document-building code expects.
- Only "adverse" (and past-director findings) should contain a genuine, notable, unresolved,
  or negative item. A clean result (no litigation, no adverse press) is an EMPTY array, never a
  sentence confirming the absence.
- Do not guess at identity for a common name. If a search result cannot be confidently tied to
  this specific individual, put a note about the ambiguity in "identity_notes" rather than silently
  including or excluding it.

OUTPUT FORMAT: your FINAL message must end with exactly one fenced code block labeled json,
containing ONLY a single JSON object matching this exact shape (empty arrays where nothing was
found), and nothing after that code block:

{schema}
""".strip()


def build_director_prompt(director: dict, company_name: str, project_context: str, is_partnership: bool = False) -> str:
    """A self-contained research prompt for one director/partner. Every
    fact injected below comes from `director` (itself derived from
    facts.json by build_roster) or the two plain strings passed in -- never
    a hardcoded name, company, or project.

    `is_partnership`: an LLP or a plain (unincorporated) Partnership Firm
    has partners, not directors -- confirmed live as a real document-
    wording bug first for an LLP promoter (Trimity Realty LLP), then again
    for a bare partnership firm with no CIN/LLPIN at all (IRA Homes),
    where the generic "director/partner" hedge below read as sloppy once
    the reader already knew the entity type. Resolved to the one correct
    noun instead."""
    role_noun = "partner" if is_partnership else "director"
    linked = director.get("sample_linked_entities") or []
    linked_note = (
        f"Via registry cross-reference we already know they hold {director['other_directorships_count']} "
        f"other {role_noun}ship(s), including: {', '.join(linked)}. Do not re-research this list; use it only "
        f"to help confirm you have the right individual."
        if linked else
        f"No other {role_noun}ships were found for them via registry cross-reference."
    )
    role_note = (
        f"a past {role_noun} (designation: {director.get('designation') or 'not disclosed'}, "
        f"ceased {_disclosed(director.get('cessation_date')) or 'an unknown date'})"
        if director.get("cessation_date") else
        f"a current {role_noun} (designation: {director.get('designation') or 'not disclosed'}, "
        f"appointed {director['appointment_date']})"
    )

    has_din = director["din"] not in ("not disclosed", "", None)
    if has_din:
        search_strategy = (
            f'SEARCH STRATEGY: Start with at least one search combining the FULL name and the DIN together (e.g. "{director["name"]} {director["din"]}" or "{director["name"]} DIN {director["din"]}") '
            f"to pull up registry-mirror sources (ZaubaCorp, Tofler, MCA, MyCorporateInfo, IndiaFilings) and firmly anchor this person's identity before researching further -- always use the full name given above, "
            f"never a shortened or partial form of it. For the biographical/news categories below, note that a DIN is an MCA-specific identifier that will not appear in general news or press coverage, so those searches "
            f'should use the full name plus a qualifier (their company, city, or known role, e.g. "{director["name"]} {company_name}" or "{director["name"]} Thane real estate") -- then use the DIN-anchored registry '
            f"facts you already found to judge whether a same-name result is genuinely this person before including it."
        )
    else:
        # No DIN exists to anchor on -- confirmed live for a plain
        # (unincorporated) Partnership Firm's partners, who are never
        # MCA-registered and hold no DIN at all (IRA Homes). Instructing
        # the agent to search "name + DIN" when DIN is "not disclosed"
        # would waste a search on the literal words "not disclosed".
        search_strategy = (
            f'SEARCH STRATEGY: No DIN or other registry identifier is available for this person (a common situation for a plain partnership firm\'s partners, who are not MCA-registered). '
            f'Search using the full name given above plus a qualifier (their company, city, or known role, e.g. "{director["name"]} {company_name}" or "{director["name"]} Thane real estate") -- '
            f"never a shortened or partial form of the name -- and rely on those qualifiers, not an identifier, to judge whether a same-name result is genuinely this person."
        )

    return f"""You are doing legitimate corporate due-diligence research (KYC/background screening) for a real-estate due-diligence report, as part of an authorized business review. Research the individual below using web search. This is standard public-record/press research on a company {role_noun}, not surveillance of a private person.

SUBJECT: {director['name']}, DIN/ID {director['din']}. They are {role_note} of {company_name}. {project_context}

{linked_note}

{search_strategy}

RESEARCH THESE CATEGORIES (search the web for each; do not rely on memory):
1. Education and professional qualifications.
2. Career history: prior employers/roles, how they came to their current position.
3. News coverage: awards, interviews, publicised achievements, business press mentions.
4. Adverse coverage: litigation, regulatory action, disqualification, disputes, insolvency
   proceedings, or negative press naming this specific individual.
5. Any indication of a same-name/different-identifier collision. If a search result cannot be
   confidently tied to THIS person, flag it as an unconfirmed same-name hit rather than silently
   including or excluding it.

{_JSON_OUTPUT_INSTRUCTIONS.format(schema=_DIRECTOR_JSON_SCHEMA)}

Keep the JSON's combined text under 500 words. This is raw research output to fold into a
due-diligence document programmatically -- the JSON is the deliverable, not a prose report."""


def build_group_prompt(facts: dict, company_name: str, material_past_directors: list) -> str:
    """A self-contained research prompt for company/group-level background
    and litigation, plus (folded into the same call) any material past
    director's litigation exposure -- mirroring how this was done by hand
    in the first engagement this pipeline was built against."""
    core = facts.get("rera_core_fields") or {}
    profile = facts.get("company_profile_check") or {}
    corp = facts.get("corporate_identity") or {}
    track = facts.get("developer_track_record") or {}
    role_noun = "partner" if cc._is_partnership(facts) else "director"

    # Falls back to corporate_identity's own model-authored fields when
    # company_profile_check lacks the usual ZaubaCorp-standard ones --
    # confirmed live as a real gap for a promoter with no CIN/LLPIN at all
    # (IRA Homes, an unincorporated partnership firm), where the group
    # prompt's own context previously went blank ("CIN/registration None,
    # registered office not disclosed") despite the Charter already having
    # a real registered-office address on file from the Form B declaration.
    cin_fallback = profile.get("cin") or (corp.get("cin_llpin") or {}).get("value") or "not disclosed"
    office_fallback = profile.get("registered_address") or (corp.get("registered_office_main") or {}).get("value") or "not disclosed"
    incorporation_fallback = profile.get("incorporation_date") or "not confirmed as a specific date (see the Charter's own Organization Type field for what is known)"

    context_lines = [f"Company: {company_name}, CIN/registration {cin_fallback}, "
                      f"incorporated {incorporation_fallback}, "
                      f"registered office {office_fallback}."]
    if core.get("project_name"):
        context_lines.append(f"The project this Charter covers: {core['project_name']} ({core.get('registration_number', '')}).")
    if track.get("years_in_industry_basis"):
        context_lines.append(f"Self-reported track record note already on file (verify independently, do not just repeat it): {track['years_in_industry_basis']}")

    past_director_block = ""
    if material_past_directors:
        lines = []
        for d in material_past_directors:
            linked = ", ".join((d.get("sample_linked_entities") or [])[:5])
            has_din = d["din"] not in ("not disclosed", "", None)
            identity_hint = (
                f'When searching for adverse coverage on this person, run at least one search combining their FULL name and DIN together (e.g. "{d["name"]} {d["din"]}") to anchor identity before searching by name alone.'
                if has_din else
                f'No DIN or other registry identifier is available for this person; search using their full name plus a company/location qualifier to anchor identity instead.'
            )
            lines.append(
                f"- {d['name']} (DIN/ID {d['din']}), a past {role_noun} who ceased {d.get('cessation_date', 'an unknown date')}, "
                f"links to {d['other_directorships_count']} other entities including: {linked}. {identity_hint}"
            )
        past_director_block = (
            "\n\nALSO research adverse coverage (litigation, regulatory action, insolvency, disqualification) for "
            f"each of these past {role_noun}s specifically, given their large related-entity footprint:\n" + "\n".join(lines)
        )

    return f"""You are doing legitimate corporate due-diligence research for a real-estate due-diligence report, as part of an authorized business review. Research the topics below using web search.

CONTEXT:
{chr(10).join(context_lines)}
{past_director_block}

RESEARCH THESE TOPICS (search the web for each; do not rely on memory):
1. Independent (non-self-reported, non-listing-aggregator) press coverage of the company/group's
   history, scale, and reputation. Does independent coverage corroborate any self-reported scale
   claim (projects delivered, area developed), or is there no independent trace of it?
2. Any named investment partnership, joint venture, or funding announcement involving this company
   or its group -- find the actual press release or news coverage, not just a repeated headline
   figure across syndicated sources.
3. Corporate-level litigation or regulatory action against this company or its named group entities
   specifically -- NCLT filings, High Court cases, consumer forum complaints, RERA orders/penalties.
   (Skip re-checking insolvency register / credit rating / this project's own RERA complaint record
   if those are already covered elsewhere in the Charter -- focus on anything beyond them.)
4. Any discrepancy between this project's own registration number/identifiers (as given in the
   context above) and how third-party aggregator sites list the same project.

{_JSON_OUTPUT_INSTRUCTIONS.format(schema=_GROUP_JSON_SCHEMA)}

Keep the JSON's combined text under 700 words. This is raw research output to fold into a
due-diligence document programmatically -- the JSON is the deliverable, not a prose report."""


# ---------------------------------------------------------------------------
# 3. Parsing an agent's reply, and assembling the final research dict.
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def parse_agent_json(raw_text: str) -> dict:
    """Extracts the LAST ```json ... ``` fenced block from an agent's reply
    and parses it. Returns {} (never raises) if no valid block is found, so
    one malformed agent reply degrades to "nothing found" for that person
    rather than crashing the whole assembly step -- the same fail-open
    posture as every other gap in this pipeline."""
    matches = _JSON_BLOCK_RE.findall(raw_text or "")
    if not matches:
        return {}
    try:
        return json.loads(matches[-1])
    except (ValueError, TypeError):
        return {}


def _as_tuples(items) -> list:
    """Converts the agent JSON's [{"text":, "source":}, ...] shape into the
    (text, source) tuples charter_report.py's builder methods expect."""
    out = []
    for item in items or []:
        if isinstance(item, dict):
            text = (item.get("text") or "").strip()
            if text:
                out.append((text, item.get("source") or None))
        elif item:
            out.append((str(item), None))
    return out


def assemble_research(roster: dict, director_json_by_name: dict, group_json: dict, is_partnership: bool = False) -> dict:
    """Merges the deterministic roster with each agent's parsed JSON into
    the exact `research` dict shape charter_report.build_charter_report()
    expects. Mechanical merge only -- every judgment call about what counts
    as a genuine finding was already made by the research agent itself, per
    the find-first instruction baked into its prompt."""
    role_noun = "partner" if is_partnership else "director"
    directors = {}
    for d in roster.get("current") or []:
        parsed = director_json_by_name.get(d["name"], {})
        directors[d["name"]] = {
            "other_directorships_count": d["other_directorships_count"],
            "education": _as_tuples(parsed.get("education")),
            "career": _as_tuples(parsed.get("career")),
            "positive": _as_tuples(parsed.get("positive")),
            "adverse": _as_tuples(parsed.get("adverse")),
            "identity_notes": _as_tuples(parsed.get("identity_notes")),
        }

    research = {
        "directors": directors,
        "group": {
            "corporate_litigation": _as_tuples(group_json.get("corporate_litigation")),
            "track_record_corroboration": _as_tuples(group_json.get("track_record_corroboration")),
            "collateral_discrepancies": _as_tuples(group_json.get("collateral_discrepancies")),
        },
        "additional_next_steps": [],
    }

    material_past = roster.get("material_past") or []
    if material_past:
        # Only one past-director subsection is rendered by charter_report.py
        # today (see its _past_director_subsection). If more than one past
        # director clears the materiality bar, the most-linked one gets the
        # subsection and the rest are noted as a next step for a future,
        # deeper pass rather than silently dropped.
        primary = max(material_past, key=lambda d: d["other_directorships_count"])
        findings = _as_tuples((group_json.get("past_director_findings") or {}).get(primary["name"]))
        research["past_director_name"] = primary["name"]
        # Restates the name in the second sentence rather than using a
        # pronoun -- a prior version used "They", which read as ambiguous
        # once this sentence sat right after a heading of the same name.
        # appointment_date/cessation_date use their own "on an undisclosed/
        # unknown date" phrasing here rather than the shared "not disclosed"
        # field value directly, since "appointed on not disclosed" doesn't
        # parse as a date phrase the way "DIN/ID not disclosed" does as a
        # noun phrase elsewhere.
        appointment_display = primary["appointment_date"] if primary["appointment_date"] != "not disclosed" else "an undisclosed date"
        cessation_display = _disclosed(primary.get("cessation_date")) or "an unknown date"
        research["past_director_summary"] = (
            f"A past {role_noun}, {primary['name']}, was appointed on "
            f"{appointment_display} and ceased on {cessation_display}. "
            f"{primary['name']} separately links to {primary['other_directorships_count']} "
            f"other companies via shared {role_noun}ship; see the Annexure for the full related-entity mapping."
        )
        # The past director's OWN research agent response (education/career/
        # positive/adverse/identity_notes) was being silently dropped here --
        # only past_director_findings (the GROUP agent's brief note) ever
        # reached the document, even though a full per-person research call
        # was made and paid for. Confirmed a real gap on Pranami Bliss's
        # Vijay Kumar Mohta subsection. charter_report.py's
        # _past_director_subsection now renders this the same way a current
        # director's bio renders.
        past_parsed = director_json_by_name.get(primary["name"], {})
        research["past_director_bio"] = {
            "education": _as_tuples(past_parsed.get("education")),
            "career": _as_tuples(past_parsed.get("career")),
            "positive": _as_tuples(past_parsed.get("positive")),
            "adverse": _as_tuples(past_parsed.get("adverse")),
            "identity_notes": _as_tuples(past_parsed.get("identity_notes")),
        }
        research["past_director_findings"] = findings
        for other in material_past:
            if other is not primary:
                research["additional_next_steps"].append(
                    f"{other['name']} also clears the material-footprint bar ({other['other_directorships_count']} "
                    f"linked entities) but was not given its own subsection this pass; consider one on a deeper review."
                )

    return research

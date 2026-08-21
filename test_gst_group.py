"""
Guards on checking GST compliance across the GROUP rather than the subject.

THE FAILURE THIS FILE EXISTS FOR is the one this codebase keeps meeting in
new places: a check that could not run being read downstream as a check
that found nothing. It is sharper here than anywhere else in the pipeline.

GST is keyed on PAN. The entity graph is keyed on CIN and name, and no
public MCA source publishes a company's PAN -- so for a typical group, a
handful of entities can be reached and dozens cannot. A section that listed
only findings would let a reader conclude the group files cleanly when in
truth two of sixty-five were looked at. Hence: every entity appears with a
status, unchecked ones are named, and the coverage sentence leads with the
denominator.

The second cost is human. Each entity checked needs at least two fresh
CAPTCHA solves -- one for the PAN search, one per GSTIN found under it (see
gst_intake.run_intake). So the sweep is opt-in, bounded, and anything past
the bound is reported rather than dropped.

Everything here is offline: `intake=` is the seam, and nothing opens a
browser.

Run directly: python test_gst_group.py
"""

import gst_group as gg

_GRAPH = {
    "subject": {"name": "Pranami Neev Realty Limited", "cin": "U70109MH2022PLC385473"},
    "confirmed": [
        {"name": "Pranami Builders Pvt Ltd", "cin": "U51909JH1995PTC013805"},
        {"name": "Nysa Marine & Offshore Pvt Ltd", "cin": "U61100MH2011PTC000001"},
        {"name": "Bihar Carbons Ltd", "cin": "U23109BR1990PLC000001"},
    ],
}

# A PAN card read off a RERA document library, and a PAN named outright in
# another authority's filing -- the two provenances that actually occurred.
_IDENTITY = {"status": "verified", "promoter_name": "Pranami Neev Realty Limited",
             "pan": "AANCP0234D"}
_RERA_PANS = {"PRANAMI BUILDERS PVT.LTD": "AAECP0371L"}


def _fake_intake(pan, entity_name):
    return {"gstin": "27" + pan + "1ZO", "period_count": 76,
            "summary": {"late_filings": 3, "on_time_rate": 0.96}}


def test_an_entity_with_no_pan_is_unchecked_never_compliant():
    """THE CENTRAL GUARD. Most of a group cannot be reached at all, because
    GST needs a PAN and no MCA mirror publishes one. Those entities must
    appear in the result with a status -- dropping them would leave a table
    that looks complete, and silence about an entity reads as nothing to
    report."""
    result = gg.sweep(_GRAPH, gg.known_pans(identity_result=_IDENTITY, rera_pans=_RERA_PANS),
                      intake=_fake_intake)
    names = [row["name"] for row in result["entities"]]
    assert "Nysa Marine & Offshore Pvt Ltd" in names, names
    assert "Bihar Carbons Ltd" in names, names
    unreachable = [r for r in result["entities"] if r["name"] == "Bihar Carbons Ltd"][0]
    assert unreachable["status"] == gg.STATUS_NO_PAN, unreachable
    assert "summary" not in unreachable, "an unchecked entity carried a filing summary"
    assert result["checked"] == 2 and result["total"] == 4, result
    assert result["without_pan"] == 2, result
    print("test_an_entity_with_no_pan_is_unchecked_never_compliant: PASS")


def test_the_coverage_sentence_leads_with_the_denominator():
    """A reader who sees only findings reads silence as compliance. Across a
    group this thinly covered that would be wrong nearly every time, so the
    sentence has to say 2 of 4 before it says anything else, and has to
    state outright that the rest are not being called compliant."""
    result = gg.sweep(_GRAPH, gg.known_pans(identity_result=_IDENTITY, rera_pans=_RERA_PANS),
                      intake=_fake_intake)
    sentence = gg.coverage_sentence(result)
    assert "2 of 4" in sentence, sentence
    assert "not reported as compliant or non-compliant" in sentence, sentence
    print("test_the_coverage_sentence_leads_with_the_denominator: PASS")


def test_an_unverified_pan_card_never_becomes_a_lookup_key():
    """promoter_identity emits an explicit status precisely so callers stop
    sniffing prose. An unverified_candidate is a PAN the OCR could not tie
    to the promoter's name: using it would spend a human's CAPTCHA solve on
    a lookup for possibly the wrong company, and then attribute the result
    to this one."""
    for status in ("unverified_candidate", "unreadable", "ocr_unavailable", "no_card"):
        pans = gg.known_pans(identity_result={"status": status,
                                              "promoter_name": "Pranami Neev Realty Limited",
                                              "pan": "AANCP0234D"})
        assert pans == {}, (status, pans)
    verified = gg.known_pans(identity_result=_IDENTITY)
    assert len(verified) == 1, verified
    print("test_an_unverified_pan_card_never_becomes_a_lookup_key: PASS")


def test_every_pan_carries_its_provenance_and_the_strongest_wins():
    """A PAN off a filed card and a PAN typed in by hand are not equally
    good evidence, and the Charter shows the source, so it has to be real.
    Registration order is strongest-first and later sources must not
    overwrite earlier ones."""
    pans = gg.known_pans(
        identity_result=_IDENTITY,
        rera_pans=_RERA_PANS,
        supplied={"Pranami Neev Realty Limited": "ZZZZZ9999Z"},
    )
    subject = pans["PRANAMI NEEV REALTY LIMITED"]
    assert subject["pan"] == "AANCP0234D", subject
    assert subject["source"] == gg.PAN_SOURCE_FILED_CARD, subject
    assert pans["PRANAMI BUILDERS PVT LTD"]["source"] == gg.PAN_SOURCE_RERA_FILING, pans
    print("test_every_pan_carries_its_provenance_and_the_strongest_wins: PASS")


def test_a_pan_is_derived_from_a_gstin_arithmetically_never_guessed():
    """Characters 3-12 of a GSTIN are the PAN. That is arithmetic, so it is
    allowed; nothing else about a company may produce one."""
    pans = gg.known_pans(gstins={"Pranami Neev Realty Limited": "27AANCP0234D1ZO"})
    entry = pans["PRANAMI NEEV REALTY LIMITED"]
    assert entry["pan"] == "AANCP0234D", entry
    assert entry["source"] == gg.PAN_SOURCE_FROM_GSTIN, entry
    # A malformed GSTIN yields nothing rather than a mangled PAN.
    assert gg.known_pans(gstins={"X Ltd": "NOT-A-GSTIN"}) == {}
    print("test_a_pan_is_derived_from_a_gstin_arithmetically_never_guessed: PASS")


def test_a_malformed_pan_is_dropped_before_it_costs_a_captcha_solve():
    """gst_intake validates before opening a browser for exactly this
    reason. A typo must never reach the portal, because discovering it there
    costs a human a solve."""
    assert gg.known_pans(supplied={"X Ltd": "AANCP0234"}) == {}      # too short
    assert gg.known_pans(supplied={"X Ltd": "1ANCP0234D"}) == {}     # leading digit
    assert gg.known_pans(supplied={"X Ltd": ""}) == {}
    assert gg.known_pans(supplied={"": "AANCP0234D"}) == {}
    print("test_a_malformed_pan_is_dropped_before_it_costs_a_captcha_solve: PASS")


def test_the_entity_limit_is_reported_not_silently_applied():
    """The bound exists because each entity is human CAPTCHA work, not
    because of a rate limit. A silently truncated sweep reads as a complete
    one -- the same defect group_sweep's budget reporting was written for."""
    pans = gg.known_pans(identity_result=_IDENTITY, rera_pans=_RERA_PANS)
    result = gg.sweep(_GRAPH, pans, intake=_fake_intake, limit=1)
    assert result["checked"] == 1, result
    skipped = [r for r in result["entities"] if r["status"] == gg.STATUS_BUDGET_EXHAUSTED]
    assert len(skipped) == 1, result["entities"]
    assert any("limit of 1" in l for l in result["limitations"]), result["limitations"]
    print("test_the_entity_limit_is_reported_not_silently_applied: PASS")


def test_one_entitys_failure_costs_one_row_not_the_sweep():
    """A portal outage mid-sweep must not discard the entities already
    checked, and the failed one must say so rather than going quiet."""
    def flaky(pan, entity_name):
        if pan == "AAECP0371L":
            raise RuntimeError("portal timed out")
        return _fake_intake(pan, entity_name)

    result = gg.sweep(_GRAPH, gg.known_pans(identity_result=_IDENTITY, rera_pans=_RERA_PANS),
                      intake=flaky)
    assert result["checked"] == 1, result
    failed = [r for r in result["entities"] if r["status"] == gg.STATUS_LOOKUP_FAILED]
    assert len(failed) == 1 and "portal timed out" in failed[0]["note"], failed
    print("test_one_entitys_failure_costs_one_row_not_the_sweep: PASS")


def test_no_portal_session_is_a_stated_gap_not_an_empty_pass():
    """Running with no intake at all (the ordinary automated case) must not
    look like a sweep that found nothing: the entity holds a PAN and simply
    was not looked up."""
    result = gg.sweep(_GRAPH, gg.known_pans(identity_result=_IDENTITY), intake=None)
    row = [r for r in result["entities"] if r["name"] == "Pranami Neev Realty Limited"][0]
    assert row["status"] == gg.STATUS_LOOKUP_FAILED, row
    assert "CAPTCHA" in row["note"], row
    assert result["checked"] == 0, result
    print("test_no_portal_session_is_a_stated_gap_not_an_empty_pass: PASS")


def test_only_checked_entities_can_be_rendered_as_findings():
    """The render helper is the last line of defence: it must be impossible
    to put an unchecked entity in the findings table, where a blank filing
    record would read as a clean one."""
    result = gg.sweep(_GRAPH, gg.known_pans(identity_result=_IDENTITY, rera_pans=_RERA_PANS),
                      intake=_fake_intake)
    findings = gg.entities_with_findings(result)
    assert len(findings) == 2, findings
    assert all(row["status"] == gg.STATUS_CHECKED for row in findings), findings
    assert all(row.get("gstin") for row in findings), findings
    print("test_only_checked_entities_can_be_rendered_as_findings: PASS")


def test_proposed_entities_are_not_swept_only_confirmed_ones():
    """A brand-name match proposes a company; only a hard link confirms it.
    Sweeping proposed entities would attribute a stranger's GST record to
    this group -- the discipline group_entities was written to keep."""
    graph = dict(_GRAPH)
    graph["proposed"] = [{"name": "Pranami Textiles Ltd", "cin": "U17000DL2001PLC000001"}]
    result = gg.sweep(graph, {}, intake=_fake_intake)
    assert "Pranami Textiles Ltd" not in [r["name"] for r in result["entities"]], result
    print("test_proposed_entities_are_not_swept_only_confirmed_ones: PASS")


def test_the_group_gst_stage_is_opt_in_and_silent_when_off():
    """Off, the Charter carries no section at all -- which is different from
    carrying an empty one, and the difference is the entire point of the
    coverage reporting."""
    import company_charter as charter

    assert charter._safe_group_gst({}, "X", None, enabled=False) == {}
    assert charter._safe_group_gst({}, "X", None, enabled=None) == {}, \
        "the group GST check ran without being asked for"
    print("test_the_group_gst_stage_is_opt_in_and_silent_when_off: PASS")


if __name__ == "__main__":
    test_an_entity_with_no_pan_is_unchecked_never_compliant()
    test_the_coverage_sentence_leads_with_the_denominator()
    test_an_unverified_pan_card_never_becomes_a_lookup_key()
    test_every_pan_carries_its_provenance_and_the_strongest_wins()
    test_a_pan_is_derived_from_a_gstin_arithmetically_never_guessed()
    test_a_malformed_pan_is_dropped_before_it_costs_a_captcha_solve()
    test_the_entity_limit_is_reported_not_silently_applied()
    test_one_entitys_failure_costs_one_row_not_the_sweep()
    test_no_portal_session_is_a_stated_gap_not_an_empty_pass()
    test_only_checked_entities_can_be_rendered_as_findings()
    test_proposed_entities_are_not_swept_only_confirmed_ones()
    test_the_group_gst_stage_is_opt_in_and_silent_when_off()
    print("\nAll tests passed.")

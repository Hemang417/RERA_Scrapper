"""
Guards on the GujRERA adapter -- the first state after Maharashtra.

Three of these encode bugs found by running it live, which is the only
reason they are worth having:

  * DOCUMENT FILENAME COLLISIONS. Promoters file several different document
    slots under one filename -- the test project uploads three separate
    "NOT AVAILABLE.pdf" placeholders and reuses names across balance-sheet
    years. Writing each to its source filename silently destroyed 15 of 42
    documents: the manifest said 42, the disk had 27.

  * THE STORAGE KEY. Gujarat's registration number is
    PR/GJ/SURAT/SURAT CITY/SUDA/PAA12907/120224/311228 -- full of path
    separators. AcquisitionResult.reg_no is documented as the primary key of
    output/<reg_no>/, and used unchanged it produced a SIX-LEVEL nested
    directory tree instead of one project folder. MahaRERA's P51800077150
    is filesystem-safe only by luck.

  * THE SEARCH REQUEST SHAPE. Recovered from the server's own error
    envelope, which echoes the fields it expected (query/startWith/dataSize/
    sortBy) back as nulls. Easy to lose again on a refactor, and the failure
    mode is a 500 that looks like the portal being down.

Network-touching tests are separated and skipped by default so the suite
stays offline. Run them deliberately with GUJRERA_LIVE=1.

Run directly: python test_gujarat_adapter.py
"""

import os

import states
from states.adapter_gujarat import _document_entries, _prettify, _professionals
from states.base import storage_key

_LIVE = os.environ.get("GUJRERA_LIVE") == "1"
_SAMPLE_REG_NO = "PR/GJ/SURAT/SURAT CITY/SUDA/PAA12907/120224/311228"


def test_the_registration_format_is_unambiguous():
    """Unlike MahaRERA/TG-RERA, Gujarat's format identifies its authority
    outright, so a Gujarat number needs no probing at all."""
    profiles, _note = states.candidate_profiles(_SAMPLE_REG_NO)
    assert [p.code for p in profiles] == ["GJ"], [p.code for p in profiles]
    print("test_the_registration_format_is_unambiguous: PASS")


def test_the_storage_key_is_flat_but_the_real_number_survives():
    key = storage_key(_SAMPLE_REG_NO)
    assert "/" not in key and "\\" not in key, key
    assert key == "PR-GJ-SURAT-SURAT_CITY-SUDA-PAA12907-120224-311228", key
    # MahaRERA's is untouched -- the existing production path must not move.
    assert storage_key("P51800077150") == "P51800077150"
    print("test_the_storage_key_is_flat_but_the_real_number_survives: PASS")


def test_document_entries_pair_uid_with_a_readable_label():
    payload = {
        "projectdoc": {
            "docId": 1,
            "performaForSaleOfAgreementId": 99,
            "performaForSaleOfAgreementUId": "UID-A",
            "someUnnamedThingUId": "UID-B",
            "missingDocUId": None,          # never filed -- an absence, not an error
        },
        "findoc": {"auditedBalSheetDoc1UId": "UID-C"},
    }
    entries = _document_entries(payload)
    uids = [e["uid"] for e in entries]
    assert uids == ["UID-A", "UID-B", "UID-C"], uids
    assert entries[0]["label"] == "Proforma agreement for sale", entries[0]
    # An unnamed field still gets something a reader can parse, never a raw key.
    assert entries[1]["label"] == "Some unnamed thing", entries[1]
    assert entries[2]["label"] == "Audited balance sheet (1)", entries[2]
    print("test_document_entries_pair_uid_with_a_readable_label: PASS")


def test_a_null_uid_is_skipped_not_downloaded():
    """A document slot the promoter never filed must not become a failed
    download row -- it is an absence, and the Charter reports absences
    differently from failures."""
    entries = _document_entries({"projectdoc": {"aUId": None, "bUId": ""}})
    assert entries == [], entries
    print("test_a_null_uid_is_skipped_not_downloaded: PASS")


def test_professionals_are_normalised_to_maharera_shape():
    """company_charter.summarize_professionals expects ONE list of dicts
    carrying professionalTypeName. Gujarat splits them across four lists, so
    the adapter normalises rather than that function growing a state branch."""
    details = {
        "englist": [{"name": "MAHESH J SAVALIYA", "licenceNO": "TDO ER 1060"}],
        "calist": [{"name": "A CA"}],
        "acrchlist": [],
        "contr": [{"name": "A CONTRACTOR"}],
    }
    out = _professionals(details)
    assert len(out) == 3, out
    assert {r["professionalTypeName"] for r in out} == {"Engineer", "Chartered Accountant", "Contractor"}
    assert all(r.get("entityCompanyName") for r in out), out
    print("test_professionals_are_normalised_to_maharera_shape: PASS")


def test_prettify_never_shows_a_raw_camelcase_key():
    assert _prettify("auditedBalSheetDoc2_") == "Audited bal sheet doc2"
    assert _prettify("incomeTaxReturn1") == "Income tax return1"
    print("test_prettify_never_shows_a_raw_camelcase_key: PASS")


def test_capabilities_are_declared_honestly():
    gj = states.PROFILES["GJ"]
    assert gj.can(states.CAP_LOOKUP_BY_REG_NO)
    assert gj.can(states.CAP_CATEGORY_API)
    assert gj.can(states.CAP_DOCUMENTS)
    # GujRERA publishes no name-searchable complaint/appeal register through
    # this interface, and its land system (AnyROR) is not wired in. Declaring
    # those false is what stops MahaRERA-only work running for it.
    assert not gj.can(states.CAP_ORDERS_SEARCH)
    assert not gj.can(states.CAP_LAND_RECORDS)
    # Confirmed live: a promoter's other projects are NOT reachable. This
    # capability WAS declared true on first write and had to be corrected --
    # the adapter returned promoter_portfolio=None while claiming otherwise.
    assert not gj.can(states.CAP_PROMOTER_PORTFOLIO)
    print("test_capabilities_are_declared_honestly: PASS")


def test_no_adapter_claims_a_capability_it_does_not_deliver():
    """The general guard behind the bug above.

    A declared capability that the adapter never fulfils is worse than an
    undeclared one: downstream code checks profile.can(...) to decide
    whether an absence is 'this state cannot' or 'this run failed', and a
    false declaration makes an absence look like a failure. This walks every
    registered adapter and checks the two capabilities whose delivery is
    visible in the AcquisitionResult shape."""
    import inspect

    for code in sorted(states.PROFILES):
        try:
            adapter = states.get_adapter(code)
        except NotImplementedError:
            continue  # profile registered, adapter is future work -- fine
        profile = adapter.profile
        source = inspect.getsource(type(adapter))

        if profile.can(states.CAP_PROMOTER_PORTFOLIO):
            assert "promoter_portfolio=None" not in source, (
                f"{code} declares CAP_PROMOTER_PORTFOLIO but its adapter hard-codes "
                f"promoter_portfolio=None. Either build the portfolio or drop the "
                f"capability -- a false declaration makes 'this state cannot' look "
                f"like 'this run failed'."
            )
        if profile.can(states.CAP_DOCUMENTS):
            assert "documents_manifest=[]" not in source, (
                f"{code} declares CAP_DOCUMENTS but its adapter hard-codes an empty "
                f"documents manifest."
            )
    print("test_no_adapter_claims_a_capability_it_does_not_deliver: PASS")


# --- live tests, opt-in --------------------------------------------------

def test_live_search_returns_the_expected_envelope():
    if not _LIVE:
        print("test_live_search_returns_the_expected_envelope: SKIPPED (set GUJRERA_LIVE=1)")
        return
    from states.adapter_gujarat import _pool, search
    rows = search(_pool(), _SAMPLE_REG_NO)
    projects = [r for r in rows if r.get("entityType") == "PROJECT"]
    assert len(projects) == 1, len(projects)
    assert projects[0]["regNo"].strip() == _SAMPLE_REG_NO, projects[0]["regNo"]
    print("test_live_search_returns_the_expected_envelope: PASS")


def test_live_every_manifest_row_has_its_own_file():
    """The collision regression, checked end-to-end: manifest length and
    file count must agree."""
    if not _LIVE:
        print("test_live_every_manifest_row_has_its_own_file: SKIPPED (set GUJRERA_LIVE=1)")
        return
    import shutil
    import tempfile
    from states.adapter_gujarat import ADAPTER

    class _Reporter:
        def info(self, m): pass
        def warn(self, m): pass
        def ok(self, m): pass
        def choose(self, p, o): return None

    tmp = tempfile.mkdtemp(prefix="gjrera_")
    try:
        ctx = states.AcquisitionContext(output_dir=tmp, reporter=_Reporter())
        result = ADAPTER.acquire(_SAMPLE_REG_NO, ctx)
        saved = [r["saved_filename"] for r in result.documents_manifest if r["saved_filename"]]
        assert len(saved) == len(set(saved)), "two manifest rows share a filename"
        on_disk = os.listdir(os.path.join(tmp, result.reg_no, "documents"))
        assert len(on_disk) == len(result.documents_manifest), (len(on_disk), len(result.documents_manifest))
        assert result.registration_number == _SAMPLE_REG_NO, result.registration_number
        assert "/" not in result.reg_no, result.reg_no
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("test_live_every_manifest_row_has_its_own_file: PASS")


# --- opening one project -------------------------------------------------
#
# Gujarat cannot be SEARCHED by promoter -- that is settled and stated in
# group_sweep._CANNOT_SEARCH. Being unable to OPEN a project was a separate
# gap: a GujRERA registration arriving from any other source (a promoter's
# declared past project, an address in a past-experience row, a human) had
# nowhere to go. These guard the open.

def _gj_restore(saved):
    from states import adapter_gujarat as gj

    gj.search, gj._get, gj._pool = saved


def _gj_saved():
    from states import adapter_gujarat as gj

    return (gj.search, gj._get, gj._pool)


_GJ_DETAILS = {
    "projectDetail": {"projectName": "AALEKH BUNGALOWS - A", "distName": "Surat"},
    "dev": [{"name": "AALEKH ENTERPRISE"}],
    "acrchlist": [{"name": "SOME ARCHITECT"}],
}
_GJ_PREV = {"pervlist": [{"projectName": "EARLIER ONE"}], "gujrera": [{"projectName": "AND TWO"}]}


def test_gujarat_opens_a_project_by_its_registration_number():
    from states import adapter_gujarat as gj

    saved = _gj_saved()
    gj.search = lambda pool, q, size=100: [
        {"entityType": "PROJECT", "entityId": "17020", "regNo": _SAMPLE_REG_NO},
        {"entityType": "PROMOTER", "entityId": "999", "regNo": ""},
    ]
    gj._get = lambda pool, path: (
        _GJ_DETAILS if "getproject-details" in path
        else _GJ_PREV if "getprev-project-list" in path else {}
    )
    gj._pool = lambda: None
    try:
        summary = gj.fetch_project_summary(_SAMPLE_REG_NO)
    finally:
        _gj_restore(saved)

    assert summary["opened"] is True, summary
    assert summary["promoter_name"] == "AALEKH ENTERPRISE", summary
    assert summary["project_name"] == "AALEKH BUNGALOWS - A", summary
    assert summary["district"] == "Surat", summary
    # A PROMOTER row in the same search result must not be mistaken for the
    # project -- their entityId is an application id, not a promoter key.
    assert summary["project_id"] == "17020", summary
    print("test_gujarat_opens_a_project_by_its_registration_number: PASS")


def test_gujarat_surfaces_the_past_projects_only_this_page_carries():
    """The reason the open earns its place. Gujarat cannot be searched by
    promoter, so `getprev-project-list` is the ONLY route to registrations
    the promoter built before this one."""
    from states import adapter_gujarat as gj

    saved = _gj_saved()
    gj.search = lambda pool, q, size=100: [
        {"entityType": "PROJECT", "entityId": "17020", "regNo": _SAMPLE_REG_NO}]
    gj._get = lambda pool, path: (
        _GJ_DETAILS if "getproject-details" in path
        else _GJ_PREV if "getprev-project-list" in path else {}
    )
    gj._pool = lambda: None
    try:
        summary = gj.fetch_project_summary(_SAMPLE_REG_NO)
    finally:
        _gj_restore(saved)

    assert len(summary["declared_other_projects"]) == 2, summary["declared_other_projects"]
    assert any("would not otherwise have been found" in n for n in summary["notes"]), \
        summary["notes"]
    print("test_gujarat_surfaces_the_past_projects_only_this_page_carries: PASS")


def test_gujarat_never_guesses_between_several_matches():
    """There is no reporter to ask inside a sweep, and picking the first row
    would attribute another project's filings to this reference."""
    from states import adapter_gujarat as gj

    saved = _gj_saved()
    gj.search = lambda pool, q, size=100: [
        {"entityType": "PROJECT", "entityId": "1", "regNo": "PR/GJ/A"},
        {"entityType": "PROJECT", "entityId": "2", "regNo": "PR/GJ/B"},
    ]
    gj._pool = lambda: None
    try:
        summary = gj.fetch_project_summary("AALEKH")
    finally:
        _gj_restore(saved)

    assert summary["opened"] is False, summary
    assert "NOT opened rather than guessed at" in summary["note"], summary["note"]
    print("test_gujarat_never_guesses_between_several_matches: PASS")


def test_gujarat_an_empty_record_is_not_a_project_with_nothing_filed():
    from states import adapter_gujarat as gj

    saved = _gj_saved()
    gj.search = lambda pool, q, size=100: [
        {"entityType": "PROJECT", "entityId": "17020", "regNo": _SAMPLE_REG_NO}]
    gj._get = lambda pool, path: {}
    gj._pool = lambda: None
    try:
        summary = gj.fetch_project_summary(_SAMPLE_REG_NO)
    finally:
        _gj_restore(saved)

    assert summary["opened"] is False, summary
    assert "no such record" in summary["note"], summary["note"]
    print("test_gujarat_an_empty_record_is_not_a_project_with_nothing_filed: PASS")


def test_gujarat_complaints_stay_unknown_when_a_project_is_opened():
    """Opening the project must not quietly imply a clean complaint record:
    GujRERA publishes no name-searchable complaint register at all."""
    from states import adapter_gujarat as gj

    saved = _gj_saved()
    gj.search = lambda pool, q, size=100: [
        {"entityType": "PROJECT", "entityId": "17020", "regNo": _SAMPLE_REG_NO}]
    gj._get = lambda pool, path: (_GJ_DETAILS if "getproject-details" in path else {})
    gj._pool = lambda: None
    try:
        summary = gj.fetch_project_summary(_SAMPLE_REG_NO)
    finally:
        _gj_restore(saved)

    assert any("UNKNOWN" in n and "not be read as a clean record" in n
               for n in summary["notes"]), summary["notes"]
    print("test_gujarat_complaints_stay_unknown_when_a_project_is_opened: PASS")


if __name__ == "__main__":
    test_gujarat_opens_a_project_by_its_registration_number()
    test_gujarat_surfaces_the_past_projects_only_this_page_carries()
    test_gujarat_never_guesses_between_several_matches()
    test_gujarat_an_empty_record_is_not_a_project_with_nothing_filed()
    test_gujarat_complaints_stay_unknown_when_a_project_is_opened()
    test_the_registration_format_is_unambiguous()
    test_the_storage_key_is_flat_but_the_real_number_survives()
    test_document_entries_pair_uid_with_a_readable_label()
    test_a_null_uid_is_skipped_not_downloaded()
    test_professionals_are_normalised_to_maharera_shape()
    test_prettify_never_shows_a_raw_camelcase_key()
    test_capabilities_are_declared_honestly()
    test_no_adapter_claims_a_capability_it_does_not_deliver()
    test_live_search_returns_the_expected_envelope()
    test_live_every_manifest_row_has_its_own_file()
    print("\nAll tests passed.")

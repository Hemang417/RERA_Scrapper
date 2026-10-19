"""
Tests for company_charter._safe_nclt_check / _safe_bombay_hc_check -- the
direct-portal litigation checks (see nclt_search.py / bombay_hc_search.py),
distinct from the Indian Kanoon-based _safe_group_litigation name search.

Mocks nclt_search.search_party / bombay_hc_search.search_party (network + a
real browser/CAPTCHA) to keep this offline and fast, same convention as
test_cts_intake.py mocking mahabhumi.search_cts_candidates/
fetch_property_card -- this file tests company_charter's own wrapper logic
(default-on/explicit-disable behavior, per-lookup failure isolation), not the
Playwright/DOM internals of either search module against the live portals.
Autosolve plumbing is covered offline in test_court_captcha_solver.py.

Neither direct check has a human CAPTCHA fallback. The
`*_CAPTCHA_AUTOSOLVE=0` switches skip the corresponding check.

Run directly: python test_nclt_bombay_hc_checks.py
"""

from unittest import mock

import bombay_hc_search
import nclt_search
import company_charter as cc


def test_nclt_check_skips_when_autosolve_is_disabled():
    with mock.patch.dict("os.environ", {"NCLT_CAPTCHA_AUTOSOLVE": "0"}), \
         mock.patch("sys.stdin.isatty", return_value=False), \
         mock.patch.object(nclt_search, "search_party") as fake_search:
        result = cc._safe_nclt_check("Some Promoter LLP")

    assert result["attempted"] is False
    fake_search.assert_not_called()
    print("test_nclt_check_skips_when_autosolve_is_disabled: PASS")


def test_nclt_check_autosolves_by_default_without_terminal():
    fake_result = {"found": False, "rows": [], "raw_text": "", "url": "https://x", "note": ""}
    with mock.patch.dict("os.environ", {}, clear=True), \
         mock.patch("sys.stdin.isatty", return_value=False), \
         mock.patch.object(nclt_search, "search_party", return_value=fake_result) as fake_search:
        result = cc._safe_nclt_check("Some Promoter LLP")

    fake_search.assert_called_once_with("Some Promoter LLP", "Mumbai")
    assert result["attempted"] is True
    assert result["captcha_mode"] == "Claude vision"
    print("test_nclt_check_autosolves_by_default_without_terminal: PASS")


def test_nclt_check_reports_claude_vision_mode():
    fake_result = {"found": False, "rows": [], "raw_text": "No Record Found In Case Detail.", "url": "https://x", "note": ""}
    with mock.patch.dict("os.environ", {}, clear=True), \
         mock.patch("sys.stdin.isatty", return_value=False), \
         mock.patch.object(nclt_search, "search_party", return_value=fake_result) as fake_search:
        result = cc._safe_nclt_check("Some Promoter LLP", bench="Mumbai")

    fake_search.assert_called_once_with("Some Promoter LLP", "Mumbai")
    assert result["attempted"] is True
    assert result["captcha_mode"] == "Claude vision"
    assert result["bench"] == "Mumbai"
    assert result["found"] is False
    print("test_nclt_check_reports_claude_vision_mode: PASS")


def test_nclt_check_exception_is_never_fatal():
    with mock.patch("sys.stdin.isatty", return_value=True), \
         mock.patch.object(nclt_search, "search_party", side_effect=RuntimeError("browser crashed")):
        result = cc._safe_nclt_check("Some Promoter LLP")

    assert result["attempted"] is True
    assert result["found"] is False
    assert "RuntimeError" in result["note"]
    assert "browser crashed" not in result["note"]
    print("test_nclt_check_exception_is_never_fatal: PASS")


def test_nclt_check_never_leaks_raw_exception_text():
    """rules.md Section B: 'no file path... or raw exception string into
    either document.' Confirmed live: a bare Playwright browser-launch
    failure's str(e) embeds the full local chrome.exe command line --
    absolute filesystem paths, the OS username in a temp-profile path --
    which reached a real generated Charter before this was fixed. The note
    must carry only the exception's TYPE name, never its message."""
    leaky_message = (
        "BrowserType.launch: spawn UNKNOWN\nCall log:\n"
        "  - <launching> C:\\Users\\SomeUser\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe "
        "--user-data-dir=C:\\Users\\SomeUser\\AppData\\Local\\Temp\\playwright_chromiumdev_profile-abc123"
    )
    with mock.patch("sys.stdin.isatty", return_value=True), \
         mock.patch.object(nclt_search, "search_party", side_effect=RuntimeError(leaky_message)):
        result = cc._safe_nclt_check("Some Promoter LLP")

    assert "C:\\Users" not in result["note"]
    assert "user-data-dir" not in result["note"]
    assert "chrome.exe" not in result["note"]
    assert "RuntimeError" in result["note"]
    print("test_nclt_check_never_leaks_raw_exception_text: PASS")


def test_bombay_hc_check_skips_when_autosolve_is_disabled():
    with mock.patch.dict("os.environ", {"BOMBAY_HC_CAPTCHA_AUTOSOLVE": "0"}), \
         mock.patch("sys.stdin.isatty", return_value=False), \
         mock.patch.object(bombay_hc_search, "search_party") as fake_search:
        result = cc._safe_bombay_hc_check("Some Promoter LLP")

    assert result["attempted"] is False
    fake_search.assert_not_called()
    print("test_bombay_hc_check_skips_when_autosolve_is_disabled: PASS")


def test_bombay_hc_check_autosolves_by_default_without_terminal():
    fake_result = {"found": False, "rows": [], "raw_response": {"ok": True}, "url": "https://x", "note": ""}
    with mock.patch.dict("os.environ", {}, clear=True), \
         mock.patch("sys.stdin.isatty", return_value=False), \
         mock.patch.object(bombay_hc_search, "search_party", return_value=fake_result) as fake_search:
        result = cc._safe_bombay_hc_check("Some Promoter LLP")

    assert result["attempted"] is True
    assert fake_search.call_count == 10
    assert result["captcha_mode"] == "Claude vision"
    print("test_bombay_hc_check_autosolves_by_default_without_terminal: PASS")


def test_bombay_hc_check_runs_all_combinations_without_human_input():
    """Every bench/year combination runs without a TTY or human prompt."""
    fake_result = {"found": False, "rows": [], "raw_response": {"ok": True}, "url": "https://x", "note": ""}
    with mock.patch.dict("os.environ", {}, clear=True), \
         mock.patch("sys.stdin.isatty", return_value=False), \
         mock.patch("builtins.input") as fake_input, \
         mock.patch.object(bombay_hc_search, "search_party", return_value=fake_result) as fake_search:
        result = cc._safe_bombay_hc_check("Some Promoter LLP")

    fake_input.assert_not_called()
    assert result["attempted"] is True
    assert result["captcha_mode"] == "Claude vision"
    # 2 benches x 5 years, per _safe_bombay_hc_check's own scoping.
    assert len(result["runs"]) == 10
    assert fake_search.call_count == 10
    for run in result["runs"]:
        assert run["bench"] in result["benches_checked"]
        assert run["year"] in result["years_checked"]
    print("test_bombay_hc_check_runs_all_combinations_without_human_input: PASS")


def test_bombay_hc_check_one_failure_does_not_abort_the_sweep():
    call_count = {"n": 0}

    def flaky_search(party_name, year, bench):
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise RuntimeError("captcha timed out")
        return {"found": False, "rows": [], "raw_response": {"ok": True}, "url": "https://x", "note": ""}

    with mock.patch("sys.stdin.isatty", return_value=True), \
         mock.patch.object(bombay_hc_search, "search_party", side_effect=flaky_search):
        result = cc._safe_bombay_hc_check("Some Promoter LLP")

    assert result["attempted"] is True
    assert len(result["runs"]) == 10  # every combination still attempted
    failed = [r for r in result["runs"] if r.get("note")]
    assert len(failed) == 1
    assert "RuntimeError" in failed[0]["note"]
    assert "captcha timed out" not in failed[0]["note"]
    print("test_bombay_hc_check_one_failure_does_not_abort_the_sweep: PASS")


def test_bombay_hc_check_never_leaks_raw_exception_text():
    """Same leak class as NCLT (bombay_hc_search.py also opens a real
    Playwright browser, and this call site has no separate named-exception
    branch, so every failure funnels through the one generic handler)."""
    leaky_message = (
        "BrowserType.launch: spawn UNKNOWN\nCall log:\n"
        "  - <launching> C:\\Users\\SomeUser\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe "
        "--user-data-dir=C:\\Users\\SomeUser\\AppData\\Local\\Temp\\playwright_chromiumdev_profile-abc123"
    )
    with mock.patch("sys.stdin.isatty", return_value=True), \
         mock.patch.object(bombay_hc_search, "search_party", side_effect=RuntimeError(leaky_message)):
        result = cc._safe_bombay_hc_check("Some Promoter LLP")

    for run in result["runs"]:
        assert "C:\\Users" not in (run.get("note") or "")
        assert "user-data-dir" not in (run.get("note") or "")
        assert "chrome.exe" not in (run.get("note") or "")
    print("test_bombay_hc_check_never_leaks_raw_exception_text: PASS")


if __name__ == "__main__":
    test_nclt_check_skips_when_autosolve_is_disabled()
    test_nclt_check_autosolves_by_default_without_terminal()
    test_nclt_check_reports_claude_vision_mode()
    test_nclt_check_exception_is_never_fatal()
    test_nclt_check_never_leaks_raw_exception_text()
    test_bombay_hc_check_skips_when_autosolve_is_disabled()
    test_bombay_hc_check_autosolves_by_default_without_terminal()
    test_bombay_hc_check_runs_all_combinations_without_human_input()
    test_bombay_hc_check_one_failure_does_not_abort_the_sweep()
    test_bombay_hc_check_never_leaks_raw_exception_text()
    print("\nAll tests passed.")

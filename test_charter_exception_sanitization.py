"""
rules.md Section B: "no file path, module name, function or parameter name,
JSON key, or raw exception string into either document; least of all
External." Several `_safe_*` never-fatal wrappers in company_charter.py used
to interpolate a caught exception's raw str(e) directly into a `note`/
`limitations` field that gets rendered verbatim into the Company Charter
.docx/PDF. Confirmed live: _safe_nclt_check's bare Playwright browser-launch
failure embedded the full local chrome.exe command line -- absolute
filesystem paths, the OS username in a temp-profile path -- and it reached a
real generated Charter (both Internal and External) before this was fixed.

Two other leak-prone wrappers live only in company_charter.py with no
existing per-module test file to extend (unlike the CTS/IGR/NCLT/Bombay HC
checks, which have their own test_cts_intake.py / test_igr_registered_deed_
check.py / test_nclt_bombay_hc_checks.py): _safe_charge_movement
(charge_watch.py reads/writes a local snapshot file) and
_safe_promoter_identity (promoter_identity.py opens PAN-card PDFs/images
directly from the local document library via fitz.open/PIL). Both covered
here.

Run directly: python test_charter_exception_sanitization.py
"""

from unittest import mock

import charge_watch
import promoter_identity
import company_charter as cc

_LEAKY_MESSAGE = (
    "BrowserType.launch: spawn UNKNOWN\nCall log:\n"
    "  - <launching> C:\\Users\\SomeUser\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe "
    "--user-data-dir=C:\\Users\\SomeUser\\AppData\\Local\\Temp\\playwright_chromiumdev_profile-abc123"
)


def _assert_no_leak(note: str):
    assert "C:\\Users" not in note
    assert "user-data-dir" not in note
    assert "chrome.exe" not in note
    assert "RuntimeError" in note


def test_charge_movement_never_leaks_raw_exception_text():
    profile_result = {"found": True, "cin": "U12345MH2020PTC000000", "name": "Test Promoter Pvt Ltd"}
    with mock.patch.object(charge_watch, "load_previous", side_effect=RuntimeError(_LEAKY_MESSAGE)):
        movement = cc._safe_charge_movement(profile_result, output_dir="output")

    assert movement["checked"] is False
    _assert_no_leak(movement["note"])
    print("test_charge_movement_never_leaks_raw_exception_text: PASS")


def test_charge_movement_returns_empty_dict_without_a_cin():
    # Unrelated to the leak fix, but pins down the early-return path this
    # test module's mocking relies on not changing shape silently.
    assert cc._safe_charge_movement({}, output_dir="output") == {}
    assert cc._safe_charge_movement({"found": False, "cin": "X"}, output_dir="output") == {}
    print("test_charge_movement_returns_empty_dict_without_a_cin: PASS")


def test_promoter_identity_never_leaks_raw_exception_text():
    with mock.patch.object(promoter_identity, "extract_promoter_pan", side_effect=RuntimeError(_LEAKY_MESSAGE)):
        result = cc._safe_promoter_identity(documents_manifest=[], documents_dir="output/x/documents", category_data={})

    assert result["verified"] is False
    assert result["pan"] is None
    _assert_no_leak(result["notes"][0])
    print("test_promoter_identity_never_leaks_raw_exception_text: PASS")


if __name__ == "__main__":
    test_charge_movement_never_leaks_raw_exception_text()
    test_charge_movement_returns_empty_dict_without_a_cin()
    test_promoter_identity_never_leaks_raw_exception_text()
    print("\nAll tests passed.")

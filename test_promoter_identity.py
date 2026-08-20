"""Guards on reading the promoter's PAN off the filed PAN card.

THE RULE THIS FILE EXISTS TO ENFORCE: a PAN read off a photograph is a
CANDIDATE until something ties it to this promoter.

The whole point of extracting a PAN is that it is a national join key -- it
is what pulls MCA charges, GST filings and litigation into a Charter. So a
wrong PAN does not degrade the document, it poisons it: every downstream
lookup would return another company's records, all of them internally
consistent and all of them about the wrong entity. That is strictly worse
than reporting no PAN at all, and OCR on a phone-photographed card is
exactly the kind of input that produces confident near-misses.

Hence verify_pan checks the number against the card's own structure AND
against the promoter name the portal independently published, and anything
that fails is returned in `unverified_candidates` with its reason rather
than used.

The second guard here is subtler and cost real debugging time: A MISSING
OCR BINARY MUST NOT READ AS "NO PAN". Tesseract is shelled out to by name,
so when it is absent every scanned card yields the empty string -- silently,
with no exception. "This promoter filed no PAN card" and "we could not read
the PAN card they filed" are different findings, and only one of them is
true in that case. company_charter.py carries the same warning about the
same binary for the same reason.

Most tests here run offline on synthetic text. The two that replay the real
filing (`output/P51800077150/documents/PAN of PNRL.pdf`, a genuine scanned
upload whose whole-page OCR returns ZERO characters) skip themselves if that
artifact or Tesseract is absent, so the suite stays green on a clean
checkout while still proving the real case end to end where it can.

Run directly: python test_promoter_identity.py
"""

import io
import json
import os

import promoter_identity as pi

# The real filing: a company PAN. 4th char "C" = Company, 5th char "P" =
# PRANAMI. Both are load-bearing in the tests below.
_REAL_PAN = "AANCP0234D"
_REAL_NAME = "Pranami Neev Realty Limited"
_REAL_PROJECT = os.path.join("output", "P51800077150")
_REAL_PDF = os.path.join(_REAL_PROJECT, "documents", "PAN of PNRL.pdf")


def _skip(reason):
    print(f"  SKIPPED -- {reason}")
    return True


# --- the central rule -----------------------------------------------------

def test_a_pan_that_belongs_to_someone_else_is_rejected():
    """The guard that matters most. A structurally perfect PAN whose 5th
    character does not match the promoter's initial belongs to a different
    taxpayer, and accepting it would pull a stranger's MCA and GST record
    into this Charter."""
    verdict = pi.verify_pan("AANCZ0234D", _REAL_NAME)
    assert verdict["ok"] is False, verdict
    assert "does not match" in verdict["reason"], verdict
    assert "'Z'" in verdict["reason"] and "'P'" in verdict["reason"], verdict
    # ...and the correct one for the same promoter passes.
    good = pi.verify_pan(_REAL_PAN, _REAL_NAME)
    assert good["ok"] is True, good
    assert good["holder_type"] == "Company", good
    assert len(good["checks"]) == 2, good
    print("test_a_pan_that_belongs_to_someone_else_is_rejected: PASS")


def test_an_unverifiable_pan_is_reported_not_dropped_and_not_used():
    """A candidate that fails verification must still be visible -- silently
    discarding it would hide that the card was read but not trusted."""
    if not os.path.exists(_REAL_PDF) or not pi.tesseract_available():
        return _skip("real PAN card artifact or Tesseract unavailable")
    manifest = [{
        "label": "PAN Card",
        "saved_filename": os.path.basename(_REAL_PDF),
        "status": "downloaded",
    }]
    result = pi.extract_promoter_pan(
        manifest, os.path.dirname(_REAL_PDF), "Completely Different Developers LLP"
    )
    # The real card reads fine, but the name does not match, so:
    assert result["pan"] is None, result
    assert result["verified"] is False, result
    assert any(c["pan"] == _REAL_PAN for c in result["unverified_candidates"]), result
    assert any("could not be tied to this promoter" in n for n in result["notes"]), result
    print("test_an_unverifiable_pan_is_reported_not_dropped_and_not_used: PASS")


def test_holder_type_must_be_a_real_pan_code():
    """The 4th character is a closed set. An unrecognised letter means the
    read is wrong, not that a new entity type exists."""
    assert pi.verify_pan("AAN10234D", _REAL_NAME)["ok"] is False
    bad = pi.verify_pan("AANXP0234D", _REAL_NAME)
    assert bad["ok"] is False, bad
    assert "holder-type" in bad["reason"], bad
    for code, label in (("C", "Company"), ("F", "Firm / LLP"), ("P", "Individual")):
        v = pi.verify_pan(f"AAN{code}P0234D", "Pranami Neev Realty Limited")
        assert v["holder_type"] == label, (code, v)
    print("test_holder_type_must_be_a_real_pan_code: PASS")


def test_a_pan_with_no_promoter_name_is_never_promoted_to_verified():
    """Structurally valid is not the same as attributed. With nothing to
    check the 5th character against, the honest answer is "unconfirmed"."""
    verdict = pi.verify_pan(_REAL_PAN, None)
    assert verdict["ok"] is False, verdict
    assert "no promoter name available" in verdict["reason"], verdict
    print("test_a_pan_with_no_promoter_name_is_never_promoted_to_verified: PASS")


def test_legal_form_prefixes_do_not_become_the_initial():
    """"M/S PRANAMI..." must check against P, not M -- otherwise a correct
    PAN gets rejected for a bookkeeping word."""
    assert pi.verify_pan(_REAL_PAN, "M/S Pranami Neev Realty Limited")["ok"] is True
    assert pi.verify_pan(_REAL_PAN, "Shree Pranami Neev Realty")["ok"] is True
    print("test_legal_form_prefixes_do_not_become_the_initial: PASS")


def test_a_group_companys_pan_is_rejected_even_though_the_initial_matches():
    """THE CASE THE INITIAL TEST CANNOT CATCH, found live on a real JHARERA
    filing.

    The PAN card on file read PRANAMI BUILDERS PRIVATE LIMITED. The promoter
    of record for that project is PBPL PRANAMI CREST RERA PRIVATE LIMITED --
    a different company in the same group. Both begin with P, so the
    holder-type and 5th-character checks BOTH pass and the number would have
    been attributed to the special purpose vehicle, pulling the parent's
    charges, filings and litigation into the SPV's record.

    Same brand is not the same entity. That is the rule group_entities.py
    exists to enforce, and it has to hold here too."""
    verdict = pi.verify_pan(
        "AAECP0371L", "PBPL PRANAMI CREST RERA PRIVATE LIMITED",
        card_name="PRANAMI BUILDERS PRIVATE LIMITED",
    )
    assert verdict["ok"] is False, verdict
    assert "different entities" in verdict["reason"], verdict
    assert "Pranami Builders" in verdict["reason"], verdict
    # Without the card name, the weaker checks still pass -- which is
    # precisely why reading the name off the card matters.
    weaker = pi.verify_pan("AAECP0371L", "PBPL PRANAMI CREST RERA PRIVATE LIMITED")
    assert weaker["ok"] is True, weaker
    print("test_a_group_companys_pan_is_rejected_even_though_the_initial_matches: PASS")


def test_the_same_entity_still_verifies_across_spelling_differences():
    """The name check must not reject a genuine match over "Ltd" vs
    "Limited", or a word OCR dropped."""
    assert pi.names_agree("PRANAMI NEEV REALTY LIMITED", "Pranami Neev Realty Limited") is True
    assert pi.names_agree("PRANAMI NEEV REALTY LIMITED", "Pranami Neev Realty Ltd") is True
    assert pi.names_agree("PRANAMI NEEV REALTY", "Pranami Neev Realty Private Limited") is True
    assert pi.names_agree("PRANAMI BUILDERS PRIVATE LIMITED",
                          "PBPL PRANAMI CREST RERA PRIVATE LIMITED") is False
    # Nothing to compare is not the same as disagreement.
    assert pi.names_agree("", "Anything Ltd") is None
    assert pi.names_agree("LIMITED PRIVATE", "Anything Ltd") is None
    verdict = pi.verify_pan(_REAL_PAN, _REAL_NAME, card_name="PRANAMI NEEV REALTY LIMITED")
    assert verdict["ok"] is True and len(verdict["checks"]) == 3, verdict
    print("test_the_same_entity_still_verifies_across_spelling_differences: PASS")


def test_the_holder_name_is_read_off_the_card_not_its_boilerplate():
    """Every card prints "INCOME TAX DEPARTMENT" and "Permanent Account
    Number" in capitals. Picking either as the holder would make the name
    check compare the wrong thing on every single document."""
    text = "\n".join([
        "INCOME TAX DEPARTMENT", "GOVT. OF INDIA", "Permanent Account Number Card",
        "AANCP0234D", "PRANAMI NEEV REALTY LIMITED",
        "Date of Incorporation / Formation", "27/06/2022",
    ])
    assert pi.card_holder_name(text) == "PRANAMI NEEV REALTY LIMITED", pi.card_holder_name(text)
    # The older layout, name above the date rather than under a "Name" label.
    older = "\n".join([
        "INCOME TAX DEPARTMENT", "GOVT. OF INDIA", "PRANAMI BUILDERS PRIVATE LIMITED",
        "22/05/1995", "Permanent Account Number", "AAECP0371L",
    ])
    assert pi.card_holder_name(older) == "PRANAMI BUILDERS PRIVATE LIMITED", pi.card_holder_name(older)
    assert pi.card_holder_name("") is None
    assert pi.card_holder_name("INCOME TAX DEPARTMENT\nGOVT. OF INDIA") is None
    print("test_the_holder_name_is_read_off_the_card_not_its_boilerplate: PASS")


def test_a_low_contrast_card_gets_a_binarised_second_pass():
    """A PAN card is dark text over a pastel GRADIENT. In greyscale the
    darker end of that background sits close to the ink, and Tesseract
    returned two thousand characters of noise and neither the name nor the
    number on a real JHARERA card. Thresholding recovers both, so both
    variants are always OCRed."""
    from PIL import Image

    image = Image.new("L", (40, 40), color=160)
    variants = list(pi._image_variants(image))
    assert len(variants) == 2, variants
    assert sorted(variants[1].getdata())[0] in (0, 255), "the second variant is not binarised"
    assert set(variants[1].getdata()) <= {0, 255}, "binarised image has intermediate tones"
    print("test_a_low_contrast_card_gets_a_binarised_second_pass: PASS")


# --- OCR repair -----------------------------------------------------------

def test_ocr_confusions_are_repaired_positionally_not_guessed():
    """A PAN's layout is fixed -- 5 letters, 4 digits, 1 letter -- so an O
    landing in a digit slot is repairable with certainty. A token that is
    not merely mis-OCRed must stay rejected."""
    # letter O read where a zero belongs, and S where a 5 belongs
    assert _REAL_PAN in pi.pan_candidates("PAN AANCPO234D issued")
    assert "AANCP5234D" in pi.pan_candidates("AANCPS234D")
    # digits read where letters belong
    assert "AANCP0234D" in pi.pan_candidates("4ANCP0234D".replace("4", "A"))
    # ...but junk is not massaged into a PAN
    assert pi.pan_candidates("HELLO WORLD") == [], pi.pan_candidates("HELLO WORLD")
    assert pi.pan_candidates("") == []
    assert pi._repair("TOOSHORT") is None
    print("test_ocr_confusions_are_repaired_positionally_not_guessed: PASS")


def test_the_incorporation_date_comes_from_its_own_label():
    """The card prints several dates; only the one under
    "Date of Incorporation / Formation" is the incorporation date."""
    text = "Permanent Account Number Card\nAANCP0234D\nPRANAMI NEEV REALTY LIMITED\n13102022\nDate of Incorporation / Formation\n27/06/2022"
    assert pi._incorporation_date(text) == "2022-06-27", pi._incorporation_date(text)
    assert pi._incorporation_date("no dates here") is None
    # An impossible date is not returned as if it were real.
    assert pi._incorporation_date("Date of Incorporation 45/99/2022") is None
    print("test_the_incorporation_date_comes_from_its_own_label: PASS")


# --- document selection ---------------------------------------------------

def test_the_pan_card_is_found_under_every_states_label_convention():
    """MahaRERA labels it "PAN Card"; GujRERA's label is derived from its
    panCardDocUId field and renders "Pan card doc"; the promoter names the
    upload itself anything at all."""
    here = os.path.dirname(os.path.abspath(__file__))
    manifest = [
        {"label": "PAN Card", "saved_filename": "test_promoter_identity.py", "status": "downloaded"},
        {"label": "Pan card doc", "saved_filename": "test_promoter_identity.py", "status": "reused"},
        {"label": "Other -- Legal", "saved_filename": "test_promoter_identity.py", "status": "downloaded"},
        {"label": "Title Report", "saved_filename": "test_promoter_identity.py", "status": "downloaded"},
    ]
    found = pi.find_identity_documents(manifest, here)
    labels = [d["label"] for d in found]
    assert labels == ["PAN Card", "Pan card doc"], labels
    print("test_the_pan_card_is_found_under_every_states_label_convention: PASS")


def test_documents_that_were_never_retrieved_are_not_offered_for_reading():
    """A listed-but-undownloaded document has no bytes on disk. Treating it
    as readable would produce a spurious "could not be read" finding about a
    file the portal simply never served."""
    here = os.path.dirname(os.path.abspath(__file__))
    manifest = [
        {"label": "PAN Card", "saved_filename": "test_promoter_identity.py", "status": "failed (metadata only, file bytes not served)"},
        {"label": "PAN Card", "saved_filename": None, "status": "downloaded"},
        {"label": "PAN Card", "saved_filename": "does_not_exist_on_disk.pdf", "status": "downloaded"},
    ]
    assert pi.find_identity_documents(manifest, here) == []
    assert pi.find_identity_documents(None, here) == []
    print("test_documents_that_were_never_retrieved_are_not_offered_for_reading: PASS")


def test_no_pan_card_filed_says_so_rather_than_failing():
    result = pi.extract_promoter_pan([], "nowhere", _REAL_NAME)
    assert result["pan"] is None and result["verified"] is False, result
    assert any("No PAN card was found" in n for n in result["notes"]), result
    print("test_no_pan_card_filed_says_so_rather_than_failing: PASS")


# --- the silent-degradation guard ----------------------------------------

def test_a_missing_ocr_binary_is_a_tooling_gap_not_an_absent_document():
    """The bug this exists for: pytesseract shells out to `tesseract` by
    NAME. When the binary is absent it does not raise -- every scanned card
    yields "" and the run reports "no PAN found", which reads in the Charter
    as though the promoter filed no PAN card. Confirmed live: this module's
    first run returned exactly that, because the binary discovery lives in
    company_charter.py and had not been repeated here."""
    here = os.path.dirname(os.path.abspath(__file__))
    manifest = [{"label": "PAN Card", "saved_filename": "test_promoter_identity.py", "status": "downloaded"}]
    original = pi.tesseract_available
    pi.tesseract_available = lambda: False
    try:
        result = pi.extract_promoter_pan(manifest, here, _REAL_NAME)
    finally:
        pi.tesseract_available = original
    assert result["pan"] is None, result
    note = " ".join(result["notes"])
    assert "Tesseract" in note, result["notes"]
    assert "not an absence of the document" in note, result["notes"]
    assert "No PAN card was found" not in note, result["notes"]
    print("test_a_missing_ocr_binary_is_a_tooling_gap_not_an_absent_document: PASS")


def test_tesseract_discovery_matches_company_charter():
    """This module repeats company_charter.py's binary discovery instead of
    importing it (that import would be circular). Lockstep guard: if one
    grows a new candidate path, so must the other."""
    source = io.open("company_charter.py", "r", encoding="utf-8").read()
    mine = io.open("promoter_identity.py", "r", encoding="utf-8").read()
    for path in ("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                 r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        assert path in source, f"{path} vanished from company_charter.py"
        assert path in mine, f"{path} missing from promoter_identity.py -- discovery has drifted"
    print("test_tesseract_discovery_matches_company_charter: PASS")


# --- cropping -------------------------------------------------------------

def test_a_full_page_of_text_is_never_cropped():
    """Cropping exists for a small card on a blank sheet. Applying it to a
    dense page would risk shaving the margins off a document that was
    already OCRing correctly."""
    from PIL import Image

    full = Image.new("L", (400, 400), color=0)  # entirely "ink"
    assert pi._content_crop(full) is None
    blank = Image.new("L", (400, 400), color=255)  # nothing at all
    assert pi._content_crop(blank) is None
    sparse = Image.new("L", (400, 400), color=255)
    for x in range(10, 60):
        for y in range(10, 40):
            sparse.putpixel((x, y), 0)
    crop = pi._content_crop(sparse)
    assert crop is not None and crop.width < 120, crop
    print("test_a_full_page_of_text_is_never_cropped: PASS")


# --- the real artifact ----------------------------------------------------

def test_the_real_filed_pan_card_is_read_end_to_end():
    """The case this module was built for. Whole-page OCR of this exact file
    returns ZERO characters -- the card fills about a sixth of an A4 scan --
    so this proves the crop-and-upscale path, not just the plumbing."""
    if not os.path.exists(_REAL_PDF):
        return _skip(f"{_REAL_PDF} not present")
    if not pi.tesseract_available():
        return _skip("Tesseract binary not installed")

    manifest_path = os.path.join(_REAL_PROJECT, "documents_manifest.json")
    with io.open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    result = pi.extract_promoter_pan(
        manifest, os.path.join(_REAL_PROJECT, "documents"), _REAL_NAME
    )
    assert result["pan"] == _REAL_PAN, result
    assert result["verified"] is True, result
    assert result["holder_type"] == "Company", result
    assert result["incorporation_date"] == "2022-06-27", result
    assert result["source_document"] == "PAN Card", result
    print("test_the_real_filed_pan_card_is_read_end_to_end: PASS")


def test_whole_page_ocr_really_does_fail_on_this_file():
    """Anti-vacuous-pass guard. If plain whole-page OCR ever starts working
    on this artifact, the test above stops proving anything and the crop
    path could be silently dead."""
    if not os.path.exists(_REAL_PDF) or not pi.tesseract_available():
        return _skip("real PAN card artifact or Tesseract unavailable")
    import fitz
    import pytesseract
    from PIL import Image

    with fitz.open(_REAL_PDF) as doc:
        page = doc[0]
        assert not page.get_text().strip(), "the file now has a native text layer"
        pix = page.get_pixmap(dpi=300)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        naive = pytesseract.image_to_string(img)
    assert _REAL_PAN not in naive.upper(), (
        "whole-page OCR now finds the PAN unaided -- the crop path is no longer "
        "what makes test_the_real_filed_pan_card_is_read_end_to_end pass"
    )
    print("test_whole_page_ocr_really_does_fail_on_this_file: PASS")


if __name__ == "__main__":
    test_a_pan_that_belongs_to_someone_else_is_rejected()
    test_an_unverifiable_pan_is_reported_not_dropped_and_not_used()
    test_holder_type_must_be_a_real_pan_code()
    test_a_pan_with_no_promoter_name_is_never_promoted_to_verified()
    test_legal_form_prefixes_do_not_become_the_initial()
    test_a_group_companys_pan_is_rejected_even_though_the_initial_matches()
    test_the_same_entity_still_verifies_across_spelling_differences()
    test_the_holder_name_is_read_off_the_card_not_its_boilerplate()
    test_a_low_contrast_card_gets_a_binarised_second_pass()
    test_ocr_confusions_are_repaired_positionally_not_guessed()
    test_the_incorporation_date_comes_from_its_own_label()
    test_the_pan_card_is_found_under_every_states_label_convention()
    test_documents_that_were_never_retrieved_are_not_offered_for_reading()
    test_no_pan_card_filed_says_so_rather_than_failing()
    test_a_missing_ocr_binary_is_a_tooling_gap_not_an_absent_document()
    test_tesseract_discovery_matches_company_charter()
    test_a_full_page_of_text_is_never_cropped()
    test_the_real_filed_pan_card_is_read_end_to_end()
    test_whole_page_ocr_really_does_fail_on_this_file()
    print("\nAll tests passed.")

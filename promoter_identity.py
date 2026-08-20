"""Reads the promoter's national identifiers out of the documents the RERA
portals already give us.

WHY THIS EXISTS. No RERA portal in India publishes a CIN or a DIN, and only
two of the four built states publish a PAN as a field (TG-RERA and K-RERA).
Maharashtra and Gujarat publish neither -- but BOTH require the promoter to
upload their PAN card, and both serve that upload from their document
libraries, which this pipeline already downloads in full. So the one
identifier that joins RERA to the MCA, GST and income-tax records is sitting
on disk after every run, as a picture, unread.

That matters beyond convenience: without it, re-entering RERA from a
corporate entity is fuzzy name matching, which is exactly the thing
group_entities.py refuses to treat as evidence. A PAN is national and
unique, so it turns a proposed link into a confirmable one.

WHY WHOLE-PAGE OCR RETURNS NOTHING. Confirmed live on the real filing
(`output/P51800077150/documents/PAN of PNRL.pdf`): a PAN card scanned onto
A4 occupies roughly a sixth of the page, so at any sane DPI the glyphs are
tiny relative to the sheet and Tesseract returns the empty string -- not an
error, not garbage, nothing. company_charter._extract_document_text hits
exactly this. Cropping to the scan's content box and upscaling recovers the
number cleanly under psm 6, 4 and 11 alike.

WHAT IS NEVER DONE HERE. A PAN read off a photograph is not asserted as
fact on the strength of the OCR alone. Every candidate is checked against
the card's own structure and against the promoter name the portal already
gave us (see verify_pan), and one that fails is returned as unverified with
the reason attached, never silently dropped and never quietly promoted. A
wrong PAN is worse than no PAN: it would pull another company's charges,
filings and litigation into this promoter's Charter.

Run directly to see what it gets from a downloaded filing:
    python promoter_identity.py output/P51800077150
"""

import io
import json
import os
import re
import shutil

import fitz  # PyMuPDF
import pytesseract
from PIL import Image, ImageOps

# Same Tesseract discovery company_charter.py does at import, repeated rather
# than imported because company_charter imports this module's callers and
# pulling it in here would close the cycle. Kept in lockstep by
# test_promoter_identity.py::test_tesseract_discovery_matches_company_charter.
if not shutil.which("tesseract"):
    for _candidate in (
        os.environ.get("TESSERACT_CMD"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if _candidate and os.path.exists(_candidate):
            pytesseract.pytesseract.tesseract_cmd = _candidate
            break


def tesseract_available():
    """Whether OCR can actually run. A missing binary must be REPORTED, not
    absorbed: the failure mode it otherwise produces -- every scanned card
    reading as "no PAN found" -- is indistinguishable from a promoter who
    genuinely filed no PAN card, and the Charter would record the wrong one
    of those two as a finding."""
    configured = getattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")
    return bool(shutil.which(configured) or os.path.exists(configured))

# Manifest labels/filenames that mean "this file is the promoter's PAN card".
# Deliberately broad: MahaRERA labels it "PAN Card", GujRERA's label is
# derived from its `panCardDocUId` field and comes out "Pan card doc", and
# promoters name the upload itself anything at all ("PAN of PNRL.pdf").
_PAN_DOC_HINTS = ("pan card", "pancard", "pan of", "pan_", "pan-", " pan ", "permanent account")

_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")

# The 4th character of a PAN encodes the holder type. Only these appear on a
# promoter entity; the full set includes several we would not expect to see
# and whose presence is itself worth reporting rather than accepting.
_HOLDER_TYPES = {
    "C": "Company",
    "P": "Individual",
    "H": "Hindu Undivided Family",
    "F": "Firm / LLP",
    "A": "Association of Persons",
    "T": "Trust",
    "B": "Body of Individuals",
    "L": "Local Authority",
    "J": "Artificial Juridical Person",
    "G": "Government",
}

# Legal-form words that must not be mistaken for the entity's initial when
# checking the PAN's 5th character.
_NAME_NOISE = {"M/S", "MS", "THE", "SHRI", "SHREE", "SRI", "MESSRS"}

# Tesseract confuses these in both directions. A PAN's layout is positional
# -- five letters, four digits, one letter -- so a confusion can be repaired
# against the position it landed in rather than guessed at.
_TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "|": "1",
             "Z": "2", "S": "5", "B": "8", "G": "6", "T": "7", "A": "4"}
_TO_LETTER = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G", "4": "A"}

_DATE_RE = re.compile(r"\b(\d{2})[/\-.](\d{2})[/\-.](\d{4})\b")


def find_identity_documents(documents_manifest, documents_dir):
    """Every downloaded file in the manifest that looks like a PAN card.

    Returns [{label, path}] -- a list because a filing can carry the
    promoter's card alongside a land-owner's or a partner's, and which one
    belongs to the promoter is decided by verification, not by ordering.
    """
    out = []
    for entry in documents_manifest or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("status") not in ("downloaded", "reused"):
            continue
        filename = entry.get("saved_filename")
        if not filename:
            continue
        haystack = f" {entry.get('label') or ''} {filename} ".lower().replace("_", " ")
        if not any(hint in haystack for hint in _PAN_DOC_HINTS):
            continue
        path = os.path.join(documents_dir or "", filename)
        if os.path.exists(path):
            out.append({"label": entry.get("label") or filename, "path": path})
    return out


def _content_crop(img, threshold=40, pad=12):
    """Crops a mostly-blank scan down to the ink on it.

    Returns None when the content already fills most of the page -- there is
    nothing to gain then, and cropping a full page of text would only risk
    shaving a margin off it.
    """
    grey = img.convert("L")
    mask = ImageOps.invert(grey).point(lambda p: 255 if p > threshold else 0)
    box = mask.getbbox()
    if not box:
        return None
    page_area = img.width * img.height
    crop_area = (box[2] - box[0]) * (box[3] - box[1])
    if not page_area or crop_area / page_area > 0.7:
        return None
    box = (max(0, box[0] - pad), max(0, box[1] - pad),
           min(img.width, box[2] + pad), min(img.height, box[3] + pad))
    return grey.crop(box)


_BINARISE_THRESHOLD = 120

# Tesseract's cost scales with pixels, and this ladder runs six passes per
# page. Above roughly this many, a scan is being handed far more detail than
# OCR can use.
_MAX_OCR_PIXELS = 4_000_000


def _prepare_for_ocr(image, upscale):
    """Size an image so OCR is both accurate and finite.

    THIS FUNCTION EXISTS BECAUSE THE UPSCALE HUNG A REAL RUN. Upscaling was
    added for a card occupying a sixth of a blank A4, where the glyphs are
    genuinely tiny. It was then applied to EVERY page, including a card that
    fills the sheet: a 400-dpi A4 is 15.5 megapixels, doubling it gives 62,
    and six OCR passes over that ran for hours without finishing. Four
    background jobs had to be killed.

    Worse, it was not even helping. On the card that fills the page the PAN
    was recovered at 150 dpi and NOT at 300 or 400: past a point the extra
    pixels only feed the pastel background's noise. So upscaling is now
    applied ONLY to a crop -- the small-region case it was written for --
    and any image still over the pixel budget is scaled DOWN.
    """
    if upscale > 1:
        image = image.resize((image.width * upscale, image.height * upscale), Image.LANCZOS)
    pixels = image.width * image.height
    if pixels > _MAX_OCR_PIXELS:
        factor = (_MAX_OCR_PIXELS / pixels) ** 0.5
        image = image.resize(
            (max(1, int(image.width * factor)), max(1, int(image.height * factor))),
            Image.LANCZOS,
        )
    return image


def _image_variants(image):
    """The prepared image, then a hard black-and-white version of it.

    WHY THE SECOND ONE EXISTS. A PAN card is printed as dark text over a
    pastel blue-to-pink GRADIENT. Converted to greyscale, the darker end of
    that background lands close to the ink, and Tesseract reads the card as
    noise: on a real JHARERA filing it returned two thousand characters of
    rubbish and neither the company name nor the number, at every DPI and
    every page-segmentation mode tried.

    A hard threshold throws the gradient away and recovers both outright.
    It is the same fix the land-record work needs for the tiled "For View
    Only" watermark, and for the same reason: the obstacle is background
    tone, not resolution, so scaling and sharpening cannot help.

    Both variants are OCRed rather than choosing between them, because
    thresholding destroys faint-but-real text on a CLEAN scan, which is the
    case the plain pass handles best. Cost is one extra page of OCR on a
    one-page card.
    """
    yield image
    yield image.convert("L").point(lambda p: 0 if p < _BINARISE_THRESHOLD else 255)


def ocr_document_text(path, dpi=400, upscale=2, stop_when=None):
    """OCR text for a scanned document, cropping each page to its content.

    This is the narrow, high-effort path -- not a replacement for
    company_charter._extract_document_text, which handles whole libraries
    and must stay cheap. Never raises; returns "" if the file cannot be
    read at all.

    `stop_when` is an optional predicate over the text accumulated so far.
    It exists so a caller that already has what it needs does not pay for
    the remaining passes: the full ladder is two image variants across
    three segmentation modes, six OCR runs per page, and only a
    pathological scan needs all of them.
    """
    chunks = []
    try:
        with fitz.open(path) as doc:
            for page in doc:
                native = page.get_text().strip()
                if native:
                    chunks.append(native)
                    continue
                try:
                    pix = page.get_pixmap(dpi=dpi)
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                except Exception:
                    continue
                cropped = _content_crop(img)
                target = _prepare_for_ocr(cropped or img.convert("L"),
                                          upscale if cropped is not None else 1)
                for variant in _image_variants(target):
                    for psm in (6, 4, 11):
                        try:
                            text = pytesseract.image_to_string(variant, config=f"--psm {psm}")
                        except Exception:
                            continue
                        if text and text.strip():
                            chunks.append(text)
                            # Short-circuit once the caller has what it came
                            # for. Trying both image variants across three
                            # segmentation modes is SIX OCR passes per page,
                            # and the common case -- a clean scan read
                            # correctly on the first -- should not pay for
                            # the pathological one.
                            if stop_when is not None and stop_when("\n".join(chunks)):
                                return "\n".join(chunks).strip()
                            # Keep going: the PSMs segment differently and a
                            # PAN missed by one is often found by another.
                            # Cheap enough on a single card, and candidate
                            # selection downstream dedupes.
    except Exception:
        return ""
    return "\n".join(chunks).strip()


def _repair(token):
    """Coerces an 10-character token into PAN shape using the known layout.

    Only substitutions from the confusion table are made, so a token that is
    not simply mis-OCRed stays broken and is rejected downstream.
    """
    token = token.strip().upper()
    if len(token) != 10:
        return None
    chars = list(token)
    for i in range(0, 5):
        chars[i] = _TO_LETTER.get(chars[i], chars[i])
    for i in range(5, 9):
        chars[i] = _TO_DIGIT.get(chars[i], chars[i])
    chars[9] = _TO_LETTER.get(chars[9], chars[9])
    candidate = "".join(chars)
    return candidate if _PAN_RE.fullmatch(candidate) else None


def pan_candidates(text):
    """Every distinct PAN-shaped token in `text`, in order of appearance,
    including ones recovered by repairing OCR confusions."""
    if not text:
        return []
    upper = text.upper()
    seen, out = set(), []
    for match in _PAN_RE.finditer(upper):
        if match.group(0) not in seen:
            seen.add(match.group(0))
            out.append(match.group(0))
    for token in re.findall(r"\b[A-Z0-9|]{10}\b", upper):
        repaired = _repair(token)
        if repaired and repaired not in seen:
            seen.add(repaired)
            out.append(repaired)
    return out


_LEADING_PREFIX_RE = re.compile(r"^\s*(?:M\s*/\s*S\.?|MESSRS\.?|THE|SHRI|SHREE|SRI)\b[\s.,-]*", re.I)


def _name_initial(name):
    """The first letter of the entity's own name.

    Punctuated prefixes have to be stripped BEFORE tokenising, not filtered
    after: splitting "M/S PRANAMI..." on non-letters yields "M", "S",
    "PRANAMI", and "M" is not the string "M/S" that the noise list would
    have caught -- so the check would compare the PAN against "M" and
    reject a perfectly good number.
    """
    text = (name or "").upper()
    while True:
        stripped = _LEADING_PREFIX_RE.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    for word in re.split(r"[^A-Z]+", text):
        if word and word not in _NAME_NOISE:
            return word[0]
    return None


# Wording printed on every PAN card, which must never be mistaken for the
# holder's name.
_CARD_BOILERPLATE = (
    "INCOME TAX DEPARTMENT", "GOVT", "GOVERNMENT OF INDIA", "PERMANENT ACCOUNT",
    "ACCOUNT NUMBER", "DATE OF INCORPORATION", "FORMATION", "SIGNATURE",
    "NAME", "FATHER", "DATE OF BIRTH",
)
_LEGAL_FORM_WORDS = {
    "PRIVATE", "PVT", "LIMITED", "LTD", "LLP", "LLC", "COMPANY", "CO",
    "CORPORATION", "INCORPORATED", "AND", "&",
}


def card_holder_name(text):
    """The entity name printed on the card, or None.

    Taken as the longest all-caps line that is not the card's own
    boilerplate. Both real layouts put the holder's name on its own line in
    capitals: the current card places it under "Name", the older one places
    it above the date of incorporation.
    """
    if not text:
        return None
    best = None
    for raw in text.splitlines():
        line = raw.strip(" |_-\t")
        if len(line) < 6 or not re.search(r"[A-Z]{3}", line):
            continue
        letters = re.sub(r"[^A-Za-z ]", "", line).strip()
        if len(letters) < 6:
            continue
        # Capitals only: the holder's name is printed in caps on every card.
        if letters != letters.upper():
            continue
        upper = letters.upper()
        if any(marker in upper for marker in _CARD_BOILERPLATE):
            continue
        if best is None or len(upper) > len(best):
            best = upper
    return best


def _distinctive_tokens(name):
    tokens = [t for t in re.split(r"[^A-Za-z]+", (name or "").upper()) if t]
    return [t for t in tokens if t not in _LEGAL_FORM_WORDS and t not in _NAME_NOISE and len(t) > 1]


def names_agree(card_name, promoter_name):
    """Whether the card and the portal are naming the SAME entity.

    Compared on distinctive tokens, with legal-form words dropped, because
    "PRANAMI NEEV REALTY LIMITED" and "Pranami Neev Realty Ltd" are the same
    company while "PRANAMI BUILDERS PRIVATE LIMITED" and "PBPL PRANAMI CREST
    RERA PRIVATE LIMITED" are NOT -- they merely share a brand word, and
    they are the real case this exists for.

    One name being a subset of the other counts as agreement, since OCR
    routinely drops a word and portals routinely abbreviate one.
    """
    card = set(_distinctive_tokens(card_name))
    portal = set(_distinctive_tokens(promoter_name))
    if not card or not portal:
        return None
    if card == portal or card <= portal or portal <= card:
        return True
    return False


def verify_pan(pan, promoter_name=None, card_name=None):
    """Checks a candidate PAN against its own structure and the promoter
    name the portal already published.

    Two independent signals, both free:
      * character 4 is the holder type -- a company's PAN carries "C", a
        firm's "F". An unknown letter here means the read is wrong.
      * character 5 is the first letter of the entity name. This is the
        real check: it ties the number to THIS promoter rather than merely
        confirming it is a well-formed PAN.

    Returns {ok, holder_type, checks[], reason}. `ok` False never means
    "no PAN" -- it means this candidate was not confirmed, and `reason`
    says which check failed.
    """
    result = {"pan": pan, "ok": False, "holder_type": None, "checks": [], "reason": None}
    if not pan or not _PAN_RE.fullmatch(pan):
        result["reason"] = "not a structurally valid PAN"
        return result

    holder = _HOLDER_TYPES.get(pan[3])
    result["holder_type"] = holder
    if not holder:
        result["reason"] = f"4th character {pan[3]!r} is not a recognised PAN holder-type code"
        return result
    result["checks"].append(f"holder-type character {pan[3]!r} = {holder}")

    initial = _name_initial(promoter_name)
    if initial is None:
        # Structurally sound but unattributed. Reported as such: it is a
        # PAN, but nothing here ties it to this promoter.
        result["reason"] = "no promoter name available to check the 5th character against"
        return result
    if pan[4] != initial:
        result["reason"] = (
            f"5th character {pan[4]!r} does not match the promoter name's initial "
            f"{initial!r} ({promoter_name!r}) -- this PAN belongs to someone else"
        )
        return result

    result["checks"].append(f"5th character {pan[4]!r} matches promoter name initial")

    # The strongest check available, and the one the initial test cannot
    # make. Confirmed live on a real JHARERA filing: the card on file read
    # PRANAMI BUILDERS PRIVATE LIMITED while the promoter of record was
    # PBPL PRANAMI CREST RERA PRIVATE LIMITED. Both begin with P, so every
    # structural check above passes, and the number would have been
    # attributed to the wrong company -- pulling the PARENT's charges,
    # filings and litigation into an SPV's record. Same brand is not the
    # same entity, which is the rule group_entities.py exists to enforce.
    if card_name:
        agree = names_agree(card_name, promoter_name)
        if agree is False:
            result["reason"] = (
                f"the card on file is issued to {card_name.title()}, but the promoter of record "
                f"for this project is {promoter_name}. These are different entities, so this "
                f"number is not this promoter's"
            )
            result["card_name"] = card_name
            return result
        if agree:
            result["checks"].append(f"the card names {card_name.title()}, matching the promoter of record")

    result["ok"] = True
    return result


def _incorporation_date(text):
    """The card prints the date of incorporation/formation directly under
    the name. Returned ISO, or None -- never inferred from anything else."""
    if not text:
        return None
    window = text.upper()
    anchor = window.find("INCORPORATION")
    if anchor == -1:
        anchor = window.find("FORMATION")
    hunt = text[anchor:] if anchor != -1 else text
    match = _DATE_RE.search(hunt) or _DATE_RE.search(text)
    if not match:
        return None
    day, month, year = match.groups()
    if not (1 <= int(month) <= 12 and 1 <= int(day) <= 31):
        return None
    return f"{year}-{month}-{day}"


def extract_promoter_pan(documents_manifest, documents_dir, promoter_name=None):
    """The one entry point. Reads the promoter's PAN out of the filed PAN
    card, verified against `promoter_name`.

    Returns a dict that always states what happened -- found or not, and on
    what evidence -- so the Charter can cite it or record the gap. Never
    raises: this runs inside a pipeline where a missing OCR binary or a
    corrupt upload must not end the run.
    """
    out = {
        "pan": None,
        "holder_type": None,
        "incorporation_date": None,
        "source_document": None,
        "verified": False,
        # A machine-readable outcome, because callers need to tell these
        # cases apart and the notes below are PROSE. An earlier version made
        # a caller sniff the note text for "could not be read", which also
        # matched the no-card-filed note ("...so the promoter's PAN could not
        # be read from the RERA filing") and silently merged two outcomes
        # that call for opposite handling. One of them is a gap; the other
        # must stay silent.
        #   verified | unverified_candidate | unreadable | ocr_unavailable | no_card
        "status": "no_card",
        "checks": [],
        "notes": [],
        "unverified_candidates": [],
    }

    documents = find_identity_documents(documents_manifest, documents_dir)
    if not documents:
        out["notes"].append(
            "No PAN card was found in this authority's document library for this project, "
            "so the promoter's PAN could not be read from the RERA filing."
        )
        return out

    if not tesseract_available():
        # Stop here rather than reporting "no PAN found" from an OCR pass
        # that could not run. The card IS present; only the reader is
        # missing, and those are different findings.
        out["notes"].append(
            f"{len(documents)} PAN card document(s) were filed and downloaded, but OCR could "
            "not run because the Tesseract binary was not found, so the promoter's PAN was "
            "not read. This is a tooling gap, not an absence of the document."
        )
        out["status"] = "ocr_unavailable"
        return out

    for document in documents:
        # Stop early only once EVERYTHING this function reads off the card
        # is present: the number, the holder's name and the incorporation
        # date. Stopping on the number alone silently dropped the date --
        # a later pass was what read it -- so the optimisation quietly cost
        # a field. A predicate for a short-circuit has to cover every
        # output, not just the one that motivated it.
        text = ocr_document_text(
            document["path"],
            stop_when=lambda t: bool(pan_candidates(t))
            and bool(_incorporation_date(t))
            and bool(card_holder_name(t)),
        )
        if not text:
            out["notes"].append(
                f"{document['label']}: the file could not be read as text or by OCR."
            )
            continue

        candidates = pan_candidates(text)
        if not candidates:
            out["notes"].append(f"{document['label']}: no PAN-shaped text found on the document.")
            continue

        holder = card_holder_name(text)
        for candidate in candidates:
            verdict = verify_pan(candidate, promoter_name, card_name=holder)
            if verdict["ok"]:
                out["pan"] = candidate
                out["holder_type"] = verdict["holder_type"]
                out["source_document"] = document["label"]
                out["verified"] = True
                out["checks"] = verdict["checks"]
                out["incorporation_date"] = _incorporation_date(text)
                return out
            out["unverified_candidates"].append(
                {"pan": candidate, "document": document["label"],
                 "reason": verdict["reason"], "card_name": verdict.get("card_name")}
            )

    out["status"] = "unverified_candidate" if out["unverified_candidates"] else "unreadable"
    if out["unverified_candidates"]:
        out["notes"].append(
            "A PAN-shaped value was read from the filed card but could not be tied to this "
            "promoter, so it is recorded as unconfirmed rather than used: "
            + "; ".join(f"{c['pan']} ({c['reason']})" for c in out["unverified_candidates"][:3])
        )
    return out


if __name__ == "__main__":
    import sys

    project_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join("output", "P51800077150")
    manifest_path = os.path.join(project_dir, "documents_manifest.json")
    with io.open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    name = None
    partners_path = os.path.join(project_dir, "raw", "partners.json")
    if os.path.exists(partners_path):
        with io.open(partners_path, "r", encoding="utf-8") as f:
            name = ((json.load(f) or {}).get("promoterDetails") or {}).get("promoterName")

    print(f"promoter name from the portal: {name!r}")
    result = extract_promoter_pan(manifest, os.path.join(project_dir, "documents"), name)
    print(json.dumps(result, indent=2, ensure_ascii=False))

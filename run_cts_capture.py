"""
One live Property Card fetch, for a human at the keyboard.

WHAT THIS IS FOR. The Property Card parser (mahabhumi.parse_property_card)
was written against a captured SCREENSHOT of a real card, so its Marathi
labels are right but the exact table nesting has never been checked against
the live page's HTML. This runs one lookup and reports whether `fields`
comes back populated instead of the empty dict every lookup in this repo
has produced so far. That single result decides whether the land-record
workflow is finished or needs more work.

WHAT IT NEEDS FROM YOU, and why none of it can be automated:

  * A CAPTCHA SOLVE. The site regenerates its CAPTCHA on every partial
    postback and grants no reusable session, so one solve is required per
    lookup. A visible browser opens; read it, type it, click Submit.
  * YOUR MOBILE NUMBER, passed as an argument. The portal requires one and
    it is submitted to a government site, so it is yours to supply
    deliberately rather than something stored in this repo.

Everything else is already filled in by the time the window opens.

    python run_cts_capture.py <mobile> [cts_number]

Defaults to the one record this repo has a saved screenshot for -- CTS 183,
village Ambivali, office "Nagar Bhumapan Adhikari, Andheri", Mumbai
Suburban -- so the result can be checked field by field against a picture
of the same card rather than taken on trust.
"""

import io
import json
import os
import sys

import mahabhumi

DISTRICT = "Mumbai Suburban"
OFFICE = "नगर भूमापन अधिकारी,अंधेरी"
VILLAGE = "आंबिवली"
DEFAULT_CTS = "183"

OUT_DIR = os.path.join("output", "_pending", "cts_capture")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        print("[!] Pass your mobile number: python run_cts_capture.py <mobile> [cts_number]")
        return 2

    mobile = sys.argv[1].strip()
    cts_number = (sys.argv[2].strip() if len(sys.argv) > 2 else DEFAULT_CTS)
    os.makedirs(OUT_DIR, exist_ok=True)
    screenshot = os.path.join(OUT_DIR, "card.png")

    print(f"  district : {DISTRICT}")
    print(f"  office   : {OFFICE}")
    print(f"  village  : {VILLAGE}")
    print(f"  CTS      : {cts_number}")
    print()
    print("A browser window will open with everything above already filled in.")
    print("Read the CAPTCHA, type it, and click Submit. Then leave the window alone.")
    print()

    try:
        result = mahabhumi.fetch_property_card(
            DISTRICT, OFFICE, VILLAGE, cts_number, mobile, screenshot_path=screenshot
        )
    except Exception as e:
        print(f"[!] The fetch did not complete: {type(e).__name__}: {e}")
        return 1

    with io.open(os.path.join(OUT_DIR, "card.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, ensure_ascii=False)

    fields = result.get("fields") or {}
    mutations = result.get("mutations") or []

    print()
    print("=" * 62)
    # THE ONE THING THIS RUN EXISTS TO ANSWER.
    print(f"  fields parsed off the card : {len(fields)}")
    print(f"  mutation entries           : {len(mutations)}")
    print(f"  raw page text              : {len(result.get('raw_text') or '')} chars")
    print(f"  OCR text (fallback only)   : {len(result.get('ocr_text') or '')} chars")
    print("=" * 62)

    if not fields:
        print()
        print("  fields is EMPTY -- the card's HTML still is not reaching the parser.")
        print(f"  Compare {screenshot} against the raw text in card.json to see what")
        print("  the page actually returned.")
        return 1

    print()
    for key, value in fields.items():
        print(f"    {key:<22} {value!r}")
    for entry in mutations:
        print(f"    mutation               {entry}")
    print()
    print(f"  Saved: {os.path.join(OUT_DIR, 'card.json')}")
    print(f"         {screenshot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

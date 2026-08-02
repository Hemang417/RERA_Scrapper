"""
One-off build script assembling the biographical/group research gathered
this session into the shape charter_report.build_charter_report expects,
then building both Internal and External docx variants.

Not a reusable pipeline component -- the research dict below is hand
transcribed from web-research agent output, curated for confidence
(self-reported vs independently corroborated, ruled-out name collisions
kept only in the internal variant). Delete or rewrite per engagement.
"""

import json

import charter_report as cr

_PRANAMI_FACTS = "output/company_charters/Company_Charter_PranamiBliss_P51800077150.facts.json"
_REG_NO = "P51800077150"
_GENERATED_ON = "30 July 2026"

RESEARCH = {
    "directors": {
        "Bijay Kumar Agarwal": {
            "other_directorships_count": 14,
            "education": [
                ("Commerce graduate, Ranchi University.", "https://pranamigroup.com/about/"),
            ],
            "career": [
                ("Founded Pranami Group in 2002; Founder, Chairman and Managing Director.", "https://pranamigroup.com/about/"),
                ("Vice-President, CREDAI Ranchi (the Ranchi chapter of the developers' industry association).", "https://pranamigroup.com/about/"),
                ("No pre-2002 employment history or founding backstory is disclosed in public sources searched.", None),
            ],
            "positive": [
                ("Awarded \"Star Citizen Honor, 2013\" by Citizen Club, Ranchi.", "https://pranamigroup.com/about/"),
            ],
            "adverse": [],
            "identity_notes": [
                ("Name-collision risk is high for this name. Confirmed NOT this person: Bijay Agarwal, MD of Salarpuria Sattva Group, Bangalore (a distinct, more press-prominent individual behind an unrelated CNBC-TV18 interview and YourStory profile); Bijay Kumar Agrawal DIN 07815618; DINs 00437382, 00088987 and 02642212 also registered to a \"Bijay Kumar Agarwal\" but unconfirmed as this subject; Bijay Kumar Agarwalla the physicist and an unrelated Supreme Court case styled Bijay Agarwal v. Medilines.", None),
                ("Public-source coverage of this individual consists almost entirely of Pranami Group's own website plus registry aggregators; the \"founder\" claim itself has no independent corroboration beyond the company's self-reporting.", None),
            ],
        },
        "Nitish Kumar Agarwal": {
            "other_directorships_count": 10,
            "education": [],
            "career": [
                ("Director, Mall of Ranchi Private Limited, Ranchi, appointed 2020-07-20.", "https://www.indiafilings.com/search/mall-of-ranchi-private-limited-cin-U93000JH2020PTC014638"),
                ("Listed under \"Our Leadership\" on Pranami Group's own About page, alongside Bijay Kumar Agarwal, with no title or biography given.", "https://pranamigroup.com/about/"),
                ("A search-engine aggregator snippet describes him as \"Executive Director at Pranami Group and Mall of Ranchi\"; treated as low-confidence since the underlying page could not be independently confirmed.", "https://www.zoominfo.com/p/Nitish-Agarwal/8772091291"),
            ],
            "positive": [],
            "adverse": [],
            "identity_notes": [
                ("DIN 02750231 confirmed via registry crosswalk to Pranami Builders Pvt Ltd, Pranami Estates Pvt Ltd, Pranami Neev Realty Ltd, Mall of Ranchi Pvt Ltd, Fairmine Resources Ltd, Fairmine Carbons Pvt Ltd and Sangbi Construction, consistent with the entity list already known.", "https://www.indiafilings.com/search/nitish-kumar-agarwal-din-02750231"),
                ("A Ranchi mall/Enforcement Directorate land-scrutiny matter surfaced in search results concerns Nucleus Mall and a different individual, Vishnu/Bishnu Agrawal; confirmed unrelated to this subject or to Mall of Ranchi.", None),
                ("Family relationship to Bijay Kumar Agarwal is plausible from repeated co-directorship across Pranami Builders, Pranami Estates, Pranami Neev Realty and Mall of Ranchi, but no source states the relationship explicitly; remains unconfirmed.", None),
                ("Other same-name individuals found and explicitly ruled out: an Orion Capital Asia CEO/CIO named Nitish Agarwal; a separate \"Nitish Agrawal, Founder and Managing Director\"; and DIN 02675983 registered to a different \"Nitish Kumar Agrawal.\"", None),
            ],
        },
        "Sundeep Poddar": {
            "other_directorships_count": 6,
            "education": [],
            "career": [
                ("Company records list him jointly with Nitish Kumar Agarwal, Vijay Kumar Mohta and Bijay Kumar Agarwal as an owner of Pranami Neev Realty Limited.", "https://www.zaubacorp.com/PRANAMI-NEEV-REALTY-LIMITED-U70109MH2022PLC385473"),
                ("Not named anywhere on Pranami Group's own \"About\" / leadership page, unlike Bijay Kumar Agarwal and Nitish Kumar Agarwal.", "https://pranamigroup.com/about/"),
                ("No job title, role description or prior employer could be found in any source searched (LinkedIn, ZoomInfo, RocketReach); the project's own site could not be reached to check for a listed project team.", None),
            ],
            "positive": [],
            "adverse": [],
            "identity_notes": [
                ("Name-collision risk is high. Confirmed NOT this person: an academic Deputy Vice Chancellor at Lincoln University College, Malaysia; DIN 00486954 and DIN 07864347 registered to unrelated same-name directors; several unrelated \"Sandeep Poddar\" LinkedIn profiles at other companies; and the Poddar Housing fraud/Supreme Court matter, which concerns an entirely unrelated company and different individuals.", None),
                ("Beyond the confirmed registry-level directorship, no independent public-record trace of this individual (education, career narrative, press mentions) could be found under his own name -- itself worth noting as a low public-footprint profile for someone holding the company's authorised-signatory role.", None),
            ],
        },
    },
    "group": {
        # No NCLT filing, High Court case, consumer forum complaint or RERA order naming any Pranami entity
        # was found in public sources searched, beyond the MahaRERA/IBBI checks already reported in section 2 --
        # a clean result, so nothing is listed here (see this module's find-first editorial policy).
        "corporate_litigation": [],
        # Stays in the main Portfolio and Track Record narrative: informative context with its own caveat
        # already stated, not a "this could not be done" gap.
        "track_record_corroboration": [
            ("Real-estate listing aggregators do name eight specific prior projects (M.R. Tower, Green Park, "
             "Mangal Tower, Mangaldeep Apartment, Paramsukh Apartment, Pranami Santushti, Pranami RS Tower and "
             "Pranami Green Residency), though this is builder-supplied directory content rather than "
             "independently reported delivery.", "https://www.99acres.com/pranami-rs-tower-lalpur-ranchi-npxid-c2533"),
        ],
        # Moves to Needs Attention, Company: each of these is a check that could not be completed this pass,
        # not a routine self-reported caveat.
        "track_record_gaps": [
            ("No independent, non-affiliated press coverage of the group's self-reported scale (24+ projects, "
             "5 million-plus sq ft) could be found; every outlet carrying that figure is a listing aggregator "
             "republishing builder-supplied content, or wire-service syndication of the same August 2023 press "
             "release announcing the Integrow investment.", "https://www.prnewswire.com/in/news-releases/integrow-partners-with-pranami-group-will-invest-rs-225-crore-in-multiple-landmark-real-estate-projects-301892504.html"),
            ("A rating-agency document exists for Pranami Estates Private Limited (Infomerics, 27 December "
             "2023), independent third-party financial commentary, but its contents were not extracted this "
             "pass.", "https://infomericstorage.blob.core.windows.net/uploads/pr_Pranami_Estates_27dec23_4fba2d0fca.pdf"),
        ],
        "collateral_discrepancies": [
            ("Independent RERA-aggregator listings (Dwello, SquareYards) show this project under RERA number "
             "A51800000454, differing from the P51800077150 registration number used throughout this Charter "
             "(taken directly from MahaRERA's own live record). Flagged as an unresolved discrepancy rather than "
             "a confirmed error on either side; it may reflect a different registration phase or an aggregator "
             "indexing mistake.", "https://dwello.in/view/pranami-bliss-by-pranami-neev-realty-at-andheri_baae9dd3-0abe-4516-a823-29bc133d4ecc"),
        ],
    },
    # A past director whose related-entity footprint is material enough to warrant his own subsection under
    # The Promoters, even though he no longer holds a current role. The "no adverse news coverage" finding is
    # deliberately NOT listed here: it's a clean result, and this module only flags what was found.
    "past_director_name": "Vijay Kumar Mohta",
    "past_director_summary": (
        "A past director, Vijay Kumar Mohta (DIN 09282951), was appointed at incorporation and ceased "
        "2022-09-22, shortly after Pranami Neev Realty Limited was formed. He separately links to at least 17 "
        "other companies via shared directorship, most in Assam and Northeast India carbon and minerals "
        "businesses, a different sector from real estate."
    ),
    "past_director_findings": [
        ("Per a registry aggregator's search-result data (the source page itself returned a 403 error and could "
         "not be fully fetched), DIN 09282951 associates with approximately 27 companies, not the 17 identified "
         "via this Charter's own shared-director crosswalk; the difference may reflect snapshot date or how "
         "resigned or dissolved entities are counted, and is not independently reconciled.", "https://www.zaubacorp.com/VIJAY-KUMAR-MOHTA-09282951"),
        ("An NCLT Guwahati Bench matter (C.P. No. 20/GB/2022) and a separate Gauhati High Court case reference "
         "Guwahati Carbon Limited, one of the 17 linked entities, but the case documents could not be read in "
         "full and do not confirm whether Vijay Kumar Mohta is personally named as a party or respondent; this "
         "is an open item, not a confirmed adverse finding.", "https://nclt.gov.in/gen_pdf.php?filepath=%2FEfile_Document%2Fncltdoc%2Fcasedoc%2F1806125001512022%2F04%2FOrder-Challenge%2F04_order-Challange_004_1669197280519067672637dede023413.pdf"),
    ],
    "additional_next_steps": [
        "Reconcile the RERA registration number discrepancy between MahaRERA's own record (P51800077150) and "
        "third-party aggregator listings (A51800000454) for this project.",
        "Confirm whether Vijay Kumar Mohta is named as a party or respondent in the NCLT Guwahati Bench matter "
        "(C.P. No. 20/GB/2022) or the related Gauhati High Court case referencing Guwahati Carbon Limited.",
    ],
}


def main():
    with open(_PRANAMI_FACTS, encoding="utf-8") as f:
        base_facts = json.load(f)

    for variant in ("internal", "external"):
        out = f"output/company_charters/Company_Charter_PranamiBliss_CounterpartyPromotersCollateral_{variant.capitalize()}.docx"
        facts = json.loads(json.dumps(base_facts))
        cr.build_charter_report(_REG_NO, facts, RESEARCH, out, variant, _GENERATED_ON)
        print(f"{variant} built -> {out}")


if __name__ == "__main__":
    main()

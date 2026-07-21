---
name: maharera-report
description: Generates a structured PDF report -- MahaRERA project details, a promoter track-record & deep-research profile, macro/micro market research, and the source documents -- for a MahaRERA-registered real estate project, given its registration number (format like P51700022416 or P51800004408) or its project name. Use this skill whenever the user gives a MahaRERA/RERA registration number or project name and wants project details, a project report, professionals, partners, SPOCs, SRO details, past experience, complaint or appeal history, the project's documents, a promoter profile/background check, or market research on the project's location -- even if they just paste a bare registration number with no further explanation, or say something like "scrape this RERA number", "pull the MahaRERA report for P51...", "find this project by name", "research this promoter", or "get me the documents for this project". This skill only applies to Maharashtra RERA (MahaRERA) projects, not other states' RERA portals.
---

# MahaRERA project report

This wraps the scraper already built in this repo (`main.py`, `resolver.py`, `session_auth.py`, `api_client.py`, `promoter_portfolio.py`, `report.py`, `finalize_report.py`). Don't reimplement any of that logic -- just drive it via the commands below, and do the research step yourself (that part genuinely needs you, not a script).

## Background you need to know before running anything

MahaRERA's public API has two tiers:
- **`projects` and `complaints`** need no authentication at all. They always work, automatically.
- **The other 7 categories** (`spocs`, `professionals`, `partners`, `past_experiences`, `sro_details`, `documents`, `appeals`) need a short-lived (~100 minute) guest session. That session sits behind a real, homegrown CAPTCHA on the project's detail page (`maharerait.maharashtra.gov.in/public/project/view/<id>`) -- not reCAPTCHA/hCaptcha, just a distorted-text image and a Submit button.

`main.py` handles this automatically by default: if there's no cached session, it opens a **real, visible browser window** on the project's detail page and waits for a human to solve the CAPTCHA shown there, then reads the resulting session token itself and continues -- no manual DevTools copy-pasting needed. **The human must still solve the actual CAPTCHA by hand in that window; nothing here bypasses it.** Sessions are cached for ~90 minutes (`.guest_token_cache.json` at the project root), so most lookups after the first one within that window need no browser at all.

`main.py` also **always** builds a **Promoter Profile** (unconditional, no flag needed): it searches MahaRERA's own "Promoters" tab for every project registered under the same promoter, and aggregates complaint/appeal counts across all of them -- entirely from RERA's own data, no research needed for this part.

What `main.py` *cannot* do on its own: **Market Research (Macro + Micro)** and the promoter's **external** profile (reputation, corporate background, financial signals, news) require actually researching the web -- there's no single API for this. That's always your job as a standing part of this skill (see Step 3), not something to skip or ask permission for.

## Steps

### 1. Get the registration number or project name
Either works as the first argument -- a reg no looks like `P` followed by 11 digits (e.g. `P51700022416`); anything else is treated as a free-text project name search, which can match more than one project.

### 2. Run the scraper
```
python main.py "<REG_NO_OR_PROJECT_NAME>"
```
- If given a project name that matches multiple projects, the script lists the candidates and either prompts interactively (if run in a real terminal) or exits with the candidate list printed -- if that happens, re-run with the exact registration number shown in the list.
- If no session is cached, a Chromium window will pop up on the user's screen pointed at the CAPTCHA gate. Tell the user, concretely: "A browser window has opened -- please solve the CAPTCHA shown there and click Submit; the script will continue automatically once you do." Then just wait for the command to finish; do not try to solve or read the CAPTCHA yourself.
- This produces `output/<REG_NO>/<REG_NO>_summary.pdf`, raw JSON per category in `output/<REG_NO>/raw/`, downloaded documents in `output/<REG_NO>/documents/`, and the RERA-native promoter portfolio in `output/<REG_NO>/promoter/portfolio.json`.

If the user explicitly doesn't want a browser opened (e.g. they're not at the machine to solve a CAPTCHA right now), add `--no-auto-auth` -- this sticks to the 2 free categories (`projects`, `complaints`) only, no browser, no prompting. (The Promoter Profile section will just be based on less data in that case, since some of its inputs also need the session.)

### 3. Do the deep research (always -- this is the part that needs you)
Read `output/<REG_NO>/raw/projects.json` and `output/<REG_NO>/raw/partners.json` for context (location/pincode/district/taluka, project type, promoter name), and `output/<REG_NO>/promoter/portfolio.json` for the promoter's RERA track record so far. Then research, using web search and whatever public sources are relevant:

- **Macro market research**: city/region-level real estate trends -- price trends, demand/supply, regulatory environment, major market reports (e.g. Anarock, Knight Frank, JLL, CBRE, RBI/NHB data).
- **Micro market research**: the project's specific locality -- comparable projects nearby, connectivity/upcoming infrastructure, local price trends, demand drivers specific to that pincode/taluka.
- **Promoter external profile**: reputation, news/controversies, corporate background (MCA/corporate registry lookups where relevant), financial health signals, delivery track record beyond what RERA itself shows.

**For every data point you go looking for, if the first source doesn't answer it, try at least one more source or query before giving up.** When something genuinely can't be confirmed after multiple honest attempts, record it verbatim in that section's `gaps` list -- never fabricate a plausible-sounding figure to fill a gap.

Write your findings to `output/<REG_NO>/research/deep_research.json` in this shape (every key optional, `report.py` never crashes on missing fields):
```json
{
  "macro_market": {
    "summary": "1-3 sentence topline",
    "sections": [{"heading": "...", "body": "..."}],
    "sources": [{"claim": "...", "url": "...", "publisher": "...", "accessed_date": "YYYY-MM-DD"}],
    "gaps": ["specific data point you couldn't confirm, after trying more than one source"]
  },
  "micro_market": { "...same shape, scoped to the project's locality..." },
  "promoter_external": { "...same shape..." }
}
```

### 4. Rebuild the PDF with the research included
```
python finalize_report.py <REG_NO>
```
This is a zero-network, zero-Playwright rebuild -- it just reloads everything already saved on disk (including the `deep_research.json` you just wrote) and regenerates the same PDF with the Market Research and full Promoter Profile sections populated. Always run this after Step 3, every time -- the PDF from Step 2 alone is deliberately incomplete.

### 5. Report back
Tell the user, concretely:
- The PDF path: `output/<REG_NO>/<REG_NO>_summary.pdf`
- The documents folder: `output/<REG_NO>/documents/`
- Which RERA categories succeeded vs. failed (from the console's "Run summary" section -- don't just say "done", surface actual failures)
- The promoter's RERA track record headline numbers (total registered projects, total complaints/appeals across all of them)
- Which parts of the report are RERA-native/deterministic vs. your own web research, and any unresolved gaps you had to leave honestly unfilled
- The auth source line from the summary (cached / freshly solved / manual / none)

## Manual override (rarely needed)
If the user already has a token handy (or the automatic flow keeps failing for some reason), they can pass it directly instead of triggering the browser flow:
```
python main.py "<REG_NO_OR_PROJECT_NAME>" --token "<accessToken>"
```
They'd get this from their own browser: open the project's detail page, DevTools (F12) > Application > Session Storage > the `maharerait.maharashtra.gov.in` entry > `tokens` key > `accessToken`.

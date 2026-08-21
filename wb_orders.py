"""
Joining WBRERA's orders to a promoter, when the register names no party.

THE PROBLEM. WBRERA publishes 4,881 authority orders in one request, and
every column is Sl No. / Description / Dated / Download. The description
carries a complaint number -- "Order No. 02 dated 27.04.2026 for Complaint
No. WBRERA/COM 001338" -- and nothing else. No promoter, no project, no
party. The respondent IS in the order PDF, but at roughly 900 KB each,
4,881 of them is over 4 GB and not a sweep anyone can run.

THE JOIN. WBRERA also publishes its CAUSE LISTS, one PDF per hearing date,
and those DO name the parties against each complaint number. That gives
complaint number -> the parties, and the order register gives complaint
number -> orders. Which party is the respondent cannot be told apart
reliably once the PDF has been through OCR (see parse_cause_list_text), so
a promoter is matched by PROXIMITY inside that complaint's block and every
result is a candidate to confirm, never an established order.

THE CATCH, AND WHY resolve_complaint_no EXISTS. The cause-list PDFs carry
an OCR text layer of poor quality, and it mangles exactly the field the
join needs: "WBRERA/COMOO2117" for 002117, "WBRERAJCOMOOOTS4" for 000754 --
letter O for zero, T for 7, S for 5. Correcting those characters freely
would be guessing. What makes it safe is that the order register gives a
CLOSED SET of real complaint numbers (1,155 distinct), so a mangled string
is resolved by normalising it and looking for exactly ONE member of that
set which matches. More than one match, or none, and the row is refused
rather than attached to a promoter. Measured on a real cause list: six of
seven resolved uniquely, and the seventh had no order at all -- a pending
hearing, which is the correct outcome, not a failure.

THE COVERAGE PROBLEM IS THE USUAL ONE. Reading every cause list is 565
PDFs. Whatever is not read simply is not in the index, so a promoter's
orders can be missed -- and a missed order must never look like a promoter
with none. Every function here reports how much it read.

Offline-testable throughout: every fetch is an injected callable.
"""

import re

# Letters an OCR layer produces for digits. Applied ONLY when resolving
# against the closed set of real complaint numbers, never to invent one.
_OCR_DIGITS = {
    "O": "0", "o": "0", "Q": "0", "D": "0",
    "I": "1", "l": "1", "|": "1",
    "T": "7", "S": "5", "B": "8", "G": "6", "Z": "2", "A": "4",
}

_ORDER_COMPLAINT_RE = re.compile(r"WBRERA\s*/?\s*COM\s*([0-9]{3,8})", re.I)
# The cause lists lose the slash and mix letters into the number.
_CAUSE_COMPLAINT_RE = re.compile(r"WBRERA\s*[/J]?\s*COM\s*([A-Z0-9|]{3,10})", re.I)


def _normalise_number(raw):
    """An OCR'd complaint number as digits, with leading zeros dropped.

    Dropping leading zeros matters: the register writes 000754 and the OCR
    may render the zeros as letters in a different count.
    """
    digits = "".join(_OCR_DIGITS.get(c, c) for c in str(raw or ""))
    digits = "".join(c for c in digits if c.isdigit())
    return digits.lstrip("0")


def resolve_complaint_no(raw, known_numbers):
    """The one real complaint number `raw` can only be, or "".

    Safe because `known_numbers` is closed: it comes from the order
    register, so this picks an existing number rather than inventing a
    correction. Ambiguity is refused -- attaching an order to the wrong
    promoter is far worse than reporting one fewer.
    """
    target = _normalise_number(raw)
    if not target:
        return ""
    matches = {n for n in known_numbers if _normalise_number(n) == target}
    return matches.pop() if len(matches) == 1 else ""


def parse_order_register(html):
    """The order register as [{complaint_no, description, dated, url}].

    complaint_no is "" for a row whose description names none; those rows
    are kept, because dropping them would understate how much of the
    register cannot be joined at all.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    table = soup.find("table")
    if not table:
        return []
    entries = []
    for row in table.find_all("tr")[1:]:
        cells = [" ".join(c.get_text(" ", strip=True).split())
                 for c in row.find_all("td")]
        if len(cells) < 3:
            continue
        link = row.find("a", href=True)
        found = _ORDER_COMPLAINT_RE.search(cells[1])
        entries.append({
            "complaint_no": found.group(1) if found else "",
            "description": cells[1],
            "dated": cells[2],
            "url": link["href"] if link else "",
        })
    return entries


def parse_cause_list_text(text):
    """Each complaint number in a cause list, with the block of text that
    follows it: [{complaint_no_raw, block}].

    ATTRIBUTION IS BY PROXIMITY, NOT BY COLUMN, and that is deliberate.
    The printed cause list is a table of Complaint No. / Complainant /
    Respondent, but its OCR text layer does not preserve columns: names
    wrap mid-title ("SHIVMAHIMA DEVELOPERS" / "PRIVATE LIMITED" on separate
    lines) and hearing labels interleave between them. Picking "the last
    name in the run" as the respondent looked right on one row and was
    wrong on the next -- it read PAPPU SINGH as the respondent of a
    complaint answered by SK BUILDERS AND DEVELOPERS PVT. LIMITED.

    So no name is labelled. The block is kept whole, and a caller asks
    whether a promoter it already knows about appears in it. The residual
    risk is stated where it is used: a promoter appearing as COMPLAINANT
    would also match, which is why this proposes orders to confirm and
    never asserts one.
    """
    if not text:
        return []
    tokens = list(_CAUSE_COMPLAINT_RE.finditer(text))
    entries = []
    for index, match in enumerate(tokens):
        start = match.end()
        end = tokens[index + 1].start() if index + 1 < len(tokens) else len(text)
        block = " ".join(text[start:end].split())
        entries.append({"complaint_no_raw": match.group(1), "block": block})
    return entries


def build_complaint_index(cause_list_texts):
    """complaint_no_raw -> the text block naming its parties.

    Blocks for the same complaint are joined rather than replaced: a case
    is listed on every hearing date, and a party name mangled by one PDF's
    OCR may be legible in another's.
    """
    index = {}
    for text in cause_list_texts:
        for entry in parse_cause_list_text(text):
            key = entry["complaint_no_raw"]
            if entry["block"]:
                index[key] = (index.get(key, "") + " " + entry["block"]).strip()
    return index


def orders_for_promoter(promoter_name, orders, complaint_index):
    """Orders whose complaint was filed against this promoter.

    The promoter's name is matched inside the cause-list BLOCK for that
    complaint (see parse_cause_list_text for why no name is labelled), then
    the complaint number is RESOLVED against the order register's own
    closed set. A number that cannot be resolved uniquely is skipped -- see
    resolve_complaint_no.

    EVERY ROW IS A CANDIDATE. Proximity is not attribution: a promoter
    named as COMPLAINANT in a cause list would match here too. These are
    orders to confirm against the promoter's own record, never established
    orders against them.
    """
    needle = " ".join(str(promoter_name or "").upper().split())
    if not needle:
        return []
    known = {order["complaint_no"] for order in orders if order["complaint_no"]}
    wanted = set()
    for raw, block in complaint_index.items():
        if needle not in " ".join(str(block).upper().split()):
            continue
        resolved = resolve_complaint_no(raw, known)
        if resolved:
            wanted.add(resolved)
    return [dict(order, matched_promoter=promoter_name)
            for order in orders if order["complaint_no"] in wanted]


def coverage_note(orders, cause_lists_read, cause_lists_total, complaint_index):
    """What this join could and could not see.

    A promoter's orders are reachable only through the cause lists that
    were actually read, so an unread cause list is a silently missing
    order. Said out loud, because a short answer here looks exactly like a
    promoter with a clean record.
    """
    joinable = len({o["complaint_no"] for o in orders if o["complaint_no"]})
    return (
        f"WBRERA publishes {len(orders)} orders keyed only by complaint number, "
        f"covering {joinable} distinct complaints; no party is named in the register "
        f"itself. Promoters were matched through {cause_lists_read} of "
        f"{cause_lists_total} cause lists, which name {len(complaint_index)} "
        f"complaint(s). Orders whose complaint appears in no cause list read this "
        f"pass cannot be attributed to any promoter and are NOT reported as absent."
    )

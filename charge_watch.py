"""
Has the promoter's secured borrowing been repaid?

THE QUESTION. A charge registered against a company's assets is live until
the company files Form CHG-4 and the Registrar records its satisfaction. Our
subject carries four open charges totalling Rs 90.35 crore, and "are those
still outstanding?" is a question a lender asks again every few weeks, not
once.

HOW YOU ACTUALLY CHECK, in descending order of authority:

  1. THE LENDER'S OWN NO-DUES LETTER, or the memorandum of satisfaction the
     Registrar issues on a CHG-4 filing. Definitive, and the only thing to
     rely on at signing. Ask the borrower for it.
  2. MCA's own Index of Charges on mca.gov.in. Free and authoritative, but
     the portal refuses scripted access outright -- every URL tried returns
     403, including its public home page -- so this is a person in a
     browser, not something this pipeline can automate.
  3. THE MCA MIRRORS, which is what this module does. ZaubaCorp republishes
     the charge register including the closure date, and the pipeline
     already fetches that page for the company profile. Free, unattended,
     and repeatable.

WHAT A MIRROR CAN AND CANNOT TELL YOU. A closure date appearing is strong
evidence of satisfaction. A closure date NOT appearing is weaker: the mirror
lags MCA, and MCA lags the CHG-4 filing. So "still open here" means "no
satisfaction has reached this mirror yet", which is not the same as "the
money is still owed", and this module words it that way everywhere.

THE FAILURE THIS MODULE IS BUILT TO AVOID. A check that cannot run must
never read as "nothing changed". That is the same false-clean-record shape
that has bitten this codebase repeatedly -- K-RERA's complaint page, the
sweep's search budget, declared absences reported as failures. A charge that
simply VANISHES from the table is treated the same way: disappearing is not
satisfaction, it is a reason to look by hand.

Run directly:
    python charge_watch.py U70109MH2022PLC385473 "Pranami Neev Realty Limited"
"""

import io
import json
import os
from datetime import datetime

import company_charter as cc

SNAPSHOT_DIR = os.path.join("output", "_charge_watch")


def snapshot(cin, company_name="", profile_lookup=None):
    """The company's charge register as it stands right now.

    `profile_lookup` is injected so this is testable with no network, the
    pattern group_entities.build_entity_graph(proposer=None) and
    company_charter.run_finding_research(researcher=None) already use.
    """
    lookup = profile_lookup or cc._safe_company_profile
    profile = lookup(cin, company_name) or {}
    if not profile.get("found"):
        return {
            "cin": cin,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "ok": False,
            "note": profile.get("note") or "the company profile could not be read this pass",
            "charges": [],
        }
    charges = profile.get("charges")
    if charges is None:
        # The lookup ran but this source carried no charge table at all.
        # Distinct from "no charges": one is a missing section, the other a
        # clean register, and only the second is a finding.
        return {
            "cin": cin,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "ok": False,
            "note": "the source returned no charge register for this company this pass",
            "charges": [],
        }
    return {
        "cin": cin,
        "company_name": profile.get("name") or company_name,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "ok": True,
        "source": profile.get("url"),
        "charges": charges,
        "summary": cc.summarise_charges(charges),
    }


def compare(before, after):
    """What changed between two snapshots, in a reader's language.

    Returns {checked, changes[], still_open[], note}. `checked` is False
    whenever either snapshot failed -- a comparison against a failed fetch
    is not "no change", it is no comparison.
    """
    if not (before or {}).get("ok") or not (after or {}).get("ok"):
        return {
            "checked": False,
            "changes": [],
            "still_open": [],
            "note": (
                "The charge register could not be read on one of the two passes, so nothing can "
                "be said about whether anything was repaid. This is not evidence that the "
                "position is unchanged."
            ),
        }

    old = {c.get("charge_id"): c for c in before.get("charges") or []}
    new = {c.get("charge_id"): c for c in after.get("charges") or []}
    changes, still_open = [], []

    for charge_id, current in new.items():
        previous = old.get(charge_id)
        holder = current.get("charge_holder") or "an unnamed lender"
        amount = current.get("amount") or "an unstated amount"
        if previous is None:
            changes.append({
                "type": "new_charge",
                "charge_id": charge_id,
                "text": f"NEW: a charge of Rs {amount} to {holder} has been registered since the "
                        f"last check. The company has taken on further secured borrowing.",
            })
            continue
        if current.get("is_open") and not previous.get("is_open"):
            # Going from satisfied back to open should not happen. Report it
            # rather than silently overwriting: it usually means one of the
            # two reads is wrong.
            changes.append({
                "type": "reopened",
                "charge_id": charge_id,
                "text": f"ODD: charge {charge_id} to {holder} was recorded as satisfied "
                        f"previously and now reads as open. Verify directly before relying on "
                        f"either reading.",
            })
        elif previous.get("is_open") and not current.get("is_open"):
            changes.append({
                "type": "satisfied",
                "charge_id": charge_id,
                "text": f"SATISFIED: the charge of Rs {amount} to {holder} now carries a closure "
                        f"date of {current.get('closure_date')}. The security has been released.",
            })
        elif current.get("modification_date") != previous.get("modification_date"):
            changes.append({
                "type": "modified",
                "charge_id": charge_id,
                "text": f"MODIFIED: charge {charge_id} to {holder} was modified on "
                        f"{current.get('modification_date')}. The terms or the amount secured "
                        f"have changed; it has not been released.",
            })
        elif current.get("amount") != previous.get("amount"):
            changes.append({
                "type": "amount_changed",
                "charge_id": charge_id,
                "text": f"AMOUNT CHANGED: charge {charge_id} to {holder} now reads Rs {amount}, "
                        f"previously Rs {previous.get('amount')}.",
            })
        if current.get("is_open"):
            still_open.append(current)

    for charge_id, previous in old.items():
        if charge_id in new:
            continue
        # A charge that has disappeared has NOT been satisfied. A satisfied
        # charge stays on the register with a closure date; vanishing means
        # the page changed shape or the scrape missed it.
        changes.append({
            "type": "disappeared",
            "charge_id": charge_id,
            "text": f"CHECK BY HAND: charge {charge_id} to "
                    f"{previous.get('charge_holder') or 'an unnamed lender'} was on the register "
                    f"before and is not there now. A satisfied charge stays listed with a "
                    f"closure date, so this is not evidence of repayment.",
        })

    return {
        "checked": True,
        "changes": changes,
        "still_open": still_open,
        "note": (
            "Read from an MCA mirror, which lags the Registrar, which in turn lags the "
            "company's own filing. A closure date appearing here is good evidence of "
            "satisfaction; its absence only means no satisfaction has reached this mirror yet, "
            "not that the borrowing is definitely still outstanding."
        ),
    }


def _path(cin):
    return os.path.join(SNAPSHOT_DIR, f"{cin.strip().upper()}.json")


def load_previous(cin):
    path = _path(cin)
    if not os.path.exists(path):
        return None
    with io.open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save(current):
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    with io.open(_path(current["cin"]), "w", encoding="utf-8") as f:
        json.dump(current, f, indent=1, ensure_ascii=False)


def check(cin, company_name="", profile_lookup=None):
    """Fetch, compare against the last saved snapshot, and save this one."""
    previous = load_previous(cin)
    current = snapshot(cin, company_name, profile_lookup=profile_lookup)
    result = compare(previous, current) if previous else {
        "checked": False,
        "changes": [],
        "still_open": [c for c in current.get("charges") or [] if c.get("is_open")],
        "note": "First check for this company: there is nothing yet to compare against. "
                "Re-run later to see what has moved.",
    }
    if current.get("ok"):
        save(current)
    return current, result


if __name__ == "__main__":
    import sys

    identifier = sys.argv[1] if len(sys.argv) > 1 else "U70109MH2022PLC385473"
    name = sys.argv[2] if len(sys.argv) > 2 else ""
    now, delta = check(identifier, name)

    if not now.get("ok"):
        print(f"[!] {now.get('note')}")
        raise SystemExit(1)

    summary = now["summary"]
    total = summary["total_open_amount"]
    print(f"{now.get('company_name') or identifier}   checked {now['fetched_at']}")
    print(f"  {summary['open_charges']} open, {summary['satisfied_charges']} satisfied, "
          f"of {summary['total_charges']} on the register")
    print(f"  open secured borrowing: "
          f"{cc._format_rupees(total) if total is not None else 'amounts unreadable'}")
    for lender in summary["open_lenders"]:
        print(f"    - {lender}")
    print()
    if delta["changes"]:
        print("  CHANGED SINCE THE LAST CHECK:")
        for change in delta["changes"]:
            print(f"    {change['text']}")
    elif delta["checked"]:
        print("  No change since the last check.")
    print()
    print(f"  {delta['note']}")

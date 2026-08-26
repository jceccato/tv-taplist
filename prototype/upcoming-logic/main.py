#!/usr/bin/env python3
"""Throwaway logic prototype for issue #4: "Coming up" teaser cards.

Models the resolution rules agreed in the design-grilling session against fake
batch dicts and prints a readable board plus teaser queue per scenario. Imports
nothing from app/ and writes nothing; it exists to answer two questions for a
human reviewer:

  1. Does the ON-toggle resolution shape feel right?
  2. Do the two teaser acquisition paths merge into one ordered queue?
     (a) non-Completed tap:N batches that did not occupy a slot
     (b) upcoming: batches with no tap:N

Run from the repo root:  python prototype/upcoming-logic/main.py
"""

import re
from collections import Counter

# --- Model constants ----------------------------------------------------------

# Lower rank = more ready. Unknown or missing status ranks last (5), so if
# Brewfather ever stops sending a status, everything ties at the bottom and
# ordering degrades to plain newest-wins inside that rank - the same
# degradation the real slot-conflict resolver already accepts.
STATUS_RANK = {
    "completed": 0,
    "conditioning": 1,
    "fermenting": 2,
    "brewing": 3,
    "planning": 4,
}
UNKNOWN_RANK = 5

DEFAULT_CAP = 3  # max_upcoming_previews default

# Sentinel board entry for a Manual tap. Never a batch dict, so isinstance()
# keeps the two apart everywhere.
MANUAL_LABEL = "Manual tap"

# tap:N claims a slot; upcoming: teases. Both tokens may sit in one notes
# string, in which case tap:N wins and upcoming: is ignored.
TAP_TOKEN = re.compile(r"tap:\s*(\d+)")
UPCOMING_TOKEN = re.compile(r"upcoming:")


def mk(_id, name, status, updated, notes):
    """One fake batch: _id, name, status, updated (recency in ms), notes."""
    return {"_id": _id, "name": name, "status": status, "updated": updated, "notes": notes}


def rank_of(batch):
    status = (batch.get("status") or "").lower()
    return STATUS_RANK.get(status, UNKNOWN_RANK)


def tokens(batch):
    """Return (slot, has_upcoming) parsed from the batch notes.

    Text after the upcoming: token is ignored, mirroring how tap:N tokens are
    stripped from any text shown on a card.
    """
    notes = batch.get("notes") or ""
    m = TAP_TOKEN.search(notes)
    return (int(m.group(1)) if m else None, bool(UPCOMING_TOKEN.search(notes)))


def group_claims(batches):
    """Map slot -> every batch whose notes carry tap:N for that slot."""
    claims = {}
    for b in batches:
        slot, _ = tokens(b)
        if slot is not None:
            claims.setdefault(slot, []).append(b)
    return claims


def empty_board(num_taps):
    return {slot: None for slot in range(1, num_taps + 1)}


def resolve_off(batches, manual_slots, num_taps):
    """Today's world: no teasers exist.

    Manual slots are pre-occupied (Manual beats Brewfather, always). Every
    remaining batch with tap:N competes for its slot: the most-complete status
    wins and recency (newest) only breaks a tie within one status. Losers are
    discarded and logged. The upcoming: token does nothing yet.
    """
    board, log = empty_board(num_taps), []
    for slot in manual_slots:
        board[slot] = (MANUAL_LABEL, None)
    for slot, claimants in sorted(group_claims(batches).items()):
        if slot in manual_slots:
            for b in claimants:
                log.append(
                    f"slot {slot}: {b['name']} ({b['status']}) discarded - a "
                    "Manual tap holds the slot (kept warm underneath it in the "
                    "real system)"
                )
            continue
        winner = min(claimants, key=lambda b: (rank_of(b), -b["updated"]))
        if slot in board:
            board[slot] = winner
        else:
            log.append(
                f"slot {slot}: {winner['name']} ({winner['status']}) won, but "
                f"slot {slot} is beyond num_taps={num_taps}; kept off the board"
            )
        for b in claimants:
            if b is winner:
                continue
            if rank_of(b) != rank_of(winner):
                reason = f"status {b['status']} ranks behind {winner['status']}"
            elif b["updated"] < winner["updated"]:
                reason = "older within the same status"
            else:
                reason = "identical rank and recency, arbitrary"
            log.append(f"slot {slot}: {b['name']} ({b['status']}) discarded - {reason}")
    return board, [], log


def resolve_on(batches, manual_slots, num_taps, cap=DEFAULT_CAP):
    """Toggle-on world: teasers exist.

    Occupancy pass (step 1), in two sub-passes:

    - Completed batches with tap:N claim their slots first, recency tie-break
      among Completed. The most-ready beer pours, so a Conditioning batch that
      would hold a tap today yields it to a Completed batch and teases instead.
    - Conditioning batches then fill slots that no Completed batch claimed AND
      that have no Manual tap, recency tie-break among Conditioning. This is
      the exception that preserves today's outcome for slots nobody more ready
      wants: today a Conditioning tap:N occupies, and with the toggle on it
      still occupies.
    - Every other non-Completed status (Fermenting and below) never occupies a
      tap, so a Fermenting tap:5 leaves slot 5 Vacant.

    "Claim" here decides Conditioning exclusion and teaser membership, not the
    board itself: Manual precedence is untouched, so a Manual tap still wins
    its slot over any claimant.

    Teaser set (step 2):

    - (a) non-Completed batches with tap:N that did not occupy a slot,
    - (b) batches with upcoming: and no tap:N, any status including Completed.

    A batch with both tokens: tap:N wins and upcoming: is ignored. Completed
    tap:N losers are NOT teasers - a beer that was pulled is not coming up.

    Queue (step 3): one merged list sorted by status rank ascending, then
    recency descending, capped at max_upcoming_previews. The sort key ignores
    which path an entry came from.
    """
    board, log = empty_board(num_taps), []
    for slot in manual_slots:
        board[slot] = (MANUAL_LABEL, None)
    claims = group_claims(batches)

    # Sub-pass 1: Completed claims its slot (or the Manual tap beats it).
    completed_winners = {}
    for slot, claimants in sorted(claims.items()):
        completed = [b for b in claimants if b["status"] == "completed"]
        if not completed:
            continue
        winner = max(completed, key=lambda b: b["updated"])
        completed_winners[slot] = winner
        for b in completed:
            if b is winner:
                continue
            log.append(
                f"slot {slot}: {b['name']} (completed) lost the recency tie to "
                f"{winner['name']} - completed tap:N losers are never teasers"
            )
        if slot in manual_slots:
            log.append(
                f"slot {slot}: {winner['name']} (completed) sits beneath the "
                "Manual tap - not a teaser"
            )
        elif slot in board:
            board[slot] = winner
        else:
            log.append(
                f"slot {slot}: {winner['name']} (completed) won, but slot {slot} "
                f"is beyond num_taps={num_taps}; kept off the board"
            )

    # Sub-pass 2: Conditioning fills only slots nobody more ready claimed.
    for slot, claimants in sorted(claims.items()):
        if slot in completed_winners or slot in manual_slots:
            holder = (
                "a Manual tap holds the slot"
                if slot in manual_slots
                else "a Completed batch claimed the slot"
            )
            for b in claimants:
                if b["status"] == "conditioning":
                    log.append(
                        f"slot {slot}: {b['name']} (conditioning) did not occupy - "
                        f"{holder} - will appear as a teaser"
                    )
            continue
        conditioning = [b for b in claimants if b["status"] == "conditioning"]
        if not conditioning:
            continue
        winner = max(conditioning, key=lambda b: b["updated"])
        for b in conditioning:
            if b is winner:
                continue
            log.append(
                f"slot {slot}: {b['name']} (conditioning) lost the recency tie "
                f"to {winner['name']} - will appear as a teaser"
            )
        if slot in board:
            board[slot] = winner
        else:
            log.append(
                f"slot {slot}: {winner['name']} (conditioning) won, but slot "
                f"{slot} is beyond num_taps={num_taps}; kept off the board"
            )

    occupied_ids = {id(b) for b in board.values() if isinstance(b, dict)}

    teasers = []
    for b in batches:
        slot, has_upcoming = tokens(b)
        if slot is not None:
            # tap:N wins over upcoming: on the same batch.
            if id(b) in occupied_ids:
                continue
            if b["status"] == "completed":
                continue  # completed tap:N losers are not teasers
            teasers.append((b, slot, "tap:N"))
        elif has_upcoming:
            teasers.append((b, None, "upcoming:"))

    queue = sorted(teasers, key=lambda t: (rank_of(t[0]), -t[0]["updated"]))
    kept, dropped = queue[:cap], queue[cap:]
    for b, _slot, _path in dropped:
        log.append(
            f"teaser {b['name']} ({b['status']}) dropped - queue capped at "
            f"max_upcoming_previews={cap}"
        )
    bound = Counter(slot for _b, slot, _p in kept if slot is not None)
    for slot, count in bound.items():
        if count > 1:
            log.append(
                f"slot {slot}: {count} teasers are bound to the same slot - "
                "nothing in the rules prevents this"
            )
    return board, kept, log


# --- Printing -----------------------------------------------------------------

def describe(b):
    return f"{b['name']} ({b['status']})"


def board_line(slot, entry):
    if entry is None:
        return f"slot {slot:>2}: Vacant"
    if not isinstance(entry, dict):
        return f"slot {slot:>2}: Manual tap"
    return f"slot {slot:>2}: {describe(entry)}"


def compact_board(board):
    parts = []
    for slot in sorted(board):
        entry = board[slot]
        if entry is None:
            continue
        if isinstance(entry, dict):
            parts.append(f"{slot}={describe(entry)}")
        else:
            parts.append(f"{slot}=Manual tap")
    return "; ".join(parts) if parts else "all vacant"


def queue_lines(queue):
    if not queue:
        return ["    (empty)"]
    lines = []
    for i, (b, slot, path) in enumerate(queue, 1):
        bound = f"slot {slot}" if slot is not None else "unbound (None)"
        lines.append(
            f"    {i}. {b['name']:<14} | {b['status']:<12} | {bound:<13} | "
            f"via {path:<9} | key (rank={rank_of(b)}, updated={b['updated']})"
        )
    return lines


def print_model():
    print("Issue #4 'Coming up' teaser logic prototype")
    print("Fake data only; nothing imported from app/; nothing written to disk.")
    print()
    print("Status ranks (lower = more ready):")
    for status, rank in sorted(STATUS_RANK.items(), key=lambda kv: kv[1]):
        print(f"  {status:<12} = {rank}")
    print(f"  unknown/missing = {UNKNOWN_RANK}")
    print(f"Default max_upcoming_previews cap: {DEFAULT_CAP}")
    print("Queue sort key: (status rank ascending, recency descending)")
    print("Teaser paths: (a) tap:N non-Completed batches that did not occupy a slot")
    print("             (b) upcoming: batches with no tap:N (any status)")
    print("Both tokens on one batch: tap:N wins, upcoming: is ignored.")


def print_scenario(sc):
    num_taps = sc.get("num_taps", 6)
    cap = sc.get("cap", DEFAULT_CAP)
    print()
    print("=" * 78)
    print(f"Scenario {sc['num']}: {sc['title']}")
    print("=" * 78)
    print(f"Proves: {sc['proves']}")
    board_on, queue, log = resolve_on(sc["batches"], sc["manual"], num_taps, cap)

    if sc.get("layout") == "side_by_side":
        board_off, _, off_log = resolve_off(sc["batches"], sc["manual"], num_taps)
        print()
        print("Board, both worlds side by side:")
        left = [board_line(slot, board_off[slot]) for slot in range(1, num_taps + 1)]
        right = [board_line(slot, board_on[slot]) for slot in range(1, num_taps + 1)]
        width = max(len(line) for line in left) + 3
        print("  " + "OFF (today)".ljust(width) + "ON (with teasers)")
        for l, r in zip(left, right):
            print("  " + l.ljust(width) + r)
        print()
        print("  Teaser queue, OFF world: none - the feature is off")
        print(f"  Teaser queue, ON world (cap {cap}):")
        for line in queue_lines(queue):
            print(line)
        print()
        print("  Log, ON world:")
        if log:
            for line in log:
                print("    - " + line)
        else:
            print("    (nothing logged)")
        if off_log:
            print("  Log, OFF world:")
            for line in off_log:
                print("    - " + line)
        return

    print()
    print("  Board (toggle ON):")
    for slot in range(1, num_taps + 1):
        print("    " + board_line(slot, board_on[slot]))
    board_off, _, _ = resolve_off(sc["batches"], sc["manual"], num_taps)
    print(f"  Board (toggle OFF, today): {compact_board(board_off)}")
    print()
    print(f"  Teaser queue (cap {cap}):")
    for line in queue_lines(queue):
        print(line)
    print()
    print("  Log (ON world):")
    if log:
        for line in log:
            print("    - " + line)
    else:
        print("    (nothing logged)")


# --- Scenarios ----------------------------------------------------------------

SCENARIOS = [
    {
        "num": "1",
        "title": "OFF vs ON on identical data - Conditioning claims tap:3, no Completed batch, no Manual tap",
        "proves": (
            "The exception that preserves today's behaviour: with teasers ON, a "
            "Conditioning batch still occupies a tap that no Completed batch "
            "claimed, exactly as OFF does. It is NOT a teaser."
        ),
        "batches": [mk("b1", "Golden Ale", "conditioning", 500, "tap:3")],
        "manual": [],
        "layout": "side_by_side",
    },
    {
        "num": "2",
        "title": "Conditioning tap:3 plus Completed tap:3",
        "proves": (
            "The most-ready beer pours: Completed occupies slot 3 and the "
            "Conditioning batch becomes a teaser bound to slot 3 instead of "
            "being discarded."
        ),
        "batches": [
            mk("b1", "IPA Champion", "completed", 800, "tap:3"),
            mk("b2", "Brown Ale", "conditioning", 900, "tap:3"),
        ],
        "manual": [],
    },
    {
        "num": "3",
        "title": "Fermenting tap:5, slot vacant",
        "proves": (
            "Fermenting never occupies a tap: slot 5 stays Vacant and the beer "
            "teases bound to the slot it is destined for. Note the OFF line: "
            "today that same batch would pour on tap 5."
        ),
        "batches": [mk("b1", "Dubbel", "fermenting", 700, "tap:5")],
        "manual": [],
    },
    {
        "num": "4",
        "title": "Manual tap on slot 2, Conditioning claims tap:2",
        "proves": (
            "Manual wins the board regardless of the toggle; the Conditioning "
            "batch teases slot 2 from underneath the override."
        ),
        "batches": [mk("b1", "Saison", "conditioning", 600, "tap:2")],
        "manual": [2],
    },
    {
        "num": "5",
        "title": "Two Completed batches claim tap:4",
        "proves": (
            "The recency winner pours. The loser is NOT a teaser: a beer that "
            "was pulled is not coming up."
        ),
        "batches": [
            mk("b1", "Stout", "completed", 1000, "tap:4"),
            mk("b2", "Porter", "completed", 1200, "tap:4"),
        ],
        "manual": [],
    },
    {
        "num": "6",
        "title": "upcoming: on a Conditioning batch with no tap:N",
        "proves": (
            "Path (b): an unbound teaser. No slot is claimed, so it teases "
            "without a destination tap; text after the token is ignored."
        ),
        "batches": [mk("b1", "Hefeweizen", "conditioning", 400, "upcoming: next in line")],
        "manual": [],
    },
    {
        "num": "7",
        "title": "upcoming: on a Completed batch with no tap:N",
        "proves": (
            "The most-ready beer teases first: Completed ranks 0, so an unbound "
            "Completed batch sorts to the top of the queue."
        ),
        "batches": [mk("b1", "Lager", "completed", 300, "upcoming:")],
        "manual": [],
    },
    {
        "num": "8a",
        "title": "Both tokens on one batch, tap:6 loses occupancy",
        "proves": (
            "tap:N wins over upcoming:: the batch teases BOUND to slot 6 (via "
            "tap:N), not unbound, so upcoming: was ignored."
        ),
        "batches": [
            mk("b1", "Amber", "completed", 500, "tap:6"),
            mk("b2", "Dual A", "conditioning", 600, "tap:6 upcoming:"),
        ],
        "manual": [],
    },
    {
        "num": "8b",
        "title": "Both tokens on one batch, tap:6 wins occupancy",
        "proves": (
            "tap:N wins over upcoming:: the batch pours on slot 6 and no teaser "
            "is created, so upcoming: was ignored."
        ),
        "batches": [mk("b1", "Dual B", "conditioning", 400, "tap:6 upcoming:")],
        "manual": [],
    },
    {
        "num": "9",
        "title": "Cap: six teasers, max_upcoming_previews = 3",
        "proves": (
            "Only the top three by (status rank, recency) survive; the rest are "
            "dropped with a log line each."
        ),
        "batches": [
            mk("b1", "Done-1", "completed", 100, "upcoming:"),
            mk("b2", "Cond-1", "conditioning", 200, "upcoming:"),
            mk("b3", "Cond-2", "conditioning", 900, "upcoming:"),
            mk("b4", "Ferm-1", "fermenting", 300, "upcoming:"),
            mk("b5", "Brew-1", "brewing", 400, "upcoming:"),
            mk("b6", "Plan-1", "planning", 500, "upcoming:"),
        ],
        "manual": [],
    },
    {
        "num": "10",
        "title": "Ordering: mixed statuses and recencies, both paths",
        "proves": (
            "conditioning before fermenting before brewing before planning, "
            "newest first within one status, and the two paths interleave on "
            "the same key (path is not part of the sort)."
        ),
        "batches": [
            mk("b1", "Champ", "completed", 1000, "tap:3"),
            mk("b2", "Cond-Old", "conditioning", 100, "tap:3"),
            mk("b3", "Cond-New", "conditioning", 900, "upcoming:"),
            mk("b4", "Ferm", "fermenting", 5000, "upcoming:"),
            mk("b5", "Brew", "brewing", 8000, "upcoming:"),
            mk("b6", "Plan", "planning", 12000, "upcoming:"),
        ],
        "manual": [],
        "cap": 6,
    },
    {
        "num": "11 (bonus)",
        "title": "Two Fermenting batches claim the same vacant slot",
        "proves": (
            "A wrinkle: neither batch occupies (Fermenting never does), so BOTH "
            "become teasers bound to slot 5. The recency tie-break never runs "
            "because the status never competes for occupancy."
        ),
        "batches": [
            mk("b1", "Ferm A", "fermenting", 100, "tap:5"),
            mk("b2", "Ferm B", "fermenting", 200, "tap:5"),
        ],
        "manual": [],
    },
]


def main():
    print_model()
    for sc in SCENARIOS:
        print_scenario(sc)
    print()
    print("End of scenarios.")


if __name__ == "__main__":
    main()

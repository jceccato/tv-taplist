#!/usr/bin/env python3
"""Interactive TUI for the issue #4 "Coming up" teaser logic prototype.

A menu loop over the same resolution functions main.py models. Edit the fake
batches, Manual taps, tap count, cap and the show_upcoming_previews toggle
live; the board and teaser queue re-resolve after every change so the rules
can be felt, not just read.

Run from the repo root:  python prototype/upcoming-logic/tui.py
Stdlib only; writes nothing to disk.
"""

import os
import shlex
import sys

import main as model

NO_CLEAR = "--no-clear" in sys.argv  # for piped / captured runs

STATE = {
    "batches": [
        model.mk("b1", "IPA Champion", "completed", 800, "tap:3"),
        model.mk("b2", "Brown Ale", "conditioning", 900, "tap:3"),
        model.mk("b3", "Dubbel", "fermenting", 700, "tap:5"),
        model.mk("b4", "Hefeweizen", "conditioning", 400, "upcoming: next in line"),
    ],
    "manual": set(),
    "num_taps": 6,
    "on": True,
    "cap": model.DEFAULT_CAP,
}

HELP = """Commands:
  add                          add a batch, prompted
  add <name> <status> <updated> <notes...>   add in one line
                               quote multi-word names: add "Golden Pils" conditioning 500 tap:3
  rm <n>                       remove batch n (numbering from `batches`)
  batches                      list batches with their index
  load <n>                     load scenario n into the live state
  manual <slot>                toggle a Manual tap on a slot
  taps [n]                     show / set num_taps
  cap [n]                      show / set max_upcoming_previews
  toggle                       flip show_upcoming_previews
  board                        re-render (also runs after every change)
  scenarios                    run the 12 fixed scenarios from main.py
  help, quit
Statuses: completed, conditioning, fermenting, brewing, planning, unknown.
Notes may carry tap:N and/or upcoming: tokens."""


def clear():
    if sys.stdout.isatty() and not NO_CLEAR:
        os.system("cls" if os.name == "nt" else "clear")


def render():
    clear()
    state = "ON" if STATE["on"] else "OFF"
    print(f"show_upcoming_previews: {state}   max_upcoming_previews: {STATE['cap']}"
          f"   num_taps: {STATE['num_taps']}")
    print()
    if STATE["on"]:
        board, queue, log = model.resolve_on(
            STATE["batches"], STATE["manual"], STATE["num_taps"], STATE["cap"])
        print("Board (toggle ON):")
        for slot in range(1, STATE["num_taps"] + 1):
            print("  " + model.board_line(slot, board[slot]))
        print(f"  Board (toggle OFF, today): "
              f"{model.compact_board(model.resolve_off(STATE['batches'], STATE['manual'], STATE['num_taps'])[0])}")
        print()
        print(f"Teaser queue (cap {STATE['cap']}):")
        for line in model.queue_lines(queue):
            print(line)
    else:
        board, _, log = model.resolve_off(
            STATE["batches"], STATE["manual"], STATE["num_taps"])
        print("Board (toggle OFF, today):")
        for slot in range(1, STATE["num_taps"] + 1):
            print("  " + model.board_line(slot, board[slot]))
        print()
        print("Teaser queue: none - the feature is off")
    print()
    print("Log:")
    if log:
        for line in log:
            print("  - " + line)
    else:
        print("  (nothing logged)")
    print()


def prompt_add():
    print("Add a batch (blank name cancels).")
    name = input("  name: ").strip()
    if not name:
        return
    status = input("  status [conditioning]: ").strip().lower() or "conditioning"
    if status not in model.STATUS_RANK:
        status = "unknown"
    raw = input("  updated (recency ms) [0]: ").strip()
    try:
        updated = int(raw)
    except ValueError:
        updated = 0
    notes = input("  notes (e.g. tap:3 upcoming:) []: ").strip()
    STATE["batches"].append(
        model.mk(f"u{len(STATE['batches']) + 1}", name, status, updated, notes))
    print(f"Added {name}.")


def one_line_add(args):
    # Fast path: add <name> <status> <updated> <notes...> - only taken when the
    # second word is a valid status. Anything else (multi-word names, typos)
    # falls through to the prompts, with everything typed treated as the name,
    # so a mistyped command never mints a batch with a guessed status.
    status = args[1].lower() if len(args) > 1 else ""
    if status not in model.STATUS_RANK:
        print("Could not parse that as 'add <name> <status> <updated> [notes...]'.")
        name = " ".join(args).strip()
        if not name:
            print("No name given; use `add` for the prompted form.")
            return
        print(f"Falling back to prompts; name prefilled as '{name}'.")
        status = input("  status [conditioning]: ").strip().lower() or "conditioning"
        if status not in model.STATUS_RANK:
            status = "unknown"
        raw = input("  updated (recency ms) [0]: ").strip()
        try:
            updated = int(raw)
        except ValueError:
            updated = 0
        notes = input("  notes (e.g. tap:3 upcoming:) []: ").strip()
        STATE["batches"].append(
            model.mk(f"u{len(STATE['batches']) + 1}", name, status, updated, notes))
        print(f"Added {name} ({status}).")
        return
    name = args[0]
    updated = 0
    if len(args) > 2:
        try:
            updated = int(args[2])
        except ValueError:
            print(f"'{args[2]}' is not a number; using 0.")
    notes = " ".join(args[3:])
    STATE["batches"].append(
        model.mk(f"u{len(STATE['batches']) + 1}", name, status, updated, notes))
    print(f"Added {name} ({status}).")


def list_batches():
    if not STATE["batches"]:
        print("No batches.")
        return
    for i, b in enumerate(STATE["batches"]):
        print(f"  {i}: {model.describe(b)}  updated={b['updated']}  notes={b['notes'] or '-'}")


def load_scenario(arg):
    # Exact number match first, then prefix, so `load 11` finds "11 (bonus)"
    # and multi-word numbers survive the shell splitting on spaces.
    for sc in model.SCENARIOS:
        if sc["num"] == arg:
            break
    else:
        for sc in model.SCENARIOS:
            if sc["num"].startswith(arg):
                break
        else:
            print(f"No scenario '{arg}'. Use scenarios to see the numbers.")
            return
    STATE["batches"] = list(sc["batches"])
    STATE["manual"] = set(sc["manual"])
    STATE["num_taps"] = sc.get("num_taps", 6)
    STATE["cap"] = sc.get("cap", model.DEFAULT_CAP)
    print(f"Loaded scenario {sc['num']}: {sc['title']}")


def dispatch(line):
    # shlex so quoted multi-word names survive: add "Golden Pils" conditioning
    # 500 tap:3. Unbalanced quotes fall back to a plain whitespace split.
    try:
        parts = shlex.split(line)
    except ValueError:
        parts = line.split()
    if not parts:
        return True
    cmd, args = parts[0].lower(), parts[1:]
    if cmd in ("quit", "q", "exit"):
        return False
    if cmd == "help":
        print(HELP)
        return True
    if cmd == "board":
        render()
        return True
    if cmd == "scenarios":
        model.print_model()
        for sc in model.SCENARIOS:
            model.print_scenario(sc)
        print()
        print("End of scenarios.")
        return True
    if cmd == "batches":
        list_batches()
        return True
    if cmd == "toggle":
        STATE["on"] = not STATE["on"]
        render()
        return True
    if cmd == "cap":
        if args:
            try:
                STATE["cap"] = max(0, int(args[0]))
            except ValueError:
                print(f"'{args[0]}' is not a number.")
        else:
            print(f"max_upcoming_previews = {STATE['cap']}")
        if args:
            render()
        return True
    if cmd == "taps":
        if args:
            try:
                STATE["num_taps"] = max(1, int(args[0]))
            except ValueError:
                print(f"'{args[0]}' is not a number.")
        else:
            print(f"num_taps = {STATE['num_taps']}")
        if args:
            render()
        return True
    if cmd == "manual":
        if not args:
            print("Usage: manual <slot> (toggles the Manual tap on that slot).")
            return True
        try:
            slot = int(args[0])
        except ValueError:
            print(f"'{args[0]}' is not a number.")
            return True
        if slot in STATE["manual"]:
            STATE["manual"].discard(slot)
            print(f"Manual tap removed from slot {slot}.")
        else:
            STATE["manual"].add(slot)
            print(f"Manual tap placed on slot {slot}.")
        render()
        return True
    if cmd == "add":
        if args:
            one_line_add(args)
        else:
            prompt_add()
        render()
        return True
    if cmd == "rm":
        if not args:
            print("Usage: rm <n> - index from `batches`.")
            return True
        try:
            i = int(args[0])
            b = STATE["batches"].pop(i)
        except (ValueError, IndexError):
            print(f"No batch at index {args[0]}. Use `batches` to see indices.")
            return True
        print(f"Removed {model.describe(b)}.")
        render()
        return True
    if cmd == "load":
        if not args:
            print("Usage: load <n> - e.g. load 2 or load 11 (bonus).")
            return True
        load_scenario(" ".join(args))
        render()
        return True
    print(f"Unknown command '{cmd}'. Try `help`.")
    return True


def loop():
    render()
    print(HELP)
    print()
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not dispatch(line):
            return


if __name__ == "__main__":
    loop()

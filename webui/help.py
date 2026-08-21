"""What Opus tells you about itself.

Kept here rather than in the markup so it is one source, testable, and
reachable from the command line as well as the interface.

HOW THIS IS WRITTEN

Not as a tour. A tour fires once, gets clicked away to reach the thing you came
for, and teaches you where the buttons are -- which is not what anyone actually
gets wrong. What people get wrong here is conceptual: that a held order is not
a failed one, that moving the threshold is a decision with consequences, that
the ledger matters most on the day something has gone wrong.

So each entry answers three questions in the same order: what this is for, what
decision you are making here, and what to watch out for. No screenshots, no
step numbers, nothing that goes stale when a button moves.

The tone is deliberate too. This is written for a publisher who knows her own
business and does not know this software, so it explains the software and
assumes the business.
"""

WORKSPACES = {
    "overview": {
        "title": "Overview",
        "what": (
            "Where a working day starts. The tiles count what came in, what is "
            "ready to go out, and what is waiting on you; the panels below "
            "expand on each."
        ),
        "decide": (
            "Nothing. This screen only reports. Every action lives one click "
            "away, which is on purpose -- you should never issue a licence by "
            "accident from a summary."
        ),
        "watch": (
            "“Needs a person” is the number that matters. If it is "
            "zero, the day is uneventful. If it is not, something did not "
            "match cleanly and Opus would rather ask than guess."
        ),
    },
    "orders": {
        "title": "Orders",
        "what": (
            "One row per completed sale in the export, matched against your "
            "catalogue. Refunds, withdrawals and payments that have not "
            "cleared never reach this list."
        ),
        "decide": (
            "Whether to issue. Press Stamp and the released orders are "
            "written, locked and logged. Anything held stays exactly where it "
            "is until you look at it."
        ),
        "watch": (
            "The chip after each title says how it matched. “exact” "
            "means the title matched your catalogue letter for letter; "
            "“contains” means one is inside the other, which is how "
            "“Evening Bells - PDF Download” finds Evening Bells. "
            "“fuzzy” means it is guessing by similarity, and is "
            "worth opening before you agree with it."
        ),
    },
    "threshold": {
        "title": "The release threshold",
        "what": (
            "Every order gets a score out of one, from how the title matched, "
            "whether another piece could also have matched, whether the file "
            "count looks right, whether this buyer has ordered before, and "
            "whether there is an address to send to. The threshold is the line "
            "above which Opus stops asking."
        ),
        "decide": (
            "How much you are willing to have happen without you. At 1.01 "
            "nothing clears the line and you approve everything, which is "
            "where it starts. Lower it and more goes out unseen."
        ),
        "watch": (
            "Move the slider before you save it: the line underneath tells you "
            "how many more orders would go out without you seeing them. The "
            "honest way to choose a number is to run a month at the current "
            "setting, compare what would have released against what you "
            "actually approved, and only then lower it. Two things are held "
            "whatever the score — a transaction already in the ledger, and "
            "a title that more than one piece could match."
        ),
    },
    "catalog": {
        "title": "Catalogue",
        "what": (
            "What Opus believes you sell: one folder per piece, the parts "
            "inside it. It is read, never written -- your master files are not "
            "touched."
        ),
        "decide": (
            "Nothing here, but it is where to look when a title will not "
            "match. Nine times in ten the folder name and the name PayPal "
            "sends have drifted apart."
        ),
        "watch": (
            "A part called score.pdf usually appears in several pieces, which "
            "is why an issued file is named for the piece as well as the part "
            "and the buyer. Otherwise someone ordering two pieces gets two "
            "files they cannot tell apart."
        ),
    },
    "ledger": {
        "title": "Ledger",
        "what": (
            "One row per file, written the moment it is issued: who received "
            "it, under which order, on what date, and the password that locks "
            "it. Held orders are recorded too, with the reason."
        ),
        "decide": (
            "Nothing routine. This is the record you reach for on the day a "
            "PDF turns up somewhere it should not be."
        ),
        "watch": (
            "Every row is sealed against the row before it, so an edit, a "
            "deletion or a reordering breaks the chain and Check the chain "
            "will say which row and how. That is what makes it evidence rather "
            "than a list. It cannot stop somebody rewriting the whole file, so "
            "keep a backup somewhere else."
        ),
    },
    "conn": {
        "title": "Connections",
        "what": (
            "Where orders and master files come from, and how finished files "
            "reach the buyer. Fill in a card and press Test to find out "
            "whether it works now, rather than on the morning it does not."
        ),
        "decide": (
            "Which sources to use. Most publishers need two: somewhere the "
            "music lives, and somewhere the orders come from."
        ),
        "watch": (
            "A connector marked “planned” has no implementation "
            "behind it and will say so if you try to use it. Nothing here "
            "pretends to be connected when it is not."
        ),
    },
    "settings": {
        "title": "Settings",
        "what": (
            "Folders, appearance, and whether finished files are sent "
            "automatically. All of it is kept in one file on this machine that "
            "you can open and read."
        ),
        "decide": (
            "Where your catalogue is, where stamped files should go, and "
            "whether delivery happens without you."
        ),
        "watch": (
            "Automatic delivery is off until you turn it on. Files are stamped "
            "and logged either way; the switch only decides whether they leave "
            "without you looking."
        ),
    },
}


# ---------------------------------------------------------------------------
# First run
#
# A checklist rather than a tour, and every step reports its own state from
# what is actually configured. A step cannot be ticked by being clicked, which
# means the list can never tell her she is further along than she is.
# ---------------------------------------------------------------------------

STEPS = [
    {
        "id": "practice",
        "title": "Try it on the practice catalogue first",
        "body": (
            "Opus carries a small made-up catalogue and a made-up day of "
            "orders. Turn on practice mode and run the whole thing end to end "
            "-- match, review, stamp, look at the ledger. Nothing you do "
            "touches your own files, and you can switch back whenever you "
            "like."
        ),
        "action": "Turn on practice mode",
        "workspace": "settings",
    },
    {
        "id": "catalog",
        "title": "Point Opus at your music",
        "body": (
            "One folder, with a subfolder per piece and the parts inside. Opus "
            "only reads it."
        ),
        "action": "Choose the folder",
        "workspace": "settings",
    },
    {
        "id": "export",
        "title": "Add a PayPal export",
        "body": (
            "Download an activity CSV from PayPal covering the days you want "
            "to issue. Overlapping ranges are safe -- anything already in the "
            "ledger is skipped."
        ),
        "action": "Choose the file",
        "workspace": "settings",
    },
    {
        "id": "out",
        "title": "Say where stamped files should go",
        "body": (
            "A folder for the finished files. The ledger lives here too, so "
            "keep it somewhere that gets backed up."
        ),
        "action": "Choose the folder",
        "workspace": "settings",
    },
    {
        "id": "review",
        "title": "Look at a real day before issuing anything",
        "body": (
            "Open Orders. Every order is matched and scored, and nothing is "
            "written until you press Stamp. Opus holds everything for your "
            "approval until you decide otherwise."
        ),
        "action": "Open Orders",
        "workspace": "orders",
    },
]


def step_state(config, status):
    """Which steps are done, judged from real settings rather than clicks."""
    paths = (config or {}).get("paths") or {}
    done = {
        "practice": bool((config or {}).get("practice")),
        "catalog": bool((status or {}).get("catalog", {}).get("ready")),
        "export": bool((status or {}).get("export", {}).get("ready")),
        "out": bool(paths.get("out_dir")),
        "review": bool((status or {}).get("issued")),
    }
    return [dict(step, done=done.get(step["id"], False)) for step in STEPS]


def payload(config=None, status=None):
    return {
        "workspaces": WORKSPACES,
        "steps": step_state(config, status),
    }

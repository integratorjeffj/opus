"""Scoring an order, so a machine can tell the safe ones from the rest.

Opus has always had one confidence number buried in it: the 0.86 similarity
cutoff below which a title is reported rather than guessed at. That single
threshold is the seed of this module, generalised into a small set of signals
that can be weighed together and turned into a decision.

WHY THIS EXISTS
    Automation is only safe if the system can say which orders it is *not*
    sure about. Every rung of the trust ladder is the same code with a
    different threshold: hold everything, hold anything under 0.95, hold only
    the genuinely odd. Without a score there is nothing to turn.

WHAT IT REFUSES TO DO
    Every signal here is computed from data Opus actually has -- how the title
    matched, whether more than one catalogue entry could have matched, how many
    files the order resolves to, whether this transaction or buyer has been
    seen before. Nothing is inferred from data the tool does not hold, and
    there is no model. A score you cannot explain to the publisher whose
    business it is would be worse than no score.

DEFAULT POSTURE
    hold_below defaults to 1.01, which holds everything. Automation is opt-in,
    per rung, on evidence. The default has to be the safe one.
"""

import difflib
import re

# Verdicts
RELEASE = "release"       # safe to issue without a human
HOLD = "hold"             # a person should look before this goes out
REJECT = "reject"         # cannot be issued at all -- nothing to stamp

# Weights sum to 1.0 across the scored signals. Deliberately blunt integers
# rather than tuned decimals: these are judgements, not measurements, and
# false precision would invite treating them as measurements.
WEIGHTS = {
    "match": 0.45,        # how the title resolved -- by far the biggest risk
    "unambiguous": 0.20,  # could it have meant something else?
    "files": 0.20,        # did it resolve to a plausible set of files?
    "known_buyer": 0.10,  # have we issued to this buyer before?
    "contactable": 0.05,  # is there an address to deliver to?
}

MATCH_SCORE = {
    "exact": 1.0,
    "contains": 0.80,
    "fuzzy": 0.55,
    "none": 0.0,
}

# Mirrors opus.TITLE_MATCH_CUTOFF. Kept as its own constant so this module can
# be reasoned about (and tested) without importing the engine.
FUZZY_CUTOFF = 0.86

DEFAULT_HOLD_BELOW = 1.01   # hold everything until a rung is explicitly opened


def _normalize(text):
    """Same normalisation the engine's matcher uses: lowercase, alnum only."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def containment_candidates(item_title, catalog_titles):
    """Catalogue entries that could match this title by containment.

    The engine's matcher, when several entries contain the title, silently
    takes the longest. That is a reasonable default and an invisible risk --
    this is how the risk becomes visible.
    """
    key = _normalize(item_title)
    if not key:
        return []
    return sorted({t for t in catalog_titles
                   if _normalize(t) and (_normalize(t) in key or key in _normalize(t))})


def fuzzy_ratio(item_title, catalog_titles):
    """Best similarity between a title and any catalogue entry, 0..1."""
    key = _normalize(item_title)
    best = 0.0
    for t in catalog_titles:
        r = difflib.SequenceMatcher(None, key, _normalize(t)).ratio()
        if r > best:
            best = r
    return round(best, 3)


class Signal:
    """One observation about an order, with the reason a person would give."""

    __slots__ = ("name", "value", "weight", "note")

    def __init__(self, name, value, weight, note):
        self.name = name
        self.value = float(value)      # 0..1
        self.weight = float(weight)
        self.note = note

    def as_dict(self):
        return {"name": self.name, "value": round(self.value, 3),
                "weight": self.weight, "note": self.note}

    def __repr__(self):
        return "Signal({}={:.2f} -- {})".format(self.name, self.value, self.note)


class Assessment:
    """The score, the verdict, and every signal that produced them."""

    __slots__ = ("order_ref", "score", "verdict", "signals", "reasons")

    def __init__(self, order_ref, score, verdict, signals, reasons):
        self.order_ref = order_ref
        self.score = score
        self.verdict = verdict
        self.signals = signals
        self.reasons = reasons

    @property
    def held(self):
        return self.verdict != RELEASE

    def explain(self):
        """One line per signal, in the order a person would want to read them."""
        lines = ["{}  score {:.2f}  ->  {}".format(
            self.order_ref or "(no ref)", self.score, self.verdict.upper())]
        for s in sorted(self.signals, key=lambda s: -s.weight):
            lines.append("  {:<13} {:.2f}  {}".format(s.name, s.value, s.note))
        for r in self.reasons:
            lines.append("  ! {}".format(r))
        return "\n".join(lines)

    def as_dict(self):
        return {"order_ref": self.order_ref, "score": round(self.score, 3),
                "verdict": self.verdict,
                "signals": [s.as_dict() for s in self.signals],
                "reasons": list(self.reasons)}

    def __repr__(self):
        return "Assessment({} {:.2f} {})".format(
            self.order_ref, self.score, self.verdict)


def assess(entry, catalog_titles=(), known_refs=(), known_buyers=(),
           hold_below=DEFAULT_HOLD_BELOW, expected_parts=None):
    """Score one planned order.

    `entry` is a plan entry from opus.plan_paypal_batch: it carries buyer,
    item_title, match, matched_title, files and order_ref.

    `expected_parts` optionally maps a catalogue title to how many files that
    piece normally has, which turns "three of the four parts resolved" from
    invisible into a held order.
    """
    match = (entry.get("match") or "none").lower()
    title = entry.get("item_title") or ""
    files = entry.get("files") or []
    file_count = len(files) if hasattr(files, "__len__") else int(files or 0)
    order_ref = (entry.get("order_ref") or "").strip()
    buyer = (entry.get("buyer") or "").strip()
    email = (entry.get("email") or "").strip()

    signals, reasons = [], []

    # --- how the title resolved ------------------------------------------
    m = MATCH_SCORE.get(match, 0.0)
    if match == "fuzzy":
        ratio = fuzzy_ratio(title, catalog_titles)
        note = "matched fuzzily at {:.0%}, above the {:.0%} cutoff".format(
            ratio, FUZZY_CUTOFF)
        # A fuzzy match that only just cleared the bar is worth less than one
        # that nearly matched exactly.
        if ratio:
            span = max(1e-6, 1.0 - FUZZY_CUTOFF)
            m = 0.40 + 0.35 * min(1.0, max(0.0, (ratio - FUZZY_CUTOFF) / span))
        reasons.append("Title matched by similarity, not exactly.")
    elif match == "exact":
        note = "title matched the catalogue exactly"
    elif match == "contains":
        note = "title contains, or is contained by, a catalogue entry"
    else:
        note = "no catalogue entry matched"
        reasons.append("No catalogue entry matched this title.")
    signals.append(Signal("match", m, WEIGHTS["match"], note))

    # --- could it have meant something else? -------------------------------
    cands = containment_candidates(title, catalog_titles) if catalog_titles else []
    if match == "none":
        unambiguous, note = 0.0, "nothing to be ambiguous between"
    elif len(cands) > 1:
        unambiguous = 0.0
        note = "{} catalogue entries could match: {}".format(
            len(cands), ", ".join(cands[:3]))
        reasons.append(
            "More than one piece could match this title; the engine takes the "
            "longest and does not say so.")
    else:
        unambiguous, note = 1.0, "only one catalogue entry could match"
    signals.append(Signal("unambiguous", unambiguous, WEIGHTS["unambiguous"], note))

    # --- did it resolve to a plausible set of files? ------------------------
    expected = None
    if expected_parts and entry.get("matched_title"):
        expected = expected_parts.get(entry["matched_title"])
    if file_count == 0:
        fscore, note = 0.0, "no files resolved"
        reasons.append("The order resolves to no files.")
    elif expected and file_count < expected:
        fscore = file_count / float(expected)
        note = "{} of the {} parts this piece normally has".format(
            file_count, expected)
        reasons.append(
            "Only {} of {} expected parts resolved.".format(file_count, expected))
    else:
        fscore = 1.0
        note = "{} file(s) resolved".format(file_count)
    signals.append(Signal("files", fscore, WEIGHTS["files"], note))

    # --- have we issued to this buyer before? ------------------------------
    seen = buyer and buyer in set(known_buyers)
    signals.append(Signal(
        "known_buyer", 1.0 if seen else 0.0, WEIGHTS["known_buyer"],
        "buyer has been issued to before" if seen else "first order from this buyer"))

    # --- is there anywhere to send it? -------------------------------------
    signals.append(Signal(
        "contactable", 1.0 if email else 0.0, WEIGHTS["contactable"],
        "has an email address" if email else "no email address on the order"))

    score = round(sum(s.value * s.weight for s in signals), 3)

    # --- hard gates, which no score can override ---------------------------
    verdict = RELEASE if score >= hold_below else HOLD

    if order_ref and order_ref in set(known_refs):
        verdict = HOLD
        reasons.append("This transaction is already in the ledger.")
    if match == "none" or file_count == 0:
        verdict = REJECT      # there is literally nothing to stamp

    return Assessment(order_ref, score, verdict, signals, reasons)


def assess_plan(plan, catalog=None, known_refs=(), known_buyers=(),
                hold_below=DEFAULT_HOLD_BELOW):
    """Assess a whole plan. Returns a list of (entry, Assessment) pairs.

    `catalog` is the dict load_catalog() produces, used for the catalogue
    titles and the expected part count per piece.
    """
    titles, expected = [], {}
    for _key, value in (catalog or {}).items():
        display, files = value
        titles.append(display)
        expected[display] = len(files)

    return [(e, assess(e, catalog_titles=titles, known_refs=known_refs,
                       known_buyers=known_buyers, hold_below=hold_below,
                       expected_parts=expected))
            for e in plan]


def summarize(assessed):
    """Counts by verdict, for the line printed after a review."""
    out = {RELEASE: 0, HOLD: 0, REJECT: 0}
    for _entry, a in assessed:
        out[a.verdict] = out.get(a.verdict, 0) + 1
    return out

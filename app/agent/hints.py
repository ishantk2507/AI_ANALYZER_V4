"""Deterministic question analysis.

A 3B model asked "which two wheeler makers grew fastest" will cheerfully route
to Three Wheeler and forget the growth. Both facts are recoverable from the
question text with plain string matching, which is free, instant and does not
hallucinate -- so they are computed here and used to pin the model's choices
rather than hoped for.

Nothing here is Vahan-specific: breakdown scoring is driven by the real table
names and the real values in their columns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from app.data.catalog import ROOT_CATEGORY, Catalog

_PUNCT = re.compile(r"[^a-z0-9]+")

#: "2 wheeler" and "2W" both mean "Two Wheeler".
_DIGIT_WORDS = [
    (re.compile(r"\b2\s*w(?:heelers?)?\b"), "two wheeler"),
    (re.compile(r"\b3\s*w(?:heelers?)?\b"), "three wheeler"),
    (re.compile(r"\b4\s*w(?:heelers?)?\b"), "four wheeler"),
    (re.compile(r"\btwo[- ]wheelers?\b"), "two wheeler"),
    (re.compile(r"\bthree[- ]wheelers?\b"), "three wheeler"),
    (re.compile(r"\bfour[- ]wheelers?\b"), "four wheeler"),
]

#: Words that survive stemming badly, mapped onto the table name they imply.
#: Other words for the dimension *itself*. Naming one says what to group by.
_BREAKDOWN_SYNONYMS = {
    "MAKER": ("manufacturer", "manufacturers", "oem", "brand", "brands", "company", "companies"),
    "FUEL": ("powertrain", "electrification"),
    "CLASS": ("bodytype", "body type", "vehicle type"),
    "NORM": ("emission", "emissions"),
}

#: Words naming a *value* of the dimension. They pin the table just as firmly --
#: "ev registrations" has to be read from FUEL -- but they say nothing about the
#: grouping, which is the distinction `_match_breakdown` returns as `named`.
#: Listing "ev" as a synonym made "highest three wheeler **ev** registration in
#: which **state**" group by FUEL and answer with one electric row, ignoring the
#: state it was asked for. They are kept apart from the sample-value scan below
#: because the stored values are spelled "ELECTRIC(BOV)" and "BHARAT STAGE VI",
#: which no one types.
_BREAKDOWN_VALUE_WORDS = {
    "FUEL": ("ev", "evs", "electric"),
    "NORM": ("bharat stage", "bs6", "bs4", "euro"),
}

_GROWTH = re.compile(
    r"\b(grew|grow|grows|growing|growth|increase[sd]?|increasing|rise|rising|rose|"
    r"decline[sd]?|declining|fell|fall|falling|drop(?:ped)?|shrank|shrink(?:ing)?|"
    r"yoy|year[- ]on[- ]year|year[- ]over[- ]year|change[sd]?|trend(?:ing)?|"
    r"faster|fastest|slowest|momentum)\b",
    re.IGNORECASE,
)
_SHARE = re.compile(
    r"\b(share|shares|percentage|percent|proportion|penetration|mix|split|"
    r"contribution|dominance)\b|%",
    re.IGNORECASE,
)
_BOTTOM = re.compile(
    r"\b(bottom|least|lowest|worst|smallest|fewest|slowest|weakest)\b", re.IGNORECASE
)
_TREND = re.compile(r"\b(over time|by year|per year|each year|trend|timeline)\b", re.IGNORECASE)

#: "total vehicle registrations in Tamil Nadu" wants one number, not a breakdown.
_TOTAL = re.compile(
    r"\b(total|overall|altogether|aggregate|combined|sum of|how many|how much|"
    r"number of|count of)\b",
    re.IGNORECASE,
)
#: ...unless the question also asks for it to be split up, in which case the
#: total word is describing the measure ("total registrations by city").
_GROUPING = re.compile(
    r"\b(by|per|wise|each|every|which|breakdown|split|across|distribution|"
    r"top|bottom|rank(?:ed|ing)?|compare|versus|vs)\b",
    re.IGNORECASE,
)
#: "top 10 makers" is a count. "top 2 wheeler makers" is a vehicle category that
#: happens to follow the word "top", and reading it as a count returns two rows.
_NOT_A_COUNT = r"(?!\s*[-\s]?wheelers?\b)"
_TOP_N = re.compile(
    r"\b(?:top|bottom|first|last|best|worst)\s+(\d{1,3})\b" + _NOT_A_COUNT, re.IGNORECASE
)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")

#: Fewer rows than this is not a ranking, so an unrequested limit below it is
#: treated as an arbitrary model choice rather than an instruction.
MIN_RANKING_ROWS = 5
DEFAULT_RANKING_ROWS = 10

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20,
}
_TOP_WORD = re.compile(
    r"\b(?:top|bottom|first|last|best|worst)\s+(" + "|".join(_NUMBER_WORDS) + r")\b"
    + _NOT_A_COUNT,
    re.IGNORECASE,
)


@dataclass
class QuestionHints:
    category: Optional[str] = None
    breakdown: Optional[str] = None
    #: True when the question used the dimension's own word ("fuel split"),
    #: rather than only one of its values ("electric three wheelers").
    breakdown_named: bool = False
    wants_growth: bool = False
    wants_share: bool = False
    wants_bottom: bool = False
    wants_trend: bool = False
    #: The question asks for a single figure, not a breakdown.
    wants_total: bool = False
    #: The superlative is about size, not growth: rank by volume.
    wants_volume_order: bool = False
    #: The normalised question, so plan repairs can look for dimension names.
    normalised: str = ""
    limit: Optional[int] = None
    years: List[str] = field(default_factory=list)

    @property
    def routing_is_certain(self) -> bool:
        """True when the question names both the category and the table itself."""
        return self.category is not None and self.breakdown is not None

    def render(self) -> str:
        """A short HINT block for the planning prompt."""
        lines = []
        if self.wants_growth:
            lines.append(
                "- The question is about CHANGE BETWEEN PERIODS: "
                'set "pivot_on" to the period column and "derive" to "growth_pct".'
            )
        elif self.wants_share:
            lines.append('- The question is about SHARE OF TOTAL: set "derive" to "share_pct".')
        elif self.wants_trend:
            lines.append('- The question is about a TREND OVER TIME: set "chart" to "line".')
        if self.wants_bottom:
            lines.append('- The question asks for the SMALLEST values: set "sort_desc" to false.')
        if self.limit:
            lines.append(f'- The question asks for {self.limit} rows: set "limit" to {self.limit}.')
        if self.years:
            lines.append(f"- Periods named in the question: {', '.join(self.years)}.")
        return "HINTS:\n" + "\n".join(lines) if lines else ""


def _normalise(text: str) -> str:
    lowered = text.lower()
    for pattern, replacement in _DIGIT_WORDS:
        lowered = pattern.sub(replacement, lowered)
    cleaned = " " + _PUNCT.sub(" ", lowered).strip() + " "

    # Rewrite place names to the spelling the data uses, so the token index finds
    # them: a question about Bangalore has to look for Bengaluru.
    from app.data.places import ALIASES

    for typed, actual in ALIASES.items():
        if typed != actual and f" {typed} " in cleaned:
            cleaned = cleaned.replace(f" {typed} ", f" {actual} ")
    return cleaned


def _desuffix(text: str) -> str:
    """Drop a trailing plural "s" from each word.

    Both sides of the comparison need it, and for opposite reasons: the user
    types "ambulances" for the category `Ambulance`, and types "goods vehicle"
    for `Goods Vehicles`. Crude on purpose -- the categories are a fixed list of
    ten and none of them hinge on an irregular plural.
    """
    return " ".join(
        word[:-1] if len(word) > 3 and word.endswith("s") else word
        for word in text.split()
    )


def _match_category(normalised: str, catalog: Catalog) -> Optional[str]:
    """Pin the category only when exactly one is named. Ambiguity goes to the model."""
    # "the most ambulances in 2023" left the category unmatched, so the answer
    # was combined across all ten -- and, mid-conversation, left the next
    # question with no category to inherit either.
    singular = f" {_desuffix(normalised)} "
    matches = [
        category
        for category in catalog.categories
        if category != ROOT_CATEGORY
        and f" {_desuffix(_PUNCT.sub(' ', category.lower()).strip())} " in singular
    ]
    return matches[0] if len(matches) == 1 else None


def _match_breakdown(normalised: str, catalog: Catalog) -> tuple:
    """Score each table by its name and by values that live in its column.

    Returns (breakdown, named_explicitly). "named" means the question used the
    dimension's own word ("fuel split"), not merely one of its values ("electric
    three wheelers") -- only the former says what to group by.
    """
    scores = {breakdown: 0 for breakdown in catalog.breakdowns}
    named: set = set()

    for breakdown in catalog.breakdowns:
        stem = _PUNCT.sub(" ", breakdown.lower()).strip()
        if not stem:
            continue
        # "maker" matches "makers"; "class" matches "classes".
        if re.search(rf"\b{re.escape(stem)}(?:s|es)?\b", normalised):
            scores[breakdown] += 3
            named.add(breakdown)

        for synonym in _BREAKDOWN_SYNONYMS.get(breakdown.upper(), ()):
            if f" {synonym} " in normalised:
                scores[breakdown] += 3
                named.add(breakdown)
                break

        # Scores the same, so routing is unchanged; deliberately not `named`.
        for word in _BREAKDOWN_VALUE_WORDS.get(breakdown.upper(), ()):
            if f" {word} " in normalised:
                scores[breakdown] += 3
                break

        entry = next(
            (e for e in catalog.entries.values() if e.breakdown == breakdown), None
        )
        if entry is None:
            continue
        column = entry.profile.column(breakdown)
        if column is None:
            continue
        for value in column.sample_values:
            token = _PUNCT.sub(" ", str(value).lower()).strip()
            if len(token) >= 3 and f" {token} " in normalised:
                scores[breakdown] += 2
                break

    ranked = sorted(scores.items(), key=lambda item: -item[1])
    if not ranked or ranked[0][1] == 0:
        return None, False
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None, False  # tied: let the model decide
    return ranked[0][0], ranked[0][0] in named


def analyse_question(question: str, catalog: Optional[Catalog] = None) -> QuestionHints:
    normalised = _normalise(question)
    hints = QuestionHints(normalised=normalised)

    if catalog is not None and not catalog.is_empty():
        hints.category = _match_category(normalised, catalog)
        hints.breakdown, hints.breakdown_named = _match_breakdown(normalised, catalog)
        if hints.category and hints.breakdown:
            # A table the question names but that category does not have is not certain.
            if hints.breakdown not in catalog.breakdowns_for(hints.category):
                hints.breakdown = None

    hints.wants_growth = bool(_GROWTH.search(question))
    hints.wants_share = bool(_SHARE.search(question))
    hints.wants_bottom = bool(_BOTTOM.search(question))
    hints.wants_trend = bool(_TREND.search(question))
    hints.wants_total = bool(_TOTAL.search(question)) and not _GROUPING.search(question)
    hints.wants_volume_order = bool(_BY_VOLUME.search(question))
    hints.years = sorted(set(_YEAR.findall(question)))

    match = _TOP_N.search(question)
    if match:
        hints.limit = max(1, int(match.group(1)))
    else:
        word = _TOP_WORD.search(question)
        if word:
            hints.limit = _NUMBER_WORDS[word.group(1).lower()]

    return hints


#: At most this many filters are inferred, so a value-rich question cannot
#: silently narrow the data to nothing.
MAX_INFERRED_FILTERS = 2
#: Shorter values match too loosely ("EV", "CNG" appear inside other words).
MIN_INFERRABLE_VALUE = 4
#: A question word matching more values than this is a structural word, not an
#: identifying one -- "RTO" appears in hundreds of RTO names, "Pune" in one or two.
MAX_VALUES_PER_TOKEN = 6
#: Structural parts of a value's name, never the identifying part.
_VALUE_STOPWORDS = {
    # Office types and corporate boilerplate.
    "rto", "dto", "srto", "rta", "arto", "rla", "sdm", "mvi", "office", "unit",
    "ltd", "limited", "pvt", "private", "india", "indian", "motor", "motors",
    "auto", "autos", "company", "corporation", "vehicle", "vehicles",
    # Words a user uses to *ask* about places, not to name one. Without these,
    # "which city has the most..." matches every RTO called "<X> CITY".
    "city", "cities", "town", "district", "districts", "state", "states",
    "region", "regions", "area", "areas", "zone", "nagar", "urban", "rural",
    "north", "south", "east", "west", "central",
    "only", "not", "applicable", "available",
    "first", "second", "new", "old", "top", "bottom", "most", "least",
    # Ordinary English. Without these a preposition can pick a filter: "cities
    # **with** highest registrations" matched six CLASS values containing WITH
    # ("VEHICLE FITTED WITH RIG", ...) and cut the answer to 228 of 46,230 rows.
    "the", "and", "for", "with", "without", "from", "into", "onto", "over",
    "under", "per", "about", "across", "between", "among", "within", "during",
    "after", "before", "than", "then", "that", "this", "these", "those",
    "what", "which", "who", "whom", "whose", "where", "when", "how", "why",
    "are", "was", "were", "has", "have", "had", "been", "being", "does", "did",
    "all", "any", "each", "every", "some", "many", "much", "more", "less",
    "show", "give", "list", "tell", "find", "get", "see", "compare", "versus",
    # Words that ask for a decomposition, which `_GROUPING` already reads as
    # structural -- they must not double as identifying ones. "What is the city
    # **breakdown** for Manipur?" matched the CLASS value BREAKDOWN VAN and
    # returned 0 of 46,230 rows. Worse, that question is one the app suggests
    # itself (insights.py builds "the <dimension> breakdown for <label>"), so
    # clicking a follow-up produced an empty answer. A question genuinely about
    # the vehicle still works: pass 1 matches the whole value, "breakdown van".
    "breakdown", "breakdowns", "distribution", "rank", "ranked", "ranking",
    "growth", "grew", "change", "changed", "total", "share", "split", "count",
    "number", "numbers", "data", "value", "values", "highest", "lowest",
    "registration", "registrations", "registered", "sales", "sold", "units",
    "year", "years", "yearly", "annual", "wise", "also", "but", "its",
}


#: "in Pune", "for Kerala", "at Bengaluru" - the value is a scope, not the
#: subject. Contrast "share of petrol", where petrol is what is being broken
#: down and filtering to it would make the answer 100%.
#: The words are captured by lookahead so the match consumes only the
#: preposition. Consuming them made `finditer` skip a later preposition that sat
#: inside a previous capture: "...for three wheeler in pune" matched only "for",
#: swallowed the "in", and never saw "pune".
_SCOPING = re.compile(
    r"\b(?:in|at|within|inside|across|for|from)\s+(?=((?:[a-z0-9]+\s+){1,3}))"
)


def _scoped_tokens(normalised: str) -> set:
    """Words that follow a scoping preposition."""
    tokens: set = set()
    for match in _SCOPING.finditer(normalised):
        tokens.update(match.group(1).split())
    return tokens


def _token_index(values: List[str]) -> dict:
    """token -> the values containing it, for one column."""
    index: dict = {}
    for value in values:
        for token in _PUNCT.sub(" ", str(value).lower()).split():
            if len(token) >= 3 and token not in _VALUE_STOPWORDS:
                index.setdefault(token, set()).add(str(value))
    return index


def _consolidate_places(matched: dict, question_tokens: set, values_by_column: dict,
                        profile) -> None:
    """Put every named place on one geographic level.

    Tokens are claimed by one column each, so "compare Mumbai, Pune and Delhi"
    became `STATE == Delhi AND CITY in (Mumbai, Pune)` -- an impossible
    intersection returning nothing. Delhi *is* expressible as a city ("New
    Delhi", "Old Delhi"), so the whole list belongs on CITY.

    Picks the level covering the most of the named places, preferring the finest
    when tied, and leaves single-level matches untouched.
    """
    available = [c.name for c in profile.columns]
    geography = [name for name in GEOGRAPHIC_SCOPE if name in available]
    present = [name for name in geography if name in matched]
    if len(present) < 2:
        return

    # The words that produced those matches -- what the user actually named.
    named = {
        token
        for column in present
        for value in matched[column]
        for token in _PUNCT.sub(" ", str(value).lower()).split()
    } & question_tokens

    best_column, best_hits, best_covered = None, None, -1
    for column in geography:  # finest first, so a tie favours CITY over STATE
        index = _token_index(values_by_column.get(column, []))
        covered, hits = set(), set()
        for token in named:
            found = index.get(token)
            if found and len(found) <= MAX_VALUES_PER_TOKEN:
                covered.add(token)
                hits |= found
        if len(covered) > best_covered:
            best_column, best_hits, best_covered = column, hits, len(covered)

    if best_column is None or not best_hits:
        return

    for column in present:
        matched.pop(column, None)
    matched[best_column] = best_hits


def infer_missing_filters(
    question: str, frame, profile, spec, notes: List[str],
    redundant_value: Optional[str] = None,
) -> None:
    """Add a filter the question plainly states but the plan left out.

    Seen with the real model: "top 5 RTOs **in Kerala**" planned with no filter
    at all, returning Tamil Nadu. Values are matched against what is actually in
    the column, so this can only ever add a filter that would match rows.

    Columns already grouped by are skipped -- "the share of petrol" wants
    `group_by=[FUEL]` with no filter, not `FUEL == PETROL`, which would be 100%.
    """
    from app.analysis.spec import Filter
    from app.data.matching import distinct_values

    normalised = _normalise(question)
    already_filtered = {f.column for f in spec.filters}
    question_tokens = set(normalised.split())

    def eligible(column) -> bool:
        name = column.name
        return (
            column.kind == "categorical"
            and name in frame.columns
            and name not in already_filtered
            and name != spec.pivot_on
        )

    scoped = _scoped_tokens(normalised)

    def applies(name: str, hits: set) -> bool:
        """Decide whether a grouped column may also be filtered.

        "share of petrol" wants `group_by=[FUEL]` and no filter -- narrowing to
        PETROL makes it 100%. But two other shapes do want the filter:

        - "compare Mumbai and Bangalore" names two values: a comparison.
        - "how many three wheelers **in** Pune" names one, after a scoping
          preposition. Without this the answer ranked every city and ignored Pune.
        """
        if name not in spec.group_by or len(hits) >= 2:
            return True
        hit_tokens = {
            token
            for value in hits
            for token in _PUNCT.sub(" ", str(value).lower()).split()
        }
        return bool(hit_tokens & scoped)

    candidates = [c for c in profile.dimensions if c.kind == "categorical"
                  and c.name in frame.columns]
    values_by_column = {c.name: distinct_values(frame[c.name]) for c in candidates}

    #: Tokens already explained by a stronger match, so no other column may claim
    #: them. "Delhi" is a STATE value *and* appears inside RTO names; matching
    #: both would filter Delhi down to its two named RTOs.
    consumed: set = set()
    matched: dict = {}

    # Pass 1: whole-value matches. Strongest signal, so it claims its tokens
    # first -- even for a column already filtered, which is what stops a
    # model-supplied STATE filter from being shadowed by an RTO one.
    for column in candidates:
        hits = {
            str(value)
            for value in values_by_column[column.name]
            if len(_PUNCT.sub(" ", str(value).lower()).strip()) >= MIN_INFERRABLE_VALUE
            and f" {_PUNCT.sub(' ', str(value).lower()).strip()} " in normalised
        }
        if not hits:
            continue
        consumed |= {t for value in hits for t in _PUNCT.sub(" ", value.lower()).split()}
        if eligible(column):
            matched[column.name] = hits

    # Pass 2: distinctive-token matches, for values carrying a code or suffix the
    # user will not type: "Pune" -> "PUNE - MH12", "KL1" -> "TRIVANDRUM RTO - KL1".
    # A city with several offices matches them all, which is what was meant.
    for column in candidates:
        if column.name in matched or not eligible(column):
            continue
        index = _token_index(values_by_column[column.name])
        hits = set()
        for token in question_tokens - consumed:
            if len(token) < 3 or token in _VALUE_STOPWORDS:
                continue
            found = index.get(token)
            if found and len(found) <= MAX_VALUES_PER_TOKEN:
                hits |= found
        if hits:
            consumed |= {t for value in hits for t in _PUNCT.sub(" ", value.lower()).split()}
            matched[column.name] = hits

    from app.data.places import CITY_COLUMN, states_for_place

    # A value that just repeats the folder the table came from adds nothing --
    # and on a table whose CATEGORY column is dirty, filtering by it loses rows.
    if redundant_value:
        target = str(redundant_value).strip().lower()
        matched = {
            name: {v for v in hits if str(v).strip().lower() != target}
            for name, hits in matched.items()
        }
        matched = {name: hits for name, hits in matched.items() if hits}

    _consolidate_places(matched, question_tokens, values_by_column, profile)

    wanted = {n: h for n, h in matched.items() if applies(n, h)}

    for name, hits in list(wanted.items())[:MAX_INFERRED_FILTERS]:
        ordered = sorted(hits)
        if len(ordered) == 1:
            spec.filters.append(Filter(column=name, op="==", value=ordered[0]))
            notes.append(f"question named '{ordered[0]}'; filtered {name} to it")
        else:
            joined = ", ".join(ordered[:MAX_VALUES_PER_TOKEN])
            spec.filters.append(Filter(column=name, op="in", value=joined))
            notes.append(f"question matched {len(ordered)} {name} values; filtered to {joined}")

        # City names repeat across India -- Bilaspur is an office in three
        # different states. Summing them is rarely what was meant.
        if name == CITY_COLUMN:
            for value in ordered:
                states = states_for_place(frame, value)
                if len(states) > 1:
                    notes.append(
                        f"'{value}' exists in {len(states)} states "
                        f"({', '.join(states)}); all are included - "
                        "name the state to narrow it"
                    )


def prefer_total_metric(question: str, profile, spec, notes: List[str]) -> None:
    """Fall back to the headline measure when the plan picked jargon.

    The model answered "two wheeler registrations" with `2WIC` -- a real column,
    but a sub-category. If the question never mentions the chosen measure and the
    table has a TOTAL, TOTAL is what "registrations" means.
    """
    measures = {c.name.upper(): c.name for c in profile.measures}
    total = measures.get("TOTAL")
    if not total or not spec.metric or spec.metric == total:
        return

    normalised = _normalise(question)
    chosen = _PUNCT.sub(" ", spec.metric.lower()).strip()
    if chosen and f" {chosen} " in normalised:
        return  # the question asked for that column by name

    notes.append(f"question did not name '{spec.metric}'; used '{total}' instead")
    spec.metric = total


#: "the cities with the highest registrations", "the biggest makers" -- the
#: superlative is about size, not about growth, so a growth question phrased
#: this way should rank by volume.
_BY_VOLUME = re.compile(
    r"\b(?:highest|largest|biggest|top|most|leading|major)\s+(?:\w+\s+){0,3}?"
    r"(?:registrations?|sales|volumes?|numbers?|totals?)\b"
    r"|\b(?:biggest|largest|leading|major)\s+\w+s?\b",
    re.IGNORECASE,
)


#: Broad to narrow. Comparing across states means dropping any city or RTO
#: scope too -- "how does UP compare with other states?" filtered to UP *and*
#: Bahraich, so grouping by STATE returned a single row.
SCOPE_HIERARCHY = ["CATEGORY", "STATE", "CITY", "RTO"]
#: The place levels. "in the country" clears these and leaves CATEGORY alone,
#: so "two wheelers in the country" stays a two-wheeler question.
GEOGRAPHIC_SCOPE = ["STATE", "CITY", "RTO"]

#: An explicit instruction to answer for everywhere. Without this, "show the
#: vehicles registered in the country" asked after a Karnataka question named no
#: place of its own, so it inherited Karnataka and reported 3.7M as the national
#: total.
#: Bare "india" and "overall" are deliberately excluded: the first appears
#: inside maker names ("MARUTI SUZUKI INDIA LTD"), the second is an ordinary
#: word, and either would silently drop a legitimate place filter from
#: "overall registrations in Kerala".
_NATIONWIDE = re.compile(
    r"\bcountry\s?wide\b|\bnation(?:al|ally|wide)\b"
    r"|\b(?:the\s+|whole\s+|entire\s+)*country\b"
    r"|\b(?:in|across|for|throughout|all over)\s+india\b|\bpan[\s-]?india\b"
    r"|\ball\s+(?:the\s+)?states\b|\bevery\s+state\b|\beverywhere\b",
    re.IGNORECASE,
)

#: The category counterpart of `_NATIONWIDE`: a question about the whole fleet
#: rather than about whatever the last one was about. "Vehicle" on its own is
#: the generic term -- the four categories whose own names contain it ("Goods
#: Vehicles", "Public Service Vehicle", ...) set the category outright, so a
#: bare "vehicle" here always means "any of them".
_ALL_CATEGORIES = re.compile(
    r"\bvehicles?\b|\ball\s+(?:the\s+)?categor(?:y|ies)\b"
    r"|\b(?:every|each)\s+category\b|\bacross\s+categories\b",
    re.IGNORECASE,
)


def clears_scope(question: str) -> bool:
    """Whether the question explicitly asks for everywhere, not the current scope."""
    return bool(_NATIONWIDE.search(_normalise(question)))


def asks_all_categories(question: str) -> bool:
    """Whether the question is about every vehicle category, not the current one.

    The category counterpart of `clears_scope`. "Total vehicle registrations in
    Tamil Nadu" asked during a conversation about ambulances is a fresh, fleet-
    wide question; "what is the city breakdown for Manipur?" is not.
    """
    return bool(_ALL_CATEGORIES.search(_normalise(question)))


def drop_geographic_scope(spec, profile, notes: List[str]) -> None:
    """Remove every place filter, leaving non-place ones (like CATEGORY) alone."""
    available = {c.name for c in profile.columns}
    places = {name for name in GEOGRAPHIC_SCOPE if name in available}

    dropped = [f for f in spec.filters if f.column in places]
    if not dropped:
        return
    spec.filters = [f for f in spec.filters if f.column not in places]
    listed = ", ".join(f"{f.column} {f.op} {f.value}" for f in dropped)
    notes.append(f"the question asked for the whole country, so dropped {listed}")

#: "compare with the other states", "vs all cities", "across states".
_PEER_PHRASE = re.compile(
    r"\b(?:other|others|all|across|remaining|rest of the|every)\s+(\w+)\b", re.IGNORECASE
)
#: ...but only when the sentence is actually a comparison.
_COMPARISON = re.compile(
    r"\b(compare[ds]?|comparison|compares|versus|vs\.?|against|rank(?:ed|ing)?|"
    r"relative to|stack(?:s|ed)? up|how does|how do)\b",
    re.IGNORECASE,
)


def _matches_dimension(word: str, name: str) -> bool:
    stem = _PUNCT.sub(" ", name.lower()).strip()
    word = word.lower()
    return word in {stem, stem + "s", stem + "es",
                    (stem[:-1] + "ies") if stem.endswith("y") else stem + "s"}


def widening_target(question: str, dimensions: List[str]) -> Optional[str]:
    """The dimension a question asks to zoom out to, if any.

    "How does Uttar Pradesh compare with the other states?" is a request to
    leave Uttar Pradesh -- keeping the filter makes the comparison impossible.
    """
    normalised = _normalise(question)
    if not _COMPARISON.search(normalised):
        return None

    for match in _PEER_PHRASE.finditer(normalised):
        word = match.group(1)
        for name in dimensions:
            if _matches_dimension(word, name):
                return name
    return None


def narrower_than(target: str, profile) -> set:
    """`target` and every scope below it -- what a zoom-out has to let go of."""
    available = {c.name for c in profile.columns}
    if target not in available:
        return set()
    order = [name for name in SCOPE_HIERARCHY if name in available]
    return set(order[order.index(target):]) if target in order else {target}


def widen_scope(spec, target: str, profile, notes: List[str]) -> None:
    """Zoom out to `target`: drop its filter and every narrower one, and group by it.

    Filters *broader* than the target stay, so "how does Pune compare with the
    other cities?" asked inside Maharashtra still compares within Maharashtra.
    """
    narrower = narrower_than(target, profile)
    if not narrower:
        return

    dropped = [f for f in spec.filters if f.column in narrower]
    if dropped:
        spec.filters = [f for f in spec.filters if f.column not in narrower]
        listed = ", ".join(f"{f.column} {f.op} {f.value}" for f in dropped)
        notes.append(f"comparing across {target}, so dropped {listed}")

    if spec.group_by != [target]:
        spec.group_by = [target]
        notes.append(f"grouped by {target} to compare across it")


#: Phrases that only make sense against a previous answer.
_CONTINUATION = re.compile(
    r"\b(that|those|these|them|they|their|it|its|there|same|again|instead|"
    r"also|too|what about|how about|and for|break (?:it|that) down)\b",
    re.IGNORECASE,
)


def continues_previous(question: str, spec, previous_filters) -> bool:
    """Whether a question should be answered under the previous answer's scope.

    Two ways in: the question refers back explicitly ("break that down by
    city"), or it introduces no scope of its own while the previous answer had
    one -- "how did city registrations change year on year?" asked straight
    after "city wise registrations in Haryana" means *in Haryana*.

    A question that names its own scope is treated as a fresh start, so "top
    makers in Pune" after a Haryana question does not silently stay in Haryana.
    """
    if not previous_filters:
        return False
    if clears_scope(question):
        return False  # "in the country" is the opposite of "same as before"
    if _CONTINUATION.search(question):
        return True
    return not spec.filters


#: A word that names a whole *set* of values rather than one, as
#: (question words, what belongs, what does not). The stored spellings are not
#: what anyone types and there is more than one of them, so "three wheeler ev
#: registrations" was planned as `FUEL == ELECTRIC(BOV)` and silently dropped
#: PURE EV -- 333,610 three-wheelers, 22% of the real electric total.
#: Hybrids carry "EV" in their names (PLUG-IN HYBRID EV, STRONG HYBRID EV) and
#: are deliberately left out: "EV registrations" means battery-electric, and
#: hybrids are counted as their own thing.
_VALUE_GROUPS = {
    "FUEL": (
        ("ev", "evs", "electric"),
        re.compile(r"\belectric\b|\bpure\s+ev\b", re.IGNORECASE),
        re.compile(r"\bhybrid\b", re.IGNORECASE),
    ),
}


def merge_repeated_filters(spec, notes: List[str]) -> None:
    """Union filters the plan repeated on a single column.

    Two entries are ANDed, and a row has one fuel, so `FUEL in ELECTRIC(BOV)`
    alongside `FUEL in PURE EV` can only ever match nothing. Asked for
    "electric two wheeler registrations by state" the model wrote three of
    them and the answer came back empty. `!=` is left alone -- repeated
    exclusions do mean "and".
    """
    from app.analysis.spec import Filter

    by_column: dict = {}
    for item in spec.filters:
        if item.op in ("==", "in"):
            by_column.setdefault(item.column, []).append(item)

    for column, items in by_column.items():
        if len(items) < 2:
            continue

        values: List[str] = []
        for item in items:
            for part in str(item.value).split(","):
                part = part.strip()
                if part and part not in values:
                    values.append(part)
        for item in items:
            spec.filters.remove(item)

        joined = ", ".join(values)
        spec.filters.append(Filter(column=column, op="in", value=joined))
        notes.append(f"the plan filtered {column} {len(items)} times over, which "
                     f"cannot all hold at once; read as any of {joined}")


def widen_value_group(question: str, frame, profile, spec, notes: List[str]) -> None:
    """Cover every spelling of a value the question named with a single word.

    Runs after `infer_missing_filters`, which cannot do this: "ev" is two
    characters and appears in no value verbatim, so nothing matches it.
    """
    from app.analysis.spec import Filter
    from app.data.matching import distinct_values

    normalised = _normalise(question)

    for column, (words, belongs, excluded) in _VALUE_GROUPS.items():
        # Grouping by the column is a request to see the values side by side --
        # "the ev and petrol split" must not be narrowed to the EVs.
        if column not in frame.columns or column in spec.group_by:
            continue
        if not any(f" {word} " in normalised for word in words):
            continue

        members = {
            str(value)
            for value in distinct_values(frame[column])
            if belongs.search(str(value)) and not excluded.search(str(value))
        }
        if len(members) < 2:
            continue  # nothing to widen to

        existing = [f for f in spec.filters if f.column == column]
        if existing:
            named = {
                part.strip()
                for item in existing
                for part in str(item.value).split(",")
                if part.strip()
            }
            # Only take over a filter that was already reaching for this group.
            # A model that filtered FUEL to DIESEL meant DIESEL -- but one that
            # listed the EVs and swept a hybrid in with them was reaching, so
            # overlap is the test, not containment.
            if not (named & members):
                continue
            for item in existing:
                spec.filters.remove(item)

        joined = ", ".join(sorted(members))
        spec.filters.append(Filter(column=column, op="in", value=joined))
        notes.append(f"'{words[0]}' covers {len(members)} {column} values; "
                     f"included all of them ({joined})")


def ranked_dimensions(question: str, spec) -> set:
    """Columns the new question ranks by, which must not inherit a filter.

    "Which states registered the most tractors?" asked after a Manipur question
    groups by STATE, and carrying `STATE == Manipur` over answers a ranking
    with a single row. A question that points back is exempt -- in "how did
    *they* change year on year?" the previous values are the subject, not a
    scope to drop.
    """
    if _CONTINUATION.search(question):
        return set()
    return {column for column in spec.group_by if column}


def inherit_filters(spec, previous_filters, profile, notes: List[str],
                    skip: Optional[set] = None) -> None:
    """Carry forward the previous scope for columns the new plan left open.

    `skip` holds columns a widening is about to drop, so the notes do not read
    "carried STATE == Uttar Pradesh over" followed immediately by "dropped
    STATE == Uttar Pradesh".
    """
    from app.analysis.spec import Filter

    available = {c.name for c in profile.columns}
    already = {f.column for f in spec.filters}
    skip = skip or set()

    for column, op, value in previous_filters:
        if column in already or column not in available or column in skip:
            continue
        spec.filters.append(Filter(column=column, op=op, value=value))
        notes.append(f"carried '{column} {op} {value}' over from the previous question")


def _named_dimension(hints: QuestionHints, profile) -> Optional[str]:
    """The dimension the question asks to be broken down by, if it names one.

    Naming the column itself is the signal -- "city wise", "by category", "the
    fuel split" all say what to group by. A *value* of a column reads as a
    filter instead ("in Pune" names no column, so nothing matches here), which
    is what keeps this from hijacking scoped questions.

    The breakdown table's own name wins when both are present.
    """
    normalised = hints.normalised
    if not normalised:
        return None

    dimensions = [c.name for c in profile.dimensions]

    if hints.breakdown_named and hints.breakdown:
        for name in dimensions:
            if name.upper() == hints.breakdown.upper():
                return name

    # Non-temporal first. "How did **city** registrations change from **year**
    # to year" names both, but the year is the period to compare across, not the
    # thing to group by -- matching YEAR first left the grouping untouched and
    # the answer came back as two national rows.
    temporal = {c.name for c in profile.columns if c.kind == "temporal"}
    ordered = [n for n in dimensions if n not in temporal]
    if not hints.wants_growth and not hints.wants_trend:
        ordered += [n for n in dimensions if n in temporal]

    for name in ordered:
        stem = _PUNCT.sub(" ", name.lower()).strip()
        if stem and re.search(rf"\b{re.escape(stem)}(?:s|es|ies)?\b", normalised):
            return name
    return None


def apply_hints(spec, hints: QuestionHints, profile, notes: List[str]):
    """Repair a plan that ignored what the question plainly said.

    Only ever *adds* an intent the question stated and the plan lacks; it never
    overrides a choice the model actively made in the other direction.
    """
    from app.analysis.spec import NONE

    if hints.wants_growth and spec.derive == NONE:
        temporal = next((c.name for c in profile.columns if c.kind == "temporal"), None)
        if temporal:
            spec.derive = "growth_pct"
            if spec.pivot_on == NONE:
                spec.pivot_on = temporal
            notes.append("question asked about growth; compared periods")

    elif hints.wants_share and spec.derive == NONE:
        spec.derive = "share_pct"
        notes.append("question asked about share; added percentage of total")

    # "total vehicle registrations in Tamil Nadu" came back as a CLASS breakdown.
    # A question asking for one figure, with nothing asking for it to be split,
    # wants one figure.
    if hints.wants_total and spec.group_by and not hints.breakdown_named:
        spec.group_by = []
        spec.derive = NONE
        notes.append("question asked for a single total; removed the breakdown")

    # "the fuel split ... in pune" was planned as `by CITY`, and "city wise
    # registrations" as `by CLASS`: the model grouped by the scope, or by
    # whatever the table was named after, instead of the dimension the question
    # actually asked for.
    else:
        column = _named_dimension(hints, profile)
        if column and column not in spec.group_by:
            spec.group_by = [column]
            notes.append(f"question asked about {column}; grouped by it")

    # "the growth in the cities with the highest registrations" wants the big
    # cities' growth. Ranking by percent -- the default for a growth question --
    # answers about the smallest instead.
    if hints.wants_volume_order and spec.sort_by == "auto" and spec.derive != NONE:
        spec.sort_by = "value"
        notes.append("ranked by size, since the question asked about the largest")

    if hints.wants_bottom and spec.sort_desc:
        spec.sort_desc = False
        notes.append("question asked for the smallest values; sorted ascending")
    elif not hints.wants_bottom and not spec.sort_desc:
        # No "bottom"/"least" anywhere in the question, so ascending is the model
        # being arbitrary -- a ranking reads largest-first.
        spec.sort_desc = True
        notes.append("no order was asked for; sorted largest first")

    if hints.limit and spec.limit != hints.limit:
        spec.limit = hints.limit
        notes.append(f"question asked for {hints.limit} rows")
    elif not hints.limit and spec.group_by and spec.limit < MIN_RANKING_ROWS:
        # "top makers in Bangalore" came back with two rows. The question named
        # no count, so this is the model being stingy, not an instruction.
        spec.limit = DEFAULT_RANKING_ROWS
        notes.append(f"no row count was asked for; showed the top {DEFAULT_RANKING_ROWS}")

    return spec

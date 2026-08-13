"""The agent loop.

Five fixed stages rather than an open-ended ReAct loop:

    route -> plan -> execute -> chart -> narrate

Only stages 1, 2 and 5 involve the model, and 1 and 2 are decoded against a JSON
Schema built from the real catalog and the real column names. That is what makes
a 3B model workable here: it never chooses an invalid table or column, so the
failure modes that sink small models on free-form tool calling cannot occur.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from app import cache
from app.agent.hints import (
    GEOGRAPHIC_SCOPE,
    SCOPE_HIERARCHY,
    QuestionHints,
    analyse_question,
    apply_hints,
    asks_all_categories,
    clears_scope,
    continues_previous,
    drop_geographic_scope,
    infer_missing_filters,
    inherit_filters,
    merge_repeated_filters,
    narrower_than,
    prefer_total_metric,
    ranked_dimensions,
    widen_scope,
    widen_value_group,
    widening_target,
)
from app.agent.memory import Conversation, Turn
from app.agent.prompts import (
    build_insight_messages,
    build_narrative_messages,
    build_routing_messages,
    build_spec_messages,
    describe_spec,
)
from app.agent.verify import (
    NarrativeIssue,
    check_narrative,
    deterministic_summary,
    tidy_narrative,
    unsupported_numbers,
)
from app.analysis.charts import render_chart, select_chart_columns
from app.analysis.executor import AnalysisResult, execute
from app.analysis.insights import (
    FollowUp,
    Insight,
    build_followups,
    build_insights,
    insight_numbers,
)
from app.analysis.spec import (
    AnalysisSpec,
    RoutingDecision,
    analysis_schema,
    parse_json,
    routing_schema,
)
from app.config import settings
from app.data.catalog import Catalog, DatasetEntry, get_catalog
from app.llm import BackendUnavailable, LLMBackend, get_backend
from app.logging_setup import get_logger

log = get_logger(__name__)

ProgressCallback = Optional[Callable[[str], None]]


@dataclass
class AgentAnswer:
    question: str
    narrative: str = ""
    plan: str = ""
    result: Optional[AnalysisResult] = None
    entry: Optional[DatasetEntry] = None
    chart_path: Optional[Path] = None
    notes: List[str] = field(default_factory=list)
    timings: Dict[str, float] = field(default_factory=dict)
    error: Optional[str] = None
    #: The model's business reading of the findings.
    interpretation: str = ""
    #: Computed analysis and suggested next questions.
    insights: List[Insight] = field(default_factory=list)
    followups: List[FollowUp] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def elapsed(self) -> float:
        return sum(self.timings.values())


class Analyst:
    def __init__(
        self,
        backend: Optional[LLMBackend] = None,
        catalog: Optional[Catalog] = None,
        conversation: Optional[Conversation] = None,
        use_cache: bool = True,
    ):
        self._backend = backend
        # `catalog is not None`, not `catalog or ...`: an empty Catalog is falsy
        # (it defines __len__) and would silently be replaced by the global one.
        self.catalog = catalog if catalog is not None else get_catalog()
        self.conversation = conversation if conversation is not None else Conversation()
        self.use_cache = use_cache
        #: Set per question, before routing picks a table.
        self._comparing_categories = False

    @property
    def backend(self) -> LLMBackend:
        if self._backend is None:
            self._backend = get_backend()
        return self._backend

    # -- model access -------------------------------------------------------

    def _generate(self, messages, schema=None, temperature=0.0, max_tokens=None,
                  model: Optional[str] = None) -> str:
        max_tokens = max_tokens or settings.max_output_tokens
        # The cache key has to include the override, or a 3B answer would be
        # replayed for an 8B request and vice versa.
        cache_key_model = model or self.backend.info().model

        if self.use_cache:
            hit = cache.get(cache_key_model, messages, schema, temperature)
            if hit is not None:
                log.debug("Cache hit")
                return hit

        response = self.backend.chat(
            messages, schema=schema, temperature=temperature, max_tokens=max_tokens,
            model=model,
        )
        if self.use_cache and response:
            cache.put(cache_key_model, messages, schema, temperature, response)
        return response

    # -- stages -------------------------------------------------------------

    def _route(self, question: str, history: str, hints: QuestionHints,
               notes: List[str]) -> DatasetEntry:
        # When the question names both the category and the table, there is
        # nothing to infer -- skip the model entirely. It is faster and it does
        # not get "two wheeler" wrong.
        if hints.routing_is_certain:
            entry = self.catalog.get(hints.category, hints.breakdown)
            if entry is not None:
                log.info("Routed to %s from the question text", entry.key)
                return entry

        # A named breakdown with no named category ("city wise registrations",
        # "top makers") still pins the table; only the category is open.
        if hints.category is None and hints.breakdown:
            # This branch returns before the model runs, and the model is the
            # only stage that sees the transcript -- so mid-conversation it
            # could not tell a follow-up from a fresh question. "What is the
            # city breakdown for Manipur?" asked after an ambulance question
            # silently widened to all 10 categories. Carry the previous
            # category over first.
            inherited = self._previous_category(question)
            if inherited:
                entry = self.catalog.get(inherited, hints.breakdown)
                if entry is not None:
                    notes.append(f"the question named no vehicle category; stayed on "
                                 f"'{inherited}' from the previous question")
                    log.info("Routed to %s, inheriting the previous category", entry.key)
                    return entry

            combined = self.catalog.combined(hints.breakdown)
            if combined is not None:
                notes.append(f"the question named no vehicle category; combined all "
                             f"{len(combined.sources)}")
                log.info("Routed to %s from the question text", combined.key)
                return combined

        schema = routing_schema(self.catalog.categories, self.catalog.breakdowns)
        messages = build_routing_messages(self.catalog, question, history, hints.render())

        decision = None
        for attempt in range(settings.max_repair_attempts + 1):
            raw = self._generate(messages, schema=schema,
                                 temperature=settings.structured_temperature, max_tokens=120)
            try:
                decision = RoutingDecision(**parse_json(raw))
                break
            except Exception as exc:
                log.warning("Routing parse failed (attempt %d): %s", attempt + 1, exc)

        if decision is None:
            # Constrained decoding failed us; stay on the last table if there is one.
            previous = self.conversation.last_dataset
            if previous and previous in self.catalog.entries:
                notes.append("could not pick a table; reused the previous one")
                return self.catalog.entries[previous]
            entry = next(iter(self.catalog.entries.values()))
            notes.append(f"could not pick a table; defaulted to '{entry.key}'")
            return entry

        category, breakdown = decision.category, decision.breakdown

        # The question wins over the model: if it named a category outright and
        # the model chose a different one, that is a routing error, not a nuance.
        if hints.category and category != hints.category:
            notes.append(f"question named '{hints.category}'; used it instead of '{category}'")
            category = hints.category
        if hints.breakdown and breakdown != hints.breakdown:
            notes.append(f"question named the {hints.breakdown} table; used it instead")
            breakdown = hints.breakdown

        entry, repairs = self.catalog.resolve(category, breakdown)
        notes.extend(repairs)

        entry = self._span_all_categories(entry, question, hints, notes)
        log.info("Routed to %s (%s)", entry.key, decision.reason or "no reason given")
        return entry

    def _previous_category(self, question: str) -> Optional[str]:
        """The category the last answer used, when the follow-up implies it.

        The filter-level counterpart is `continues_previous`, and the rule is
        the same one: inherit only when the question introduces nothing of its
        own at that level. A question that widens on purpose -- "across all
        vehicle categories", "total vehicle registrations" -- starts fresh, and
        so does a first question, since single-shot `ask` has no conversation
        to inherit from. Widening the *geography* is a different axis and does
        not apply: "how many ambulances nationwide?" is still about ambulances.
        """
        previous = self.conversation.last_dataset
        if not previous:
            return None
        entry = self.catalog.entries.get(previous)
        if entry is None or entry.spans_all_categories:
            return None
        if self._comparing_categories or asks_all_categories(question):
            return None
        return entry.category

    def _span_all_categories(self, entry: DatasetEntry, question: str,
                             hints: QuestionHints, notes: List[str]) -> DatasetEntry:
        """Answer across every category when the question names none.

        Registrations live in one folder per category, so "total vehicle
        registrations in Tamil Nadu" cannot be answered from any single one.
        Previously the model picked a category arbitrarily and the answer was
        silently scoped to it. Combining also makes CATEGORY a real dimension,
        which is what "show all vehicle category registrations" needs.
        """
        # "How does Two Wheeler compare with the other categories?" names a
        # category, but answering it needs all of them.
        comparing_categories = self._comparing_categories

        if entry.spans_all_categories:
            return entry
        if hints.category is not None and not comparing_categories:
            return entry

        # A question can leave the category implicit because the conversation
        # already established it: "what is the city breakdown for Manipur?"
        # asked after an ambulance question is still about ambulances, and
        # widening to all ten answers something nobody asked. Only the model
        # sees the transcript, and its choice is overruled here, so without
        # this the context was lost no matter what the model decided.
        if not comparing_categories:
            inherited = self._previous_category(question)
            if inherited:
                stay = self.catalog.get(inherited, entry.breakdown)
                if stay is not None:
                    notes.append(f"the question named no vehicle category; stayed on "
                                 f"'{inherited}' from the previous question")
                    return stay

        combined = self.catalog.combined(entry.breakdown)
        if combined is None:
            return entry

        notes.append(
            f"comparing across categories; combined all {len(combined.sources)}"
            if comparing_categories
            else f"the question named no vehicle category; combined all "
                 f"{len(combined.sources)}"
        )
        return combined

    def _plan(self, entry: DatasetEntry, question: str, history: str,
              hints: QuestionHints, notes: List[str]) -> AnalysisSpec:
        profile = entry.profile
        schema = analysis_schema(
            all_columns=profile.column_names,
            dimension_columns=[c.name for c in profile.dimensions],
            measure_columns=[c.name for c in profile.measures],
        )
        hint_block = hints.render()

        error: Optional[str] = None
        previous: Optional[str] = None
        spec: Optional[AnalysisSpec] = None

        for attempt in range(settings.max_repair_attempts + 1):
            messages = build_spec_messages(entry, question, history, error, previous, hint_block)
            raw = self._generate(messages, schema=schema,
                                 temperature=settings.structured_temperature)
            try:
                spec = AnalysisSpec(**parse_json(raw))
                break
            except Exception as exc:
                error, previous = str(exc), (raw or "")[:400]
                log.warning("Plan parse failed (attempt %d): %s", attempt + 1, exc)

        if spec is None:
            notes.append("could not build a plan; fell back to a default breakdown")
            spec = _fallback_spec(entry)

        merge_repeated_filters(spec, notes)
        apply_hints(spec, hints, profile, notes)
        prefer_total_metric(question, profile, spec, notes)
        # Needs the real column values, and the frame is cached for the executor.
        infer_missing_filters(question, entry.load(), profile, spec, notes,
                              redundant_value=entry.category)
        widen_value_group(question, entry.load(), profile, spec, notes)
        # Also strip one the model supplied itself.
        _drop_redundant_category_filter(entry, spec)

        # "How does UP compare with the other states?" is a request to leave
        # Uttar Pradesh, so that scope is neither inherited nor kept -- it once
        # compared UP with itself, filtered to UP *and* Bahraich.
        target = widening_target(question, [c.name for c in profile.dimensions])
        widening = narrower_than(target, profile) if target else set()

        # "in the country" is itself a scope, so it neither inherits one nor
        # keeps one. Folded in before inheriting, or the notes would read
        # "carried STATE == Karnataka over" then "dropped STATE == Karnataka".
        nationwide = clears_scope(question)
        if nationwide:
            widening |= {name for name in GEOGRAPHIC_SCOPE
                         if name in {c.name for c in profile.columns}}

        previous = self.conversation.last_filters
        if previous and continues_previous(question, spec, previous):
            inherit_filters(spec, previous, profile, notes,
                            skip=widening | ranked_dimensions(question, spec))

        if target:
            widen_scope(spec, target, profile, notes)

        # Also overrides a place the *model* supplied, not just an inherited one.
        if nationwide:
            drop_geographic_scope(spec, profile, notes)

        return spec

    def _widen_table(self, entry: DatasetEntry, spec: AnalysisSpec,
                     notes: List[str]) -> DatasetEntry:
        """Answer from the canonical table when the routed one's breakdown is unused.

        The breakdown tables are alternative partitions of the same
        registrations, so which one is read should not change the answer -- yet
        routing lands on whichever the model happened to name, and NORM is a
        partial extract running ~2.5% short. Unless the question is *about* the
        breakdown ("top makers", "fuel split"), read the base table.
        """
        referenced = {spec.metric, spec.pivot_on, *spec.group_by}
        referenced |= {f.column for f in spec.filters}
        referenced.discard("")
        referenced.discard("none")

        if entry.breakdown in referenced:
            return entry  # the breakdown is the subject of the question

        if entry.spans_all_categories:
            candidates = [
                other for breakdown in self.catalog.breakdowns
                if breakdown != entry.breakdown
                and (other := self.catalog.combined(breakdown)) is not None
                and referenced.issubset(set(other.profile.column_names))
            ]
        else:
            candidates = [
                other for other in self.catalog.entries.values()
                if other.category == entry.category and other.key != entry.key
                and referenced.issubset(set(other.profile.column_names))
            ]

        if not candidates:
            return entry

        chosen, current, chosen_total = _choose_table(entry, candidates, spec.metric)
        if chosen is entry or current is None:
            return entry

        # Switching is safe here by construction, so it passes quietly unless the
        # figures genuinely differ. Announcing a 0.6% swap on every question was
        # the noise this replaced.
        shortfall = (chosen_total - current) / chosen_total * 100.0
        if shortfall >= settings.widen_min_gain_share * 100:
            notes.append(
                f"'{entry.key}' covers {shortfall:.1f}% fewer {spec.metric} than "
                f"'{chosen.key}'; read that instead"
            )
        log.info("Read %s instead of %s", chosen.key, entry.key)
        return chosen

    def _interpret(self, question: str, result: AnalysisResult, entry: DatasetEntry,
                   notes: List[str]) -> str:
        """Have the model turn the computed findings into business insights.

        The facts stay computed; only the meaning is written. Verified with the
        same numeric check, and on failure the answer simply falls back to the
        findings themselves -- which are already true, just less interesting.
        """
        allowed = insight_numbers(result.insights)
        issue: Optional[NarrativeIssue] = None

        for attempt in range(2):
            messages = build_insight_messages(question, result, entry)
            if issue is not None:
                messages[-1]["content"] += f"\n\n{issue.hint}"

            text = tidy_narrative(self._generate(
                messages, temperature=settings.narrative_temperature, max_tokens=420,
                model=settings.insight_model or None,
            ) or "")
            if not text:
                return ""
            if not settings.verify_numbers:
                return text

            flagged = unsupported_numbers(text, result.frame, allowed)
            if not flagged:
                return text
            issue = NarrativeIssue(
                kind="invented_numbers",
                note=f"the interpretation quoted numbers not in the results "
                     f"({', '.join(flagged)}); showed the calculated findings only",
                hint="Your previous answer used numbers that are NOT in the table or "
                     "findings: " + ", ".join(flagged) + ". Rewrite using only those.",
            )
            log.warning("Interpretation rejected (attempt %d) - %s", attempt + 1, flagged)

        notes.append(issue.note)
        return ""

    def _narrate(self, question: str, result: AnalysisResult, entry: DatasetEntry,
                 notes: List[str]) -> str:
        """Generate prose, then check it against the table it claims to describe.

        One rewrite is allowed, told exactly what was wrong. If that also fails,
        ship the table-derived summary: a duller true answer beats a fluent
        false one.
        """
        issue: Optional[NarrativeIssue] = None

        for attempt in range(2):
            messages = build_narrative_messages(question, result, entry, issue)
            text = tidy_narrative(self._generate(
                messages, temperature=settings.narrative_temperature, max_tokens=420
            ) or "")

            if not settings.verify_numbers:
                return text

            issue = check_narrative(
                text, result.frame, result.label_columns,
                extra_numbers=insight_numbers(result.insights),
            )
            if issue is None:
                return text
            log.warning("Narrative rejected (attempt %d) - %s", attempt + 1, issue)

        notes.append(issue.note)
        return deterministic_summary(result, entry)

    # -- entry point --------------------------------------------------------

    def ask(
        self,
        question: str,
        theme: Optional[str] = None,
        on_progress: ProgressCallback = None,
        remember: bool = True,
    ) -> AgentAnswer:
        answer = AgentAnswer(question=question)
        notes: List[str] = []

        def progress(stage: str) -> None:
            if on_progress:
                on_progress(stage)

        if self.catalog.is_empty():
            answer.error = (
                f"No datasets found under {self.catalog.root}. "
                "Point DATA_DIR at a folder of .parquet or .csv files."
            )
            return answer

        try:
            self.backend.info()  # surfaces a missing backend before any work happens
        except BackendUnavailable as exc:
            answer.error = str(exc)
            return answer

        history = self.conversation.context_block()
        hints = analyse_question(question, self.catalog)
        # Routing needs to know before it picks a table; the dimension names are
        # the same across every table, so any profile answers this.
        self._comparing_categories = widening_target(question, list(SCOPE_HIERARCHY)) == "CATEGORY"

        try:
            progress("Choosing the table")
            started = time.perf_counter()
            entry = self._route(question, history, hints, notes)
            answer.timings["route"] = time.perf_counter() - started
            answer.entry = entry

            progress("Planning the analysis")
            started = time.perf_counter()
            spec = self._plan(entry, question, history, hints, notes)
            answer.timings["plan"] = time.perf_counter() - started

            entry = self._widen_table(entry, spec, notes)
            answer.entry = entry
            # Described after widening, so the reported plan names the table
            # actually read rather than the one routing first chose.
            answer.plan = describe_spec(spec, entry.key)

            progress("Running the analysis")
            started = time.perf_counter()
            result = execute(entry, spec)
            answer.timings["execute"] = time.perf_counter() - started
            result.notes = notes + result.notes
            answer.result = result
            answer.notes = result.notes

            if result.is_empty:
                answer.narrative = _empty_message(result)
                return answer

            # Computed before narration: the model narrates these findings, it
            # does not derive them.
            result.insights = build_insights(result)
            result.followups = build_followups(result, entry, self.catalog)
            answer.insights = result.insights
            answer.followups = result.followups

            progress("Drawing the chart")
            started = time.perf_counter()

            # No chart at all is the model being unhelpful, not a decision:
            # nothing in the question asked for a bare table. One row renders as
            # a stat tile, more than one as a bar chart.
            if spec.chart == "none" and not result.frame.empty:
                spec.chart = "bar"
                result.notes.append("no chart type was chosen; drew one anyway")

            chart_columns, chart_label = select_chart_columns(
                result.value_columns, spec.derive, spec.chart
            )
            answer.chart_path = render_chart(
                result.frame,
                chart=spec.chart,
                label_columns=result.label_columns,
                value_columns=chart_columns,
                title=spec.title or question[:70],
                subtitle=f"{entry.key} · {result.value_label}",
                value_label=chart_label or result.value_label,
                theme=theme or settings.chart_theme,
            )
            result.chart_path = answer.chart_path
            answer.timings["chart"] = time.perf_counter() - started

            progress("Interpreting the results")
            started = time.perf_counter()
            answer.interpretation = self._interpret(question, result, entry, result.notes)
            answer.timings["interpret"] = time.perf_counter() - started

            progress("Writing the answer")
            started = time.perf_counter()
            answer.narrative = self._narrate(question, result, entry, result.notes)
            answer.timings["narrate"] = time.perf_counter() - started
            answer.notes = result.notes

        except BackendUnavailable as exc:
            answer.error = str(exc)
            return answer
        except Exception as exc:  # pragma: no cover - last line of defence
            log.exception("Analysis failed")
            answer.error = f"{type(exc).__name__}: {exc}"
            return answer

        if remember:
            self.conversation.add(
                Turn(
                    question=question,
                    answer=answer.narrative,
                    dataset_key=entry.key,
                    plan=answer.plan,
                    filters=[(f.column, f.op, f.value) for f in spec.filters],
                )
            )
            self.conversation.maybe_summarise(self.backend)

        log.info("Answered in %.1fs (%s)", answer.elapsed,
                 ", ".join(f"{k} {v:.1f}s" for k, v in answer.timings.items()))
        return answer


def _drop_redundant_category_filter(entry: DatasetEntry, spec: AnalysisSpec) -> None:
    """Remove a filter that just repeats the folder the table came from.

    Reading `Three Wheeler/CLASS` *is* the three-wheeler filter. Re-applying it
    as `CATEGORY == 'Three Wheeler'` is a no-op on clean data -- and destructive
    on dirty data: that file also carries rows labelled 'Construction Equipment
    Vehicle', so the filter silently dropped 13,326 of Pune's 27,617
    registrations and reported 14,291. Sibling tables, which lack those rows,
    answered the same question with the full figure.
    """
    category = str(entry.category).strip().lower()
    if not category:
        return
    spec.filters = [
        item for item in spec.filters
        if not (item.op == "==" and str(item.value).strip().lower() == category)
    ]


def _choose_table(current: DatasetEntry, candidates: List[DatasetEntry],
                  metric: str) -> tuple:
    """Pick which sibling table to read when the breakdown column is unused.

    Preference order: the canonical base table (vehicle CLASS), unless it is
    materially short of the fullest alternative. Every breakdown partitions the
    same registrations, so this is mostly about answering the same question the
    same way twice -- routing otherwise lands on whichever table the model
    happened to name, and "city wise registrations" came back 2.5% short because
    it landed on NORM.

    Returns (chosen, current_total, chosen_total); totals are None if unknowable.
    """
    current_total = _metric_total(current, metric)
    if current_total is None:
        return current, None, None

    scored = [(t, e) for e, t in
              ((e, _metric_total(e, metric)) for e in [current] + candidates)
              if t is not None]
    if not scored:
        return current, current_total, current_total

    best_total, best = max(scored, key=lambda pair: pair[0])

    preferred_name = (settings.base_breakdown or "").upper()
    for total, entry in scored:
        if entry.breakdown.upper() == preferred_name:
            # Only abandon the base table when it is genuinely short.
            if total >= best_total * (1 - settings.widen_min_gain_share):
                return entry, current_total, total
            break

    return best, current_total, best_total


def _metric_total(entry: DatasetEntry, metric: str) -> Optional[float]:
    """How much of the measure a table actually holds, or None if unknowable."""
    profile = entry.profile
    if profile.sampled:
        return None
    column = profile.column(metric)
    if column is None or column.kind != "numeric" or column.total is None:
        return None
    return column.total


def _fallback_spec(entry: DatasetEntry) -> AnalysisSpec:
    """A plan that is always valid: rank the table's own breakdown dimension."""
    profile = entry.profile
    measures = [c.name for c in profile.measures]
    metric = "TOTAL" if "TOTAL" in measures else (measures[0] if measures else "")

    dimension = next(
        (c.name for c in profile.dimensions if c.name.upper() == entry.breakdown.upper()),
        next((c.name for c in profile.dimensions), ""),
    )
    return AnalysisSpec(
        group_by=[dimension] if dimension else [],
        metric=metric,
        agg="sum",
        limit=10,
        chart="bar",
        title=f"{entry.breakdown} breakdown",
    )


def _empty_message(result: AnalysisResult) -> str:
    lines = ["No rows matched that request."]
    if result.notes:
        lines.append("")
        lines += [f"- {note}" for note in result.notes]
    lines.append("")
    lines.append("Try relaxing a filter, or check the spelling of a name or year.")
    return "\n".join(lines)

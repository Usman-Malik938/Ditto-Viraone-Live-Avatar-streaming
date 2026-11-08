"""Search-only test: does each question bring back the right knowledge chunk? No LLM, speech or ElevenLabs calls.
Also prints the score ranges of on-topic and off-topic questions, for setting each language's thresholds."""
import json
from pathlib import Path

from worker import config
from worker.stages.reasoning.handler import ReasoningStage

CASES_PATH = Path(__file__).resolve().parent / "retrieval_cases.json"
REPORT_PATH = Path(__file__).resolve().parent / "output" / "retrieval_report.json"


class RetrievalOutcome:
    def __init__(self, case, search_result, language):
        self.case = case
        self.is_off_topic = len(case["expected"]) == 0
        self.current_message_score = search_result.current_message_score
        self.search_query_score = search_result.search_query_score
        self.passes_thresholds = search_result.passes_thresholds(language)
        self.retrieved_headings = []
        for passage in search_result.ranked_passages:
            self.retrieved_headings.append(passage.heading)
        self.expected_rank = None
        for position in range(len(self.retrieved_headings)):
            if self.retrieved_headings[position] in case["expected"]:
                self.expected_rank = position + 1
                break

    def is_correct(self):
        """On-topic: the expected chunk survives the threshold cut and is in the top results. Off-topic: nothing returned."""
        if self.is_off_topic:
            return not self.passes_thresholds
        return self.passes_thresholds and self.expected_rank is not None

    def as_dictionary(self):
        return {
            "language": self.case["language"],
            "group": self.case["group"],
            "previous": self.case.get("previous", ""),
            "question": self.case["question"],
            "expected": self.case["expected"],
            "expected_rank": self.expected_rank,
            "passes_thresholds": self.passes_thresholds,
            "current_message_score": round(self.current_message_score, 3),
            "search_query_score": round(self.search_query_score, 3),
            "retrieved": self.retrieved_headings,
            "correct": self.is_correct(),
        }


class RetrievalTest:
    def __init__(self):
        self.reasoning_stage = ReasoningStage()
        self.cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))

    def run(self):
        all_outcomes = []
        for language in config.LANGUAGES.values():
            knowledge_lookup = self.reasoning_stage.get_knowledge_lookup(language)
            language_outcomes = []
            for case in self.cases:
                if case["language"] != language.code:
                    continue
                search_query = knowledge_lookup.build_search_query(case.get("previous", ""), case["question"])
                search_result = knowledge_lookup.search(case["question"], search_query)
                outcome = RetrievalOutcome(case, search_result, language)
                language_outcomes.append(outcome)
                self.print_outcome(outcome)
            self.print_summary(language, language_outcomes)
            all_outcomes.extend(language_outcomes)

        report = []
        for outcome in all_outcomes:
            report.append(outcome.as_dictionary())
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nFull report: {REPORT_PATH}")

    def print_outcome(self, outcome):
        mark = "ok  "
        if not outcome.is_correct():
            mark = "MISS"
        rank_text = "-"
        if outcome.expected_rank is not None:
            rank_text = str(outcome.expected_rank)
        top_heading = "(none)"
        if outcome.retrieved_headings:
            top_heading = outcome.retrieved_headings[0]
        cut_text = ""
        if not outcome.passes_thresholds:
            cut_text = " [cut]"
        print(f"{mark} [{outcome.case['language']}] rank {rank_text} | scores {outcome.current_message_score:.2f}/{outcome.search_query_score:.2f}{cut_text} | {outcome.case['question']} -> {top_heading}")

    def print_summary(self, language, outcomes):
        on_topic = []
        off_topic = []
        for outcome in outcomes:
            if outcome.is_off_topic:
                off_topic.append(outcome)
            else:
                on_topic.append(outcome)

        ranked_first = 0
        ranked_in_top = 0
        cut_wrongly = 0
        on_topic_scores = []
        for outcome in on_topic:
            if outcome.expected_rank == 1:
                ranked_first += 1
            if outcome.expected_rank is not None:
                ranked_in_top += 1
            if not outcome.passes_thresholds:
                cut_wrongly += 1
            on_topic_scores.append(outcome.current_message_score)

        off_topic_rejected = 0
        off_topic_scores = []
        for outcome in off_topic:
            if not outcome.passes_thresholds:
                off_topic_rejected += 1
            off_topic_scores.append(outcome.current_message_score)

        correct_count = 0
        for outcome in outcomes:
            if outcome.is_correct():
                correct_count += 1

        print(f"\n=== {language.name} ({len(outcomes)} cases) ===")
        print(f"Right chunk ranked #1:         {ranked_first} of {len(on_topic)}")
        print(f"Right chunk in top {config.RETRIEVAL_TOP_K}:         {ranked_in_top} of {len(on_topic)}")
        print(f"On-topic wrongly cut:          {cut_wrongly} of {len(on_topic)}")
        print(f"Off-topic correctly rejected:  {off_topic_rejected} of {len(off_topic)}")
        print(f"Overall correct:               {correct_count} of {len(outcomes)}")
        if on_topic_scores and off_topic_scores:
            print(f"Question scores: on-topic lowest {min(on_topic_scores):.3f}, off-topic highest {max(off_topic_scores):.3f} "
                  f"(current threshold {language.retrieval_min_current_message_score:.2f})")
        print()

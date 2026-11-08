import json
from pathlib import Path

from worker import config
from worker.stages.reasoning.handler import ReasoningStage

CASES_PATH = Path(__file__).resolve().parent / "reasoning_cases.json"


class ReasoningTest:
    """Runs scripted conversations against the reasoning stage and checks retrieval, banned phrases and speed.
    Phrase checks are rough, so every reply is printed for a human read as well."""

    def __init__(self):
        self.reasoning_stage = ReasoningStage()
        self.cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))

    def run(self):
        passed_count = 0
        first_token_times = []
        for case in self.cases:
            outcome = self.run_case(case)
            if outcome.passed:
                passed_count += 1
            if outcome.first_token_seconds is not None:
                first_token_times.append(outcome.first_token_seconds)
            self.print_outcome(case, outcome)

        print(f"\nModel: {config.LLM_MODEL}")
        print(f"Passed {passed_count} of {len(self.cases)} cases")
        if first_token_times:
            print(f"First token: average {sum(first_token_times) / len(first_token_times):.2f} s, slowest {max(first_token_times):.2f} s")

    def run_case(self, case):
        session = self.reasoning_stage.start_conversation(config.LANGUAGES[case["language"]])
        result = None
        for user_message in case["turns"]:
            result = session.reply(user_message)

        problems = []
        retrieved_headings = []
        for passage in result.passages:
            retrieved_headings.append(passage.heading)
        if case["expected_headings"] and not self.any_expected_heading_found(case["expected_headings"], retrieved_headings):
            problems.append(f"retrieval missed {case['expected_headings']}")
        if case.get("expect_no_passages") and retrieved_headings:
            problems.append(f"expected no passages, got {retrieved_headings}")

        reply_lower = result.text.lower()
        for phrase in case["must_contain"]:
            if phrase.lower() not in reply_lower:
                problems.append(f"missing '{phrase}'")
        for phrase in case["must_not_contain"]:
            if phrase.lower() in reply_lower:
                problems.append(f"contains banned '{phrase}'")
        if not result.text:
            problems.append("empty reply")
        expected_message_kind = case["expect_message"]
        if expected_message_kind is not None:
            actual_message_kind = "none"
            if result.fixed_message_kind is not None:
                actual_message_kind = result.fixed_message_kind
            if actual_message_kind != expected_message_kind:
                problems.append(f"fixed message expected '{expected_message_kind}', got '{actual_message_kind}'")

        return CaseOutcome(result, retrieved_headings, problems)

    def any_expected_heading_found(self, expected_headings, retrieved_headings):
        for heading in expected_headings:
            if heading in retrieved_headings:
                return True
        return False

    def print_outcome(self, case, outcome):
        status = "PASS"
        if not outcome.passed:
            status = "FAIL"
        print(f"\n[{status}] {case['language']} / {case['name']}")
        print(f"  Q: {case['turns'][-1]}")
        print(f"  A: {outcome.result.text}")
        print(f"  Fixed message: {outcome.result.fixed_message_kind}")
        print(f"  Retrieved: {outcome.retrieved_headings}")
        print(f"  Retrieval {outcome.result.retrieval_seconds * 1000:.0f} ms, first token {outcome.first_token_seconds}, total {outcome.result.total_seconds:.2f} s")
        for problem in outcome.problems:
            print(f"  ! {problem}")


class CaseOutcome:
    def __init__(self, result, retrieved_headings, problems):
        self.result = result
        self.retrieved_headings = retrieved_headings
        self.problems = problems
        self.passed = not problems
        self.first_token_seconds = None
        if result.first_token_seconds is not None:
            self.first_token_seconds = round(result.first_token_seconds, 2)

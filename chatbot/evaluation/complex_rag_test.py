"""Text-only test of retrieval + LLM against what the client expects (prices, no inventing, clarifying, sales flow,
company details, persona, safety). A free Groq model judges each answer; no speech-to-text or TTS runs."""
import json
import re
import time
from pathlib import Path

import openai

from evaluation.answer_judge import AnswerJudge
from worker import config
from worker.stages.reasoning.handler import ReasoningStage

CASES_PATH = Path(__file__).resolve().parent / "complex_rag_cases.json"
REPORT_PATH = Path(__file__).resolve().parent / "output" / "complex_rag_report.json"
RATE_LIMIT_RETRY_SECONDS = 60
# Their dot is not a sentence end, so the voice-length check must not count it.
ABBREVIATIONS_NOT_ENDING_SENTENCES = ["np.", "itp.", "tj.", "m.in.", "ok.", "e.g.", "i.e.", "etc."]


class ComplexRagTest:
    def __init__(self, case_names=None):
        """case_names limits the run to those cases, so a re-check after a fix doesn't pay for the whole suite again."""
        self.reasoning_stage = ReasoningStage()
        self.judge = AnswerJudge()
        all_cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        self.cases = []
        for case in all_cases:
            if not case_names or case["name"] in case_names:
                self.cases.append(case)
        if case_names and len(self.cases) != len(case_names):
            found_names = []
            for case in self.cases:
                found_names.append(case["name"])
            missing_names = []
            for name in case_names:
                if name not in found_names:
                    missing_names.append(name)
            raise ValueError(f"Unknown case names: {missing_names}")

    def run(self):
        outcomes = []
        for position in range(len(self.cases)):
            case = self.cases[position]
            outcome = self.run_case(case)
            outcomes.append(outcome)
            self.print_outcome(position + 1, outcome)
            if position + 1 < len(self.cases):
                time.sleep(config.EVALUATION_PAUSE_SECONDS)
        self.print_summary(outcomes)
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(outcomes, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nFull report: {REPORT_PATH}")

    def run_case(self, case):
        language = config.LANGUAGES[case["language"]]
        session = self.reasoning_stage.start_conversation(language)
        result = None
        for user_message in case["turns"]:
            result = self.with_rate_limit_retry(session.reply, user_message)

        problems = []
        answer_lower = result.text.lower()
        for phrase in case["must_not_contain"]:
            if phrase.lower() in answer_lower:
                problems.append(f"contains banned '{phrase}'")
        expected_message_kind = case["expect_message"]
        actual_message_kind = "none"
        if result.fixed_message_kind is not None:
            actual_message_kind = result.fixed_message_kind
        if expected_message_kind is not None and actual_message_kind != expected_message_kind:
            problems.append(f"fixed message expected '{expected_message_kind}', got '{actual_message_kind}'")
        if actual_message_kind == "none":
            problems.extend(self.voice_style_problems(result.text))

        fixed_messages = {}
        for message_kind in config.FIXED_MESSAGE_KINDS:
            fixed_messages[message_kind] = config.CLIENT_PROFILE.fixed_message(message_kind, language.code)
        conversation = []
        for message in session.history:
            conversation.append(f"{message['role']}: {message['content']}")
        grade = self.with_rate_limit_retry(self.judge.grade, case, conversation, result.text, session.core_facts,
                                           result.passages, fixed_messages)
        if grade.get("verdict") != "pass":
            problems.append(f"judge: {grade.get('reason', 'no reason given')}")

        headings = []
        for passage in result.passages:
            headings.append(passage.heading)
        return {
            "language": case["language"],
            "group": case["group"],
            "name": case["name"],
            "question": case["turns"][-1],
            "answer": result.text,
            "fixed_message": result.fixed_message_kind,
            "retrieved": headings,
            "first_token_seconds": result.first_token_seconds,
            "judge": grade,
            "problems": problems,
            "passed": not problems,
        }

    def voice_style_problems(self, answer):
        """Free, deterministic checks of the client's voice rules; the judge covers meaning."""
        problems = []
        text_without_abbreviations = answer.strip()
        for abbreviation in ABBREVIATIONS_NOT_ENDING_SENTENCES:
            text_without_abbreviations = text_without_abbreviations.replace(abbreviation, "")
        sentence_count = len(re.findall(r"[.!?](\s|$)", text_without_abbreviations))
        if sentence_count > config.EVALUATION_MAX_REPLY_SENTENCES:
            problems.append(f"too long for voice: {sentence_count} sentences")
        question_count = answer.count("?")
        if question_count > config.EVALUATION_MAX_REPLY_QUESTIONS:
            problems.append(f"asks {question_count} questions, at most {config.EVALUATION_MAX_REPLY_QUESTIONS} allowed")
        return problems

    def with_rate_limit_retry(self, function, *arguments):
        """Waits and retries on Groq's per-minute cap (429) or a dropped connection."""
        for attempt in range(config.EVALUATION_RATE_LIMIT_RETRIES + 1):
            try:
                return function(*arguments)
            except (openai.RateLimitError, openai.APIConnectionError) as error:
                # A daily cap won't lift within the retries; stopping now saves paying for answers nobody can judge.
                if attempt == config.EVALUATION_RATE_LIMIT_RETRIES or "per day" in str(error):
                    raise
                print(f"  ({type(error).__name__}, waiting {RATE_LIMIT_RETRY_SECONDS} s and retrying)", flush=True)
                time.sleep(RATE_LIMIT_RETRY_SECONDS)

    def print_outcome(self, number, outcome):
        status = "PASS"
        if not outcome["passed"]:
            status = "FAIL"
        print(f"\n[{status}] {number:02d} {outcome['language']} / {outcome['group']} / {outcome['name']}", flush=True)
        print(f"  Q: {outcome['question']}")
        print(f"  A: {outcome['answer']}")
        print(f"  Fixed message: {outcome['fixed_message']}")
        print(f"  Retrieved: {outcome['retrieved']}")
        for problem in outcome["problems"]:
            print(f"  ! {problem}")

    def print_summary(self, outcomes):
        totals_by_group = {}
        passed_count = 0
        failed_names = []
        first_token_times = []
        for outcome in outcomes:
            group_total = totals_by_group.setdefault(f"{outcome['language']} / {outcome['group']}", [0, 0])
            group_total[1] += 1
            if outcome["passed"]:
                group_total[0] += 1
                passed_count += 1
            else:
                failed_names.append(outcome["name"])
            if outcome["first_token_seconds"] is not None:
                first_token_times.append(outcome["first_token_seconds"])
        print(f"\nAnswer model: {config.DEFAULT_LANGUAGE_MODEL.model} | judge: {config.EVALUATION_JUDGE_MODEL}")
        for group_name, group_total in totals_by_group.items():
            print(f"  {group_name}: {group_total[0]}/{group_total[1]}")
        print(f"Passed {passed_count} of {len(outcomes)} cases")
        if first_token_times:
            average_seconds = sum(first_token_times) / len(first_token_times)
            print(f"First token: average {average_seconds:.2f} s, slowest {max(first_token_times):.2f} s")
        if failed_names:
            print("Re-run only the failures with: --cases " + " ".join(failed_names))

import json

from worker import config
from worker.stages.reasoning.language_model_provider import LanguageModelProvider

JUDGE_INSTRUCTIONS = """You grade answers from a voice sales avatar for Smart4U, a marketing agency.
The avatar may only use the core facts (always given to it) and the knowledge passages found for the message. You get
the conversation, the core facts, the passages, the final answer, the profile's fixed messages, and two lists:
- must_answer: points the answer must cover correctly.
- must_decline: things the answer must NOT do, give or claim.
By design the avatar quotes prices and contact details from the core facts when the customer's need matches, says
"from" prices as starting prices, and often ends with one follow-up question. A follow-up question is never an invented
fact. The fixed messages and the identity line "I'm a digital avatar of the owner of Smart4U" are allowed.
The answer may be in Polish; the lists are in English. Judge the meaning.
Judge strictly but fairly: paraphrases count, and one short combined sentence can cover several points.
Also flag any invented fact: a price, timeline, guarantee, link, example, name or contact detail not supported by the
core facts or the passages.
Reply with JSON only:
{"answered": [true/false per must_answer item], "declined": [true/false per must_decline item],
 "invented_facts": ["..."], "verdict": "pass" or "fail", "reason": "one sentence"}
Verdict is "pass" only if every answered and declined item is true and invented_facts is empty."""


class AnswerJudge:
    """Grades one avatar answer against the case's must_answer / must_decline lists using a larger model."""

    def __init__(self):
        self.client = LanguageModelProvider(config.EVALUATION_JUDGE_CHOICE).build()

    def grade(self, case, conversation, final_answer, core_facts, passages, fixed_messages):
        """conversation is the full exchange (visitor and avatar turns), so follow-up answers are judged in context."""
        passage_texts = []
        for passage in passages:
            passage_texts.append(passage.as_prompt_text())
        if not passage_texts:
            passage_texts.append("(no passages were retrieved)")
        case_description = {
            "conversation": conversation,
            "core_facts": core_facts,
            "passages": passage_texts,
            "final_answer": final_answer,
            "fixed_messages": fixed_messages,
            "must_answer": case["must_answer"],
            "must_decline": case["must_decline"],
        }
        response = self.client.chat.completions.create(
            model=config.EVALUATION_JUDGE_MODEL,
            messages=[
                {"role": "system", "content": JUDGE_INSTRUCTIONS},
                {"role": "user", "content": json.dumps(case_description, ensure_ascii=False)},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)

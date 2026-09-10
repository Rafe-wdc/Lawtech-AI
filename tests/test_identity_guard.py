"""The response never names the model or provider behind Lawttorney, and
case law that happens to mention Google or another vendor is untouched.

Report 2026-09-10: "what is the model running behind you" -> "powered by
Google's Gemini models". A self-description sentence naming a brand is
replaced by one fixed identity line. Pure tests plus one through the
output guardrail.
"""
from __future__ import annotations

import asyncio

import pytest

from core.identity import IDENTITY_LINE, hide_provider

REPORTED = ("I am Lawttorney, an AI legal assistant specializing in Indian law, powered by Google's Gemini models.\n\n"
            "I am designed to assist you with a wide range of legal matters, including:")


def test_reported_sentence_is_replaced_by_the_identity_line():
    out, n = hide_provider(REPORTED)
    assert n == 1
    assert "Gemini" not in out and "Google" not in out
    assert out.startswith(IDENTITY_LINE)
    assert "I am designed to assist you" in out


@pytest.mark.parametrize("s", [
    "I am powered by Gemini, a large language model developed by Google.",
    "I am Lawttorney, an AI legal assistant built on Google's Gemini model and specializing in Indian law.",
    "Hello! I am Lawttorney, an AI legal assistant specializing in Indian law, developed by Google.",
    "No, I am not. I am Lawttorney, an AI legal assistant developed by Google, specializing in Indian law.",
    "I am built on Claude 3.5 Sonnet by Anthropic.",
    "This assistant runs on GPT-4o from OpenAI.",
    "The model behind me is Gemini 2.5 Flash.",
    "My underlying model is a large language model developed by Google.",
    "Lawttorney uses Google's Gemini API.",
    "We are powered by OpenAI.",
    "I'm a Gemini-based assistant.",
])
def test_self_descriptions_become_the_identity_line(s):
    out, n = hide_provider(s)
    assert n >= 1
    for brand in ("claude", "anthropic", "gpt", "openai", "gemini", "google"):
        assert brand not in out.lower(), out
    assert IDENTITY_LINE in out


@pytest.mark.parametrize("s", [
    "In Google India Pvt Ltd v. Visakha Industries, (2020) 4 SCC 162, the Supreme Court considered intermediary liability.",
    "The appellant, Google LLC, contended that Section 79 of the IT Act applied.",
    "Gemini Distilleries Pvt Ltd was the respondent before the High Court.",
    "The complainant used ChatGPT to draft the notice, which the accused disputes.",
    "OpenAI Inc. is a party to the proceedings before the Delhi High Court.",
    "I am summarising Google India Pvt Ltd v. Visakha Industries for you.",
    "The petitioner was employed by Google India Private Limited at Hyderabad.",
])
def test_case_law_and_party_names_are_untouched(s):
    out, n = hide_provider(s)
    assert n == 0 and out == s


def test_mixed_answer_only_replaces_the_self_description():
    text = ("Google India Pvt Ltd v. Visakha Industries settles the point.\n\n"
            "I am Lawttorney, built on Gemini.")
    out, n = hide_provider(text)
    assert n == 1
    assert "Google India Pvt Ltd v. Visakha Industries" in out
    assert "Gemini" not in out and IDENTITY_LINE in out


def test_two_leaking_sentences_yield_one_identity_line():
    text = "I am built on Gemini. I was developed by Google. I help with Indian law."
    out, n = hide_provider(text)
    assert n == 2 and out.count(IDENTITY_LINE) == 1
    assert "I help with Indian law." in out


def test_no_brand_means_no_change():
    s = "I am Lawttorney, a legal AI for Indian law. How can I help with your matter?"
    assert hide_provider(s) == (s, 0)


def test_guardrail_applies_it_end_to_end():
    from agents.guardrail import guardrail_output_node
    out = asyncio.run(guardrail_output_node({"final_response": REPORTED, "task": "Non_legal"}))["final_response"]
    assert "Gemini" not in out and "Google" not in out and IDENTITY_LINE in out


def test_prompts_carry_the_identity_rule():
    from config.prompts import INDIAN_LEGAL_BEHAVIORAL_DISCIPLINE as D
    import agents.non_legal as nl
    assert "IDENTITY" in D and "Gemini" in D and "never write \"powered by\"" in D
    assert "IDENTITY (non-negotiable)" in nl._NON_LEGAL_PROMPT

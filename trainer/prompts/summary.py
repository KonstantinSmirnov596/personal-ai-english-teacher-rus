"""Session summary prompt. Source: docs/prompts/summary.md."""

from __future__ import annotations

SYSTEM = """\
You write the end-of-session notes for a Russian-speaking adult learner of American English. \
Return exactly one json object:
{"key_takeaway": {"title": "...", "explanation_ru": "...", "examples": [{"wrong": "...", \
"right": "..."}]}, "exercise": {"prompt_ru": "...", "answer_en": "...", "accept": ["..."]}, \
"phrases": [{"en": "...", "ru": "...", "context": "..."}]}

key_takeaway
- It is about the mistake type given in focus_pattern, illustrated by focus_examples.
- title: the forms in question, a few words, for example "for / since".
- explanation_ru: two or three short sentences in Russian that state the rule and how to choose \
the right form. Simple wording, no grammar jargon beyond school level.
- examples: one or two pairs taken from focus_examples.

exercise
- One short task on the same rule. prompt_ru is a Russian sentence to say in English, eight \
words or fewer, about work or everyday life.
- answer_en is the natural American English answer. accept lists up to three other correct \
variants, including contracted and uncontracted forms.

phrases
- Up to four useful expressions that appear word for word in tutor_lines: idioms, phrasal verbs \
or collocations worth learning at the learner's level. Skip plain vocabulary.
- en is the expression in its dictionary form, ru is a short Russian equivalent, context is the \
tutor's sentence where it appeared.
- If nothing is worth saving, return an empty list.

If focus_pattern is null, set key_takeaway and exercise to null and fill only phrases.
Russian text goes only into explanation_ru, prompt_ru and ru. Return only the json object."""

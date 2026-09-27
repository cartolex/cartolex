# Prompt templates — the editable surface

The engine's LLM prompt defaults live **here, as plain-text files**, not in
code. They are placeholder defaults: edit them (or fork the repo and replace
them) to retarget the engine to your domain — nothing else in the engine pins
a field of research.

| File | Used by | Required placeholders |
|---|---|---|
| `triage_typed_system.txt` | keyword triage (stage 2) | `{domain_title}`, `{subfields_block}`, `{reference_language_name}`, `{person_whitelist_block}` |
| `labels_translate_system.txt` | display-label translation pass (`labels.fill_missing_label_sides`) | `{language_name}` |

The prompts are **language-parameterized** (see the language model in INTEGRATION.md
§3): `{reference_language_name}` is filled with the human name of
`KeywordsConfig.reference_language` (the canonical-label language). The prompt
wording stays English; only the target languages are parameterized. With the defaults (`reference_language="en"`,
`display_languages=("fr","en")`) the rendered prompts match the historical
English-canonical + French-`label_fr` behaviour.

Resolution order at runtime:

1. **Per-workspace override** — the context's `paths.triage_prompt_override_txt`
   (`<workspace>/manual_data/llm_prompts/triage_typed.txt`; currently supported
   for the triage template; must keep the placeholders).
2. **The run's prompt directory** — `RunContext.prompt_dir`, by default these
   packaged files (read with `importlib.resources`, so a checkout, an installed
   wheel and a zip all work). Templates are read and checked for their
   placeholders when a stage uses them, never at import; a missing file or
   placeholder raises `PromptTemplateError` naming both
   (`cartolex/lexicon/prompt_store.py`).

Notes on the shipped defaults:

- They were tuned on **natural-science corpora**: the triage template's scope
  rule treats non-quantitative humanities terms as out-of-scope. For
  social-science / humanities corpora, edit that rule in
  `triage_typed_system.txt` (or use the workspace override).
- The triage LLM response cache is keyed by *(term, domain title, model)* —
  not by prompt text — so editing the triage template does **not** invalidate
  cached verdicts. Delete the workspace LLM cache if you change semantics and
  want terms re-judged.
- The label-translation cache, on the contrary, is keyed by the exact
  rendered prompt (system text included): editing
  `labels_translate_system.txt` makes the next translation pass call the model
  again for every label.

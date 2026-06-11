# Prompt Templates

Agent instructions live here as editable Markdown templates — not inline in
Python — so prompt iteration is a content change, not a code change.

## How it works

- `prompt_loader.render_prompt("sales_agent")` loads `sales_agent.md`,
  substitutes `$variables`, and returns the instruction string.
- Variables use `string.Template` syntax (`$business_name`, `${product_plural}`).
  Literal `$` must be written `$$`. JSON braces need no escaping.
- Available variables (see `prompt_loader.build_context()`):
  `business_name, business_address, business_phone, business_hours,
  product_singular, product_plural, personality, greeting, spec_fields,
  today_str, today_iso`.
- Extra variables can be passed per-call: `render_prompt(name, foo="bar")`.

## Overriding without a deploy

Set `THO_PROMPT_DIR=/path/to/dir` to load templates from another directory
(falling back to this one per-file). Useful for prompt experiments; remove the
env var to return to the repo versions.

## Rules

- Unresolved `$variables` fail loudly at load time (tests enforce this).
- Keep the `property` JSON block contract in `sales_agent.md` in sync with
  `frontend/src/components/SafeMarkdown.jsx` property-card parsing.
- Never put PII, secrets, or customer examples in templates.

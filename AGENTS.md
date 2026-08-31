# Working on Action Control

Conventions for anyone — human or AI agent — changing this repository.
Most of these exist because they were broken at least once.

## What this is

A Home Assistant custom integration (HACS) that watches the `call_service`
event, and for each configured rule verifies that the command actually took
effect on its target entities — retrying, escalating to a recovery action
and notifying when it doesn't.

## Module map

| File | Responsibility |
|---|---|
| `coordinator.py` | `ActionControlEngine`: listens to `call_service`, resolves targets, dispatches watchdog runs, owns per-rule state and the persisted escalation cooldowns. |
| `watchdog.py` | One run per (rule, entity): early exit, verify, retry, escalate, verify the escalation, replay, notify. |
| `matching.py` | Which entities a call targets, and which rules apply to a call. |
| `comparator.py` | What to expect from a service call, and tolerance-based comparison. |
| `config_flow.py` | Setup flow (single instance) + options flow (the rule wizard). |
| `models.py` | `Rule` and `RuleRunStatus` dataclasses, and their dict round-trip. |
| `messages.py` | Runtime notification texts (EN/FR), picked from `hass.config.language`. |
| `context_registry.py` | Remembers self-issued `Context` ids — the anti-loop mechanism. |
| `domain_defaults.py` | Per-domain presets and the service → expected-state tables. |

## Non-negotiables

- **`strings.json` and `translations/en.json` are byte-identical.** Edit one,
  copy it over the other, in the same commit. `tests/test_translations.py`
  enforces this.
- **Everything bilingual stays in sync**: `translations/en.json` ↔ `fr.json`,
  `docs/documentation.md` ↔ `docs/documentation.fr.md`, `README.md` ↔
  `README.fr.md`. Changing one without the other is an incomplete change.
- **Never commit** `.venv*/`, `__pycache__/`, `.pytest_cache/`.

## Traps specific to this codebase

- `CONF_VERIFICATION_MODE`, `CONF_ESCALATION_CHECK_ENABLED`,
  `CONF_EXCLUSIONS_ENABLED` and `CONF_GO_BACK` are **wizard-only keys**.
  They are not fields on `Rule` and must never be persisted; the first three
  gate which steps run and map onto the real fields `wait_for_change`,
  `escalation_check_entity_id` and the three exclusion lists.
- `Rule.from_dict` skips keys that are **absent or `None`**, falling back to
  the dataclass default. That is a useful safety net for old stored rules,
  but it also means a field the wizard stops collecting degrades silently —
  no exception, no failing test.
- `tests/test_config_flow.py` depends on the **number and order of wizard
  steps** (several `for _ in range(3)` loops). Inserting or moving a step
  breaks it, and the worst case fails late on an unrelated assertion rather
  than at the step itself.
- `test_services_step_offers_the_chosen_domains_registered_services` requires
  `add_rule_services` to stay the step immediately after `add_rule` **when
  exclusions are unticked** — which is the default, and what that test
  submits.
- Stepping back is home-made: `data_entry_flow` has no back navigation, so
  every wizard step carries a `go_back` field, stores its input in the draft
  *before* validating, and hands over to `_step_back`. A new wizard step
  needs three things or the back button lies: the field (via
  `_wizard_schema`), the `go_back` check in its handler, and an entry in
  `_wizard_order` — behind its gate, if it is conditional.
- The anti-loop context registry is **deliberately not** persisted across
  restarts: a restart already kills every in-flight run.
- Escalation cooldowns *are* persisted (`Store`), and are armed **before** the
  recovery action runs, so entities failing together can't fire it repeatedly.

## Before pushing

```bash
python -m pytest -q
ruff check custom_components tests
```

`requirements_test.txt` pins a `pytest-homeassistant-custom-component`
version that needs **Python 3.14**. On 3.13, install it unpinned to work
locally — CI is the authority.

Also worth knowing: `ruff` is configured for `line-length = 100`, and CI runs
hassfest + HACS validation, which do **not** check translation completeness
or Mermaid syntax. Validate Mermaid blocks with a real parser before pushing;
a broken diagram fails no test.

## Branches, PRs and releases

- Develop on a `claude/...` branch; open the PR against **`main`**, never
  `dev`.
- Never reuse a merged PR for follow-up work — branch again from an
  up-to-date `main`.
- Bump `manifest.json` and name the `CHANGELOG.md` section with that same
  version **in the same commit**. Leaving the section as `[Unreleased]` means
  someone has to remember to rename it at release time, and that has already
  been forgotten once.
- A release users can actually get needs **both** a `manifest.json` version
  bump **and** a matching GitHub release/tag. Without the release, HACS shows
  nothing. Record it in `CHANGELOG.md`.

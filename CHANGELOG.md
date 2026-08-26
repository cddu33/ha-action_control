# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions match
the published GitHub releases — which is what HACS offers users as an update.

## [Unreleased]

### Added
- `log_entity_info` is now exposed as an attribute of each rule's status
  sensor, so you can tell from the UI whether that rule's info-level summary
  is switched on without downloading diagnostics.
- `AGENTS.md` (with `CLAUDE.md` pointing to it) documenting the repository's
  conventions and pitfalls, and this changelog.
- `tests/test_translations.py`, guarding that `strings.json` stays identical
  to `translations/en.json` and that every language has the same key set — a
  missing key breaks nothing at runtime, it just shows a raw field name.

### Fixed
- The documentation's logging section now lists what is logged without
  enabling debug, and warns that *Settings → System → Logs* only shows
  `warning` and above — so the per-rule `info` summary needs **Load full
  logs** or `home-assistant.log`. It looked like a broken feature.
- Recipes referenced *Wait for change* and *Escalation: enabled*, field names
  removed from the UI in 0.5.0. They now use the current labels, and the
  KLF200 recipe shows the escalation verification it was written for.

## [0.5.0]

### Added
- Mermaid diagrams for the verification lifecycle, the wizard flow and the
  anti-loop mechanism.

### Changed
- The rule wizard is now conditional: a *what it should do* step collects the
  capabilities you want, and later steps only ask for the settings those
  choices need. The escalation steps are skipped entirely when unticked, so a
  simple rule is still four steps but with far shorter forms.
- Verifying the recovery action moved to its own step; notifications moved to
  the features step, since the escalation step they shared is now conditional.
- *Wait for change* became an explicit Delay / Movement choice.

### Fixed
- A rule with no domain selected was accepted and became permanently inert
  with no message.
- Movement mode with no attribute to watch silently fell back to a snapshot
  comparison. Both are now rejected in the form.

## [0.3.0]

### Added
- Escalation actions can be verified: an optional entity + expected state
  re-runs the recovery action (reusing the rule's retry and backoff settings)
  until it is confirmed, before the original command is replayed.
- Services `action_control.run_rule` (test a rule on demand) and
  `action_control.reset_escalation_cooldown`.
- A diagnostics platform, and a repair issue when a rule targets an area,
  label or device that no longer exists.

### Changed
- `Rule.to_dict` / `from_dict` derive from `dataclasses.fields()` instead of
  repeating every field by hand.

## [0.2.1]

### Added
- Retry backoff modes (`constant`, `linear`, `exponential`), capped at one
  hour.
- `response_duration` per run, exposed on the status sensor.
- Optional per-rule `info`-level summary (entity, outcome, response time,
  attempt count), off by default.
- An explicit `scene` preset, documenting why domains with nothing to verify
  resolve immediately.

## [0.2.0]

Initial release: generic, fully UI-configurable verification of Home
Assistant service calls, with tolerance-based comparison, movement detection
for covers, retries, escalation, notifications, a per-rule status sensor, and
anti-loop protection based on Home Assistant's `Context`.

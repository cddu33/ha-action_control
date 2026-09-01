# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions match
the published GitHub releases — which is what HACS offers users as an update.

## [0.6.2]

### Changed
- The rule menu's buttons are named in one or two words instead of a sentence,
  and the two sections that do the opposite of one another finally answer each
  other: **Inclusion** and **Exclusion**. The rest follow — *Behavior*,
  *Verification*, *Recovery*, *Recovery check*, *Save*. What a short label no
  longer spells out, the summary line under it does; the two sections that had
  no summary now have one. Step titles and the documentation's section headings
  follow the same names, so a button and the screen it opens are called the
  same thing.
- The tick box that opens the exclusion section reads *Exclude some entities or
  devices*, and its fields *Entities to exclude* / *Devices to exclude*.

## [0.6.1]

### Changed
- The *Back to the previous step* tick box added in 0.6.0 is gone. It was a
  toggle you had to flip and then submit, and it read as a setting of the rule
  rather than as navigation. It is replaced by a **rule menu**: one button per
  section — Targeting, What to leave out, Services, What it should do,
  Verification & retries, the recovery-action sections — each with a one-line
  summary of what it holds, plus *Save the rule*. Open a section, correct it,
  and you are back on the menu.
  The guided pass for a new rule is unchanged; it now ends on that menu instead
  of saving straight away, so a mistake made at the first step is fixed before
  anything is written.
  Home Assistant's flow engine has no back navigation, and an integration
  cannot put a button on a form — `async_show_menu` is the only thing the
  frontend renders as clickable buttons.
- **Editing a rule opens that menu directly.** Changing one field no longer
  means walking every form of the wizard again.

### Fixed
- The two gates that exist only in the wizard (*Leave out some entities or
  devices*, *Verify the recovery action worked*) are not stored on the rule, so
  a rule opened for editing had neither. The exclusion and recovery-check
  sections were therefore missing from the menu for exactly the rules that use
  them. They are now derived from the fields they map onto.

## [0.6.0]

### Added
- Exclusions are now a step of their own, shown only when you tick *Leave
  out some entities or devices* on the targeting step. It offers three ways
  to drop something: **picking entities from a list** (restricted to the
  rule's domains, which is what makes it readable), **picking devices**
  (every entity they expose goes), and the glob patterns that were there
  before, now for the wider cases. Unticking the box on an existing rule
  clears what it excluded, rather than leaving a filter in force that no
  step shows any more.
- Every step of the rule wizard ends with a **Back to the previous step**
  tick box. Home Assistant's flow engine has no back navigation of its own,
  so each step carries the control and hands over to the step before it —
  skipping the conditional steps that were never shown, and keeping
  everything typed on both steps. From the first step it returns to the
  menu and abandons the rule.

### Changed
- The list of exclusion patterns left the targeting form, which had grown
  to eight fields, for the new step.

### Notes
- Excluding a *device* is not the way to split a switch also exposed as a
  light: `switch_as_x` attaches the derived entity to the same device as
  the switch it wraps, so the exclusion would drop both. Exclude the
  duplicate *entity*. Both the wizard and the documentation say so.

## [0.5.5]

### Added
- Targeting gained a list of **entity ID patterns to exclude**. An entity
  matching any of them is dropped whatever the other filters say, which lets a
  rule cover a whole domain minus the few entities that duplicate one another.
  It takes a list rather than a single pattern on purpose: a switch also
  exposed as a light (Home Assistant's *change device type*) is watched twice
  per command, and those pairs rarely share one prefix.

### Fixed
- A rule's retry count was stored as the float the number selector hands back,
  so it read as `retry 1/4.0` in the logs even though the field is declared as
  an integer. Coerced on load, which also repairs rules already saved.

## [0.5.4]

### Fixed
- A command that contradicted one still being verified — turning a light back
  off while the check for `turn_on` was running — was reported as a failed
  verification, with a warning and a notification, and could even fire the
  recovery action for an order the user had already replaced. The check for a
  newer command was missing at the one point where both verification modes
  converge: between the end of the retry loop and the failure path. It was
  therefore reached whenever the newer command landed during the last wait,
  or on any rule with no retries configured.
- Document that only commands issued as service calls can supersede a check.
  A physical switch press, a directly bound remote, or `homeassistant.turn_off`
  (a different domain from `light`/`switch`) stay invisible, and a mismatch
  they cause still reads as a failure.

## [0.5.3]

### Added
- `log_entity_info` is now exposed as an attribute of each rule's status
  sensor, so you can tell from the UI whether that rule's info-level summary
  is switched on without downloading diagnostics.
- `AGENTS.md` (with `CLAUDE.md` pointing to it) documenting the repository's
  conventions and pitfalls, and this changelog.
- `tests/test_translations.py`, guarding that `strings.json` stays identical
  to `translations/en.json` and that every language has the same key set — a
  missing key breaks nothing at runtime, it just shows a raw field name.

### Changed
- The minimum Home Assistant version dropped from `2026.3.0` to `2025.3.0`.
  The old value was only there so the icon would be served natively, but HACS
  treats it as a hard floor and refused to install below it. `2025.3.0` is the
  real minimum the code needs — `AddConfigEntryEntitiesCallback` landed there,
  and every other Home Assistant API this integration uses predates it.

### Fixed
- A rule saved before 0.5.0 could carry an escalation-check entity with no
  state to compare it against — the two fields were independently optional
  back then. That check can never pass, so it re-ran the recovery action
  once per retry, for nothing, while holding the entity's slot. The check is
  now attempted only when both fields are set, and only if the recovery
  action actually ran.
- A check that had already been superseded by a newer command waited for the
  in-flight run to finish before discovering it should be dropped. Since a
  run holds its slot across every delay — including escalation and the
  replay delay — those obsolete checks could pile up. They now exit
  immediately.
- The documentation's logging section now lists what is logged without
  enabling debug, and warns that *Settings → System → Logs* only shows
  `warning` and above — so the per-rule `info` summary needs **Load full
  logs** or `home-assistant.log`. It looked like a broken feature.
- Recipes referenced *Wait for change* and *Escalation: enabled*, field names
  removed from the UI in 0.5.0. They now use the current labels, and the
  gateway-restart recipe shows the escalation verification it was written
  for.

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

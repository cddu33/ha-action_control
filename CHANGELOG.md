# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions match
the published GitHub releases — which is what HACS offers users as an update.

## [0.7.0]

### Added
- **Persistent notification at every retry** (rule option, off by default).
  Each time a rule re-issues a command — and when it replays it after the
  recovery action — a `persistent_notification` gives the attempt number
  and the total number of retries for that entity. It is one notification
  per rule and entity, updated in place, so it stays until dismissed
  without piling up.
- **Retry counter.** Every retry is counted, whether or not the option
  above is on, and shown on the rule's status sensor as `retry_count`
  (all entities) and `retry_counts` (per entity). The count is persisted
  across restarts, alongside the escalation cooldowns.
- **`action_control.reset_retry_count`** service to put a rule's counter
  back to zero.

## [0.6.7]

### Fixed
- **A check that settles during the plain `check_delay` wait is no longer
  logged as "verified OK after retry."** Found in a real install's debug
  log: 53 of 55 zero-retry resolutions (`attempt=0`, confirmed by the
  per-rule `info` summary) carried that message, because the debug line
  didn't distinguish "the entity caught up on its own" from "a retry
  actually fixed it." Grepping the debug log to see how often retries are
  really needed would have overcounted them almost every time. The two
  cases now log distinctly ("verified OK after retry" only when
  `attempt > 0`, "verified OK, no retry needed" otherwise); the published
  status and `attempt` count themselves were always correct.

## [0.6.6]

A follow-up audit of 0.6.5: the same false-failure class the light bench
found is checked, by analogy, across every other domain the integration
models, and the light bench itself is checked against what its own PR
description claimed.

### Fixed
- **A cover/valve with no position feedback no longer times out forever.**
  Movement mode now falls back to the normal state-based check when the
  entity has never reported `change_attribute` at all — a plain open/close
  cover (no `current_position`) used to wait out the full timeout, retry,
  and report `failed` every single time, even though it opened or closed
  exactly as asked.
- **`climate.set_temperature`/`set_humidity` and `humidifier.set_humidity`
  outside the entity's own range are excluded from verification**, instead
  of expecting a value Home Assistant itself rejects before the entity ever
  sees the call (not a clamp like light's color temperature — a straight
  rejection, so there's nothing a retry could fix).
- **`siren.turn_on` with a `duration` is never verified or replayed**, the
  same way a light's `flash` isn't: the siren turns itself back off on its
  own, and a retried `turn_on` would only re-arm it.
- **A light `effect` suppresses the whole check**, like `flash` already
  did: it can change brightness and color however it likes.
- **A light group's own color is never compared.** Its color/color
  temperature is a mean across whichever members are on and report it,
  with the group's own color mode picked by majority vote — neither is
  predictable from a single bulb's math, and the light bench's own group
  fixture (claimed in 0.6.5's description, never actually present) caught
  it immediately once added.
- **Legacy mireds `color_temp` on `light.turn_on`** is mapped to
  `color_temp_kelvin` like `kelvin` already was — matters only on a Home
  Assistant core old enough to still accept it (removed from the service
  schema in 2026.1).
- **`rgbw_color`/`rgbww_color` sent directly** are now exercised by the
  light bench; they were already handled in code but never actually
  tested.

### Added
- **`vacuum` and `media_player` now have a modeled expected state**
  (`start`/`pause`/`stop`/`return_to_base`/`clean_spot`, and
  `turn_on`/`turn_off`/`media_play`/`media_pause`/`media_stop`), where
  before every rule on these domains was a silent no-op — it could never
  detect a real failure. `media_player.turn_on` is deliberately permissive
  (`on`, `idle`, `playing`, `paused` or `buffering`), since a player can
  skip straight from "turning on" to any of those.
- New built-in presets for `vacuum`, `media_player`, `fan`, `humidifier`
  and `siren`, pre-filling sensible defaults the same way `light`/`switch`/
  `cover` already did.
- New recipes: Vacuum watchdog, Fan speed watchdog, Humidifier setpoint.

### Docs
- EN/FR documentation updated for all of the above, including new "Known
  limitations" entries for `vacuum.stop`'s best-effort target state,
  `media_player.media_play_pause` being left unmodeled, and the
  climate/humidifier range check not converting units.

## [0.6.5]

### Fixed
Lights, checked end to end against Home Assistant's own light component and
simulated bulbs of every kind (xy, hs, RGB, RGBW, RGBWW, white-only, dimmer,
on/off, and a light group):
- **Colors are compared as a chromaticity** (`xy_color`), computed the way
  Home Assistant converts the request for that light, within a distance of at
  least 0.06. Comparing `rgb_color` failed for nearly every color sent to a
  Hue or Zigbee bulb: those store xy, clip it to their gamut, and Home
  Assistant recomputes `rgb_color` from that — pure red came back as
  `[255, 43, 0]`. Only lights that work in RGB are still compared on
  `rgb_color`, raw or at full intensity.
- A **toggle is retried as `turn_on`/`turn_off`** (`open_cover`/`close_cover`
  for covers). Retried as a toggle, it turned off a light that had come on
  with its brightness reported late.
- `brightness_step` and `flash` are never replayed by a retry, so a retry no
  longer dims the light one more step or blinks it again.
- A `flash` is no longer expected to leave the light on, and dimming down past
  zero (which Home Assistant turns into "off") is no longer a failure.
- A color temperature sent to a light with no white channel is checked as the
  color Home Assistant emulates it with, instead of not at all.
- A check cancelled by a newer command no longer leaves the rule's sensor on
  `retrying`: it goes back to `idle`.
- The 0.6.4 hue comparison could reject a dim RGB color that matched exactly;
  `rgb_color` now matches either as reported or at full intensity.

## [0.6.4]

### Fixed
Fewer failures reported for commands that actually worked:
- A rule that watches only some services (say `light.turn_on`) now drops its
  check when the entity is turned off, closed, locked... by a service it does
  not watch. It used to report the light as failing — and retry, switching it
  back on.
- A light `transition` is waited for: it is added to the check delay and to
  each retry delay, instead of checking a light still fading and restarting
  the fade with a retry.
- `light.turn_on` with `brightness: 0` (or `brightness_pct: 0`) expects the
  light **off**, as Home Assistant turns it off.
- A toggle that turns something off no longer expects the attributes in its
  data (a light that is off has no brightness).
- A color temperature outside the light's range is expected at the nearest
  end of that range; `color_temp_kelvin` is not compared on a light with no
  color-temperature mode, colors on a light with no color mode, brightness on
  an on/off-only light.
- `rgb_color` is compared as a hue: lights driven in hs or xy report their
  color at full intensity, so `[200, 0, 0]` comes back as `[255, 0, 0]`.

### Documentation
- `homeassistant.turn_on`/`turn_off` and scenes were wrongly listed as
  invisible to Action Control: Home Assistant forwards them to each entity's
  own domain, and they are seen like any other command.

## [0.6.3]

### Changed
- A newer command on an entity now **cancels** the check still running for
  the previous one, instead of letting it run on until its next retry. Turn
  a light on, then off a few seconds later: the "on" check stops there — no
  more waiting, retrying, escalating or replaying "on" — and the "off"
  command is verified straight away rather than queueing behind it, which
  could take minutes with escalation and a long replay delay.
- A recovery action that is already running when that happens is left to
  finish, so a gateway restart is never cut off halfway; the old command is
  just not replayed after it.

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

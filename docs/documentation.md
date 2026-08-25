# Action Control — Documentation

*[English](documentation.md) | [Français](documentation.fr.md)*

## Table of contents

- [How it works](#how-it-works)
- [Configuring rules](#configuring-rules)
- [Rule reference](#rule-reference)
- [What gets compared](#what-gets-compared)
- [Status sensor](#status-sensor)
- [Recipes](#recipes)
- [Debug logging](#debug-logging)
- [Known limitations](#known-limitations)

## How it works

Action Control listens to Home Assistant's internal `call_service` event —
the event fired for *every* service call, regardless of what triggered it
(a person, an automation, a script, a voice assistant, another
integration). For each rule you have configured, on a matching call it:

1. **Resolves the target entities** — from `entity_id`, `device_id`,
   `area_id`, `label_id` and/or `floor_id` on the call, using the entity,
   device and area registries, then keeps only the entities that also pass
   the rule's own filters (domain, patterns, areas, labels, devices).
   Disabled entities, and entities with no state at all, are skipped: they
   could only ever fail the check.
2. **Computes what to expect** — the state the service implies, plus the
   attributes the call actually carried. This happens synchronously, in
   the event callback itself, so a `toggle` is judged against the state as
   it was the instant the command was issued. See [What gets
   compared](#what-gets-compared).
3. **Checks for an immediate match.** If the entity already reflects the
   requested state/attributes the instant the event fires (a no-op
   command, or one the target integration applied instantly), the rule
   resolves immediately — no delay, no notification.
4. Otherwise, depending on the mode:
   - **Delay mode** (the default): waits `check_delay` seconds, then
     compares the entity's state/attributes against what was requested,
     with tolerance. On a mismatch it re-issues the command and retries up
     to `retries` times, `retry_delay` seconds apart. Worst case:
     `check_delay + retries × retry_delay`.
   - **Movement mode** (`wait_for_change`, the default for covers):
     instead of comparing a snapshot after a fixed delay, waits up to
     `change_timeout` seconds for `change_attribute` to actually start
     changing. If it doesn't, that is the failure — the command is
     re-issued and the wait starts over, up to `retries` times.
     `retry_delay` is not used in this mode; worst case:
     `(retries + 1) × change_timeout`.
5. **On persistent failure**, if escalation is enabled and its cooldown
   has elapsed: runs the configured recovery action, arms the cooldown,
   waits `escalation_replay_delay` seconds, then replays the original
   command once more.
6. **Notifies** you (persistent notification and/or a `notify.*` service)
   with what was expected vs. what was actually observed.

Each retry re-issues the command for that one entity: the original target
keys (`entity_id`, `device_id`, `area_id`, `label_id`, `floor_id`) are
replaced by that entity's id, and the rest of the service data is kept
as-is.

Only one run at a time per (rule, entity) pair: if the same entity is
commanded again while a check is still in flight, the second run waits for
the first one to finish instead of racing it, and the older run is then
dropped rather than re-issuing a command that no longer reflects what was
asked. Several rules matching the same call each run independently.

### Anti-loop protection

Every command Action Control re-issues (a retry, the escalation action
itself, or the post-escalation replay) carries its own freshly created
Home Assistant `Context`, remembered internally for 120 seconds. The event
listener recognizes and ignores any `call_service` event carrying one of
these self-issued contexts *before* any processing — this is what prevents
a retry from re-triggering the same or another rule, with no guard entity
and no extra configuration.

That memory is intentionally in-process only (a Home Assistant restart
clears it). There is never anything meaningful to carry across a restart,
since a restart also stops any in-flight watchdog run.

## Configuring rules

Everything is configured from the integration's **Configure** button
(Settings → Devices & services → Action Control). The menu offers:

| Menu entry | What it does |
|---|---|
| Add a rule | Wizard: what to watch → which services → verification & retries → escalation & notifications. |
| Edit a rule | The same wizard, pre-filled with the selected rule. |
| Delete a rule | Asks for confirmation, then removes the rule and its sensor. |
| Global settings | Master switch and default values, see [Global settings](#global-settings). |

Only one instance of the integration is needed — a second setup attempt is
aborted on purpose. Saving any change reloads the integration so the
sensors follow the rule list; that reload also cancels any check still in
flight and resets the status sensors to `idle`.

## Rule reference

### Targeting

| Field | Description |
|---|---|
| Name | Label shown on the rule's status sensor and in notifications. |
| Rule enabled | Off pauses the rule without deleting it — its sensor stays, nothing is watched. Paused rules are prefixed with ⏸ in the rule pickers. |
| Domains | One or more domains this rule watches (e.g. `light`, `switch`, `cover`). Required. The picker lists the domains currently present in your instance, translated, and also accepts a domain typed by hand. |
| Services | Services within those domains to watch (e.g. `turn_on`). Suggestions cover every service of the chosen domains. Leave empty to watch every service in those domains. |
| Entity ID pattern | Optional glob pattern (e.g. `cover.volet_*`) the `entity_id` must match. Case-sensitive. |
| Friendly name pattern | Optional glob pattern matched against the entity's name, case-insensitively. |
| Areas / Labels / Devices | Optional filters — an entity matches if it (or its device) belongs to one of the selected areas/labels/devices. |

Filters are combined with AND: an entity must satisfy every filter that is
set. A rule with no pattern/area/label/device filter at all matches every
entity in scope for its domain(s)/service(s) — e.g. "watch every light".

### Verification

| Field | Description | Default (range) |
|---|---|---|
| Delay before the first check | Seconds to wait after the command before the first comparison (delay mode only). | 2 (0–120) |
| Attributes to check | Attributes compared in addition to the state (e.g. `brightness`, `rgb_color`). Only those actually present in the service call are compared. | none |
| Tolerances | `attr:value, attr2:value2` — per-attribute numeric tolerance. List attributes (like `rgb_color`) apply the tolerance element by element. Entries that can't be parsed are ignored. | none (exact match) |
| Number of retries | How many times to re-issue the command if verification fails. | 2 (0–10) |
| Delay between retries | Seconds between each retry (delay mode only). | 2 (0–600) |
| Delay growth between retries | How the delay between retries grows: `constant` (same delay every time), `linear` (delay × attempt number), or `exponential` (delay doubles each time, capped at 3600 s). Only affects delay mode — movement mode has no delay between retries to begin with. | constant |
| Wait for change | Switches to movement mode: waits for `change_attribute` to actually change instead of comparing a snapshot. | off |
| Attribute to watch | The attribute movement mode watches (e.g. `current_position`). Required for that mode: left empty, the rule stays in delay mode. | — |
| Timeout waiting for the change | Seconds to wait for that attribute to change before considering it a failure. | 45 (1–600) |
| Log a summary for this rule at info level | When on, every entity's final outcome (ok/escalated/failed) for this rule is also logged at `info` level — entity, outcome, response time, attempt count — visible without enabling debug logging. Off by default; the full step-by-step trace is still only in the debug log. | off |

When a rule targets exactly one of the `light`, `switch` or `cover`
domains, sensible defaults are pre-filled automatically:

| Domain | Pre-filled defaults |
|---|---|
| `light` | Attributes `brightness`, `rgb_color`, `color_temp_kelvin`, `xy_color`, with tolerances `5`, `5`, `100`, `0.01`. |
| `switch` | State only, no attribute. |
| `cover` | Movement mode on `current_position`, 45 s timeout. |

Any other domain — or a rule targeting several domains at once — starts
from a plain state-only check that you can refine with the fields above.

### Escalation & notifications

| Field | Description | Default (range) |
|---|---|---|
| Enable escalation action | Turns on the recovery-action step after persistent failure. | off |
| Escalation action | Any Home Assistant action sequence (service call, script, ...) — the same action editor as in automations. An action that fails is logged and does not break the run. | — |
| Minimum time between two escalations | Cooldown before the same rule may escalate again, in seconds. Counted from the moment the action has run, and shared by every entity of the rule. | 300 (0–86400) |
| Delay after escalation before replaying the command | Seconds to wait after the escalation action before replaying the original command. | 90 (0–3600) |
| Entity to verify after the escalation action | Optional. If set, Action Control checks this entity's state after running the escalation action, instead of assuming it worked. | — |
| State it should reach | The state the entity above must reach (e.g. `on`). Required when an entity is set. | — |
| Delay before checking it | Seconds to wait before the first check. | 5 (0–600) |
| Notify via a persistent notification | Creates a `persistent_notification` titled `Action Control: <rule name>` on final failure. | on |
| Also notify via this notify service | Also calls this `notify.*` service on final failure, with the same title and message. | — |

The cooldown is armed *before* the recovery action runs, and it survives a
restart, so entities failing at the same moment cannot fire the action
several times over. Escalation enabled without a configured action does
nothing and is logged as a warning.

When "Entity to verify" is set, the escalation action is re-run (up to
"Number of retries" times, with the same delay-growth setting as the
regular retries) until that entity reaches the expected state, before the
original command gets replayed. This is for recovery actions that can fail
too — a gateway restart switch that doesn't always take on the first try,
for instance. If it's still not confirmed after all retries, the original
command is replayed anyway (a warning is logged), exactly as if no check
had been configured.

The failure notification is sent right after the replay, without waiting
again: it describes the state observed at that moment, and mentions that a
recovery action was triggered. It carries a stable notification id per
(rule, entity), so a repeated failure replaces its notification instead of
stacking a new one. The replay itself is not re-verified — if it works, the
next status update comes from the next command on that entity.

### Global settings

| Field | Description | Default (range) |
|---|---|---|
| Action Control enabled | Master switch. When off, `call_service` events are ignored: no check, no retry, no notification. Rules keep their configuration. | on |
| Default number of retries for new rules | Pre-fills the retry field of a **new** rule. Existing rules keep their own value. | 2 (0–10) |
| Default delay between retries for new rules | Same, for the delay between retries. | 2 (0–600) |

## What gets compared

**Expected state.** It is derived from the service being called:

| Service | Expected state(s) |
|---|---|
| `turn_on` / `turn_off`, on/off domains only | `on` / `off` |
| `toggle`, on/off domains only | the opposite of the state at the moment of the call |
| `cover.open_cover`, `valve.open_valve` | `open` or `opening` |
| `cover.close_cover`, `valve.close_valve` | `closed` or `closing` |
| `cover.toggle`, `valve.toggle` | `closed`/`closing` if it was open, `open`/`opening` otherwise |
| `lock.lock` / `lock.unlock` / `lock.open` | `locked`/`locking`, `unlocked`/`unlocking`, `open`/`opening`/`unlocked` |
| any other service | none — only the attributes are compared |

The on/off domains are `light`, `switch`, `fan`, `siren`, `input_boolean`,
`humidifier`, `remote` and `automation`. Anything else — `climate`,
`media_player`, `water_heater`, ... — gets no expected state from
`turn_on`/`toggle`, because "on" is not what those entities report. States
that mean "on its way" (`opening`, `closing`, `locking`, ...) are accepted:
that is what movement mode is for.

**Expected attributes.** An attribute listed in *Attributes to check* is
only compared if the service call actually carried it: `light.turn_on`
without `brightness` does not check the brightness, even when `brightness`
is in the list. Two aliases handle service-data keys that differ from the
state attribute name:

- `cover.set_cover_position`, `valve.set_valve_position`: `position` →
  `current_position`
- `cover.set_cover_tilt_position`: `tilt_position` → `current_tilt_position`
- `light.turn_on`: `brightness_pct` → `brightness` (converted to 0–255) and
  `kelvin` → `color_temp_kelvin`; an explicit `brightness` in the call wins

**Comparison rules.**

- Numbers: match when `|expected − actual| ≤ tolerance` (tolerance `0`
  unless configured).
- Lists/tuples (`rgb_color`, `xy_color`, ...): compared element by element
  with the same tolerance; different lengths never match.
- Text, booleans, anything else: exact match.
- An attribute expected to be `None` always counts as satisfied; an entity
  with no state at all is always a mismatch.

**Domains with nothing meaningful to compare** (scenes, and any domain/
service combination not listed above with no attributes configured either)
get an expected state of `None` and no expected attributes. The early-exit
check on such a rule is then trivially satisfied — it resolves immediately,
with no delay and no retry, the first time it runs. This is intentional: a
scene's own state is a last-activated timestamp, not a target to reach, so
there is nothing to verify beyond the call having been accepted. A `scene`
preset (empty, like `switch`) exists mainly to make this explicit rather
than an implicit side effect.

## Status sensor

Each rule gets one diagnostic sensor named after the rule, grouped under a
single *Action Control* service device. Its state is the last known
outcome:

| State | Meaning |
|---|---|
| `idle` | No check has run yet (also the state right after a reload). |
| `ok` | Last check succeeded — immediately, or after a retry. |
| `retrying` | A check is in progress and the command is being re-issued. |
| `escalated` | Verification failed and the escalation action was run. |
| `failed` | Verification failed (no escalation, or still in cooldown). |

The state labels are translated (English/French), as are the notification
texts. Attributes: `entity_id`, `expected_state`, `expected_attributes`,
`actual_state`, `actual_attributes`, `attempt`, `mismatches`,
`last_checked` (UTC, ISO 8601), `response_duration` (seconds elapsed since
the command was issued, measured from the moment the `call_service` event
fired to the current status — keeps growing while `retrying`, settles once
the rule resolves).

The sensor reflects the rule's **latest** run. When one command targets
several entities, they are all watched, but the sensor keeps the last
update only — the debug log holds the full per-entity picture.

## Services

| Service | Purpose |
|---|---|
| `action_control.run_rule` | Test a rule on demand: re-issues a service call on a chosen entity and lets the rule verify it, exactly as if that call had happened normally. Fields: the rule (pick its status sensor), the entity to test, and optional service data — include a `service` key in it to use something other than the rule's first configured service. |
| `action_control.reset_escalation_cooldown` | Clears a rule's escalation cooldown so it can escalate again immediately, instead of waiting out the configured delay. |

Both take the rule's status sensor as the way to select it, so no rule id
needs to be typed in by hand.

## Diagnostics and repairs

The integration's entry supports Home Assistant's built-in diagnostics
download (rules, global settings, and current statuses, with anything that
looks like a token/password/API key/webhook id redacted) — useful when
filing a bug report.

If a rule targets an area, label, or device that has since been deleted, a
repair issue is raised on reload pointing at the rule by name; it clears
itself once the rule is fixed or the stale target is removed.

## Recipes

### Light watchdog

- Domains: `light`
- Services: `turn_on`, `turn_off`, `toggle` (or leave empty for all)
- Attributes to check: `brightness`, `rgb_color` (pre-filled by default)
- Retries: 2, delay 2 s

Verifies that the requested brightness/color were actually applied, with
tolerance, and retries on mismatch.

### Cover / gateway-restart watchdog (KLF200-style)

- Domains: `cover`
- Entity ID pattern: `cover.volet_*`
- Wait for change: on, attribute `current_position`, timeout 45 s
- Escalation: enabled, action = `switch.turn_on` on your gateway's restart
  switch, cooldown 300 s, replay delay 90 s

Waits for a cover to actually start moving; if it doesn't after the
retries, turns on the gateway's restart switch, waits, then replays the
original command.

### Exact cover position

- Domains: `cover`
- Services: `set_cover_position`
- Wait for change: **off** (delay mode)
- Attributes to check: `current_position`
- Tolerances: `current_position:2`
- Delay before the first check: 30 s (long enough for the cover to travel)

Verifies that a cover reached the position that was requested, ±2 %. The
`position` key of the service call is matched against the
`current_position` attribute automatically.

### Thermostat setpoint

- Domains: `climate`
- Services: `set_temperature`
- Attributes to check: `temperature`
- Tolerances: `temperature:0.2`
- Delay before the first check: 5 s

Catches setpoints silently dropped by a flaky radio link.

### Scene activation

- Domains: `scene`
- Services: `turn_on` (or leave empty)

There is nothing to verify (a scene's state is a timestamp, not a target),
so the rule resolves immediately with no delay and no retry. Configuring
it at all is mostly useful for the response-time metric it still records
(`response_duration` on the sensor, and the info-level log if enabled),
which doubles as confirmation that the `scene.turn_on` call itself went
through.

## Debug logging

Nothing needs a restart to try this: Developer tools → Actions → call
`logger.set_level` with:

```yaml
custom_components.action_control: debug
```

For a persistent setting, add this to `configuration.yaml` and fully
restart Home Assistant:

```yaml
logger:
  logs:
    custom_components.action_control: debug
```

At debug level you'll see which rules a service call matched, which
entities got watched and with what expected state/attributes, each
check/retry attempt with its mismatches, escalation and replay, ignored
self-issued calls, and outgoing notifications. A rule's final failed
verification is always logged at **warning** level, so it's visible even
without debug logging.

### When a rule never triggers

1. Check the master switch in *Global settings*.
2. Find the `call_service ...` debug line and compare the domain in it
   with your rule's domains: a rule only reacts to calls whose **domain**
   is in its list, and some helpers call services under their own domain
   (`homeassistant.turn_on` is a call in the `homeassistant` domain).
3. `... resolved to no entities` means the call carried no resolvable
   target; `has no state, nothing to watch` means the entity does not exist
   in Home Assistant (a disabled entity never gets watched either).
4. No `watching ...` line for your entity means one of the rule's filters
   (pattern, area, label, device) rejected it.
5. `Ignoring self-issued call_service event` means the call came from
   Action Control itself — that's the anti-loop protection doing its job.

## Known limitations

- **Notification texts ship in English and French only**, picked from the
  Home Assistant language, English for anything else.
- **One status sensor per rule**, so a command targeting many entities at
  once only leaves the last outcome on the sensor.
- **Editing rules reloads the integration**, which cancels in-flight
  checks and resets the sensors to `idle`.
- **The post-escalation replay is not verified**; it is the last action of
  the run.

# Action Control — Documentation

*[English](documentation.md) | [Français](documentation.fr.md)*

This page is the detailed reference for **Action Control**. For a quick
overview, features list, and installation steps, see the [main
README](../README.md).

## Table of contents

- [How it works](#how-it-works)
- [Rule reference](#rule-reference)
- [Recipes](#recipes)
- [Debug logging](#debug-logging)
- [FAQ](#faq)

## How it works

Action Control listens to Home Assistant's internal `call_service` event —
the event fired for *every* service call, regardless of what triggered it
(a person, an automation, a script, another integration). For each rule
you've configured, on a matching call it:

1. **Resolves the target entities** — from `entity_id`, `device_id`,
   `area_id`, and/or `label_id` on the call, using the entity/device
   registries (the same resolution the original hand-written automations
   did by hand, generalized to any domain).
2. **Checks for an immediate match.** If the entity already reflects the
   requested state/attributes the instant the event fires (a no-op
   command, or one the target integration already applied instantly), the
   rule resolves immediately — no delay, no notification.
3. Otherwise, either:
   - **Snapshot mode** (the default): waits `check_delay` seconds, then
     compares the entity's state/attributes against what was requested,
     with tolerance. If it doesn't match, the rule re-issues the command
     and retries up to `retries` times, `retry_delay` seconds apart.
   - **Movement mode** (`wait_for_change`, used for covers): instead of
     comparing a snapshot, waits up to `change_timeout` seconds for
     `change_attribute` to actually start changing. If it doesn't, that's
     the failure — reissuing the command and retrying works the same way.
4. **On persistent failure**, if escalation is enabled and its cooldown
   has elapsed: runs the configured recovery action, waits
   `escalation_replay_delay` seconds, and replays the original command
   once more.
5. **Notifies** you (persistent notification and/or a `notify.*` service)
   with what was expected vs. what was actually observed.

Every command Action Control re-issues (a retry, or the post-escalation
replay) carries its own internally tracked Home Assistant `Context`. The
event listener recognizes and ignores any `call_service` event carrying
one of these self-issued contexts *before* any processing — this is what
prevents a retry from re-triggering the same or another rule, with no
guard entity or extra configuration needed. This memory is intentionally
in-process only (a Home Assistant restart clears it) — there is never
anything meaningful to carry across a restart, since a restart also stops
any in-flight watchdog run.

## Rule reference

### Targeting

| Field | Description |
|---|---|
| Name | Label shown on the rule's status sensor and in notifications. |
| Domains | One or more domains this rule watches (e.g. `light`, `switch`, `cover`). Required. |
| Services | Services within those domains to watch (e.g. `turn_on`). Suggested options are the union of every service actually registered across the chosen domains. Leave empty to watch every service in the domain. |
| Entity ID pattern | Optional glob pattern (e.g. `cover.volet_*`) an entity's ID must match. |
| Friendly name pattern | Optional glob pattern matched against the entity's name. |
| Areas / Labels / Devices | Optional filters — an entity matches if it (or its device) belongs to one of the selected areas/labels/devices. |

A rule with no pattern/area/label/device filter at all matches every
entity in scope for its domain(s)/service(s) — e.g. "watch every light".

### Verification

| Field | Description | Default |
|---|---|---|
| Delay before first check | Seconds to wait after the command before the first comparison (snapshot mode only). | 2 |
| Attributes to check | Attributes compared in addition to state (e.g. `brightness`, `rgb_color`). | none |
| Tolerances | `attr:value, attr2:value2` — per-attribute numeric tolerance. List attributes (like `rgb_color`) apply the tolerance per element. | none (exact match) |
| Number of retries | How many times to re-issue the command if verification fails. | 2 |
| Delay between retries | Seconds between each retry. | 2 |
| Wait for change | Switches to movement mode: waits for `change_attribute` to actually change instead of comparing a snapshot. | off |
| Attribute to watch | The attribute movement mode watches (e.g. `current_position`). | — |
| Change timeout | Seconds to wait for that attribute to change before considering it a failure. | 45 |

The `light`, `switch`, and `cover` domains get sensible defaults
pre-filled automatically (light: brightness/rgb_color/color_temp_kelvin/
xy_color with tolerance; switch: state-only; cover: movement mode on
`current_position`). Any other domain starts from a plain state-only
check that you can refine with the fields above.

### Escalation & notifications

| Field | Description | Default |
|---|---|---|
| Enable escalation action | Turns on the recovery-action step after persistent failure. | off |
| Escalation action | Any Home Assistant action sequence (service call, script, ...) — uses the same action editor as automations. | — |
| Minimum time between escalations | Cooldown in seconds before the same rule is allowed to escalate again. | 300 |
| Delay before replaying | Seconds to wait after the escalation action before replaying the original command. | 90 |
| Persistent notification | Creates a `persistent_notification` on final failure. | on |
| Notify service | Also calls this `notify.*` service on final failure. | — |

## Recipes

### Light watchdog

- Domains: `light`
- Services: `turn_on`, `turn_off`, `toggle` (or leave empty for all)
- Attributes to check: `brightness`, `rgb_color` (pre-filled by default)
- Retries: 2, delay 2s

Verifies brightness/color were actually applied after a command, with
tolerance, and retries on mismatch — generalizes the original
lights/switches watchdog automation to any light.

### Cover / gateway-restart watchdog (KLF200-style)

- Domains: `cover`
- Entity ID pattern: `cover.volet_*`
- Wait for change: on, attribute `current_position`, timeout 45s
- Escalation: enabled, action = `switch.turn_on` on your gateway's restart
  switch, cooldown 300s, replay delay 90s

Waits for a cover to actually start moving; if it doesn't after retries,
turns on the gateway's restart switch, waits, then replays the original
command — generalizes the KLF200 gateway-restart automation, with the
cooldown replacing the old external guard-switch entirely.

## Debug logging

Nothing needs a restart to try this: Developer tools → Actions → call
`logger.set_level` with:

```yaml
custom_components.action_control: debug
```

For a persistent setting, add to `configuration.yaml` and fully restart
Home Assistant:

```yaml
logger:
  logs:
    custom_components.action_control: debug
```

At debug level you'll see which rules a service call matched, which
entities got watched and with what expected state/attributes, each
check/retry attempt with its mismatches, escalation and replay, and
outgoing notifications. A rule's final failed verification is always
logged at **warning** level, so it's visible even without debug logging.

## FAQ

**Does the anti-loop memory survive a Home Assistant restart?**
No, and it doesn't need to — it's an in-process registry with a short TTL,
and a restart already stops every in-flight watchdog run, so there's
nothing meaningful left to protect across a restart.

**If I pick two domains, are the suggested services combined?**
Yes — the "which services" step suggests the union of every service
actually registered across all chosen domains (e.g. `light` + `switch`
suggests `turn_on`/`turn_off`/`toggle` merged, deduplicated). You can
still type a service name that isn't suggested.

**Why don't I see the integration's icon in Home Assistant?**
The icon ships in `custom_components/action_control/brand/` and is served
automatically via Home Assistant's [Brands Proxy
API](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api),
which requires **Home Assistant 2026.3.0 or later**. On older versions
the icon won't show, but the integration works the same either way.

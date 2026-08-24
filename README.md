# Action Control

*English | [Français](README.fr.md)*

A generic, fully UI-configurable watchdog for Home Assistant: verify that
your commands (`light.turn_on`, `switch.turn_off`,
`cover.set_cover_position`, or any other service call) actually took
effect.

This integration generalizes the pattern behind two hand-written YAML
automations: check that a command was actually applied, retry it on
failure, notify you, and optionally trigger a recovery action (e.g.
turning on a switch) if the problem persists — all without writing any
YAML, and without risk of a retry loop thanks to a built-in anti-loop
mechanism based on Home Assistant's `Context`.

## Features

- **Generic command verification** — watches any domain/service call, not
  just light/switch/cover.
- **Customizable targeting per rule** — domain(s), service(s), `entity_id`
  glob pattern, friendly-name pattern, areas, labels, and/or devices.
- **Tolerance-based verification** — scalar tolerance (e.g. `brightness`
  ±5), element-wise tolerance for list attributes (`rgb_color`,
  `xy_color`), exact match for text/boolean attributes.
- **Movement/change detection** — for entities like covers, waits for an
  attribute (e.g. `current_position`) to actually start changing instead
  of doing a snapshot comparison.
- **Immediate exit when already satisfied** — a no-op command, or one
  already applied by the time the event fires, resolves instantly with no
  delay or notification.
- **Configurable retries** — retry count and delay, per rule.
- **Configurable escalation** — an optional recovery action (turn on a
  switch, run a script, ...) triggered after persistent failure, with a
  cooldown between escalations and a delay before replaying the original
  command.
- **Notifications** — persistent notification and/or a `notify.*` service
  of your choice, per rule.
- **Built-in anti-loop protection** — every command the integration
  re-issues carries its own tracked `Context`, so the resulting
  `call_service` event is recognized and ignored before it can re-trigger
  any rule. No guard entity to configure.
- **Fully configured through the UI** — Config Flow (setup) + Options Flow
  (add/edit/delete rules, global settings). No YAML required.
- **Per-rule status sensor** — a diagnostic sensor (`ok` / `retrying` /
  `escalated` / `failed`) with the details of the last check.
- **Bilingual UI** — English and French.

## Installation

### Via HACS

1. HACS → Integrations → menu (⋮) → *Custom repositories*.
2. Add `https://github.com/cddu33/ha-action_control` as category
   *Integration*.
3. Install *Action Control*, then restart Home Assistant.

### Manual

Copy the `custom_components/action_control` folder into the
`custom_components` directory of your Home Assistant configuration, then
restart.

## Configuration

Settings → Devices & services → Add integration → *Action Control*. All
configuration (watchdog rules, escalation options, notifications) is then
done from the integration's *Configure* button — no YAML needed.

Each rule defines:

- **Targeting**: domain(s), service(s), `entity_id` pattern, name pattern,
  areas, labels, devices.
- **Verification**: delay before the first check, attributes to check with
  tolerance (e.g. `brightness`, `rgb_color`), retry count and delay
  between retries. The `light`, `switch`, and `cover` domains come with
  sensible defaults pre-filled.
- **Escalation** (optional): a recovery action (e.g. turning on a switch,
  running a script) triggered when verification keeps failing, with a
  minimum delay between two escalations.
- **Notifications**: persistent notification and/or a `notify.*` service
  of your choice.

## Usage example

- A rule on the `light` domain checks that the requested brightness and
  color were actually applied, with tolerance, and retries the command up
  to 2 times on failure.
- A rule on the `cover` domain, with an `cover.volet_*` pattern, waits for
  a cover to actually start moving; if it doesn't, it turns on a gateway
  restart switch and then replays the command.

## Troubleshooting

Enable debug logging:

```yaml
logger:
  logs:
    custom_components.action_control: debug
```

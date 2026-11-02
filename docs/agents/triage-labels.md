# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the strings used in the `Status:` line of local issue files under `.scratch/<feature>/issues/`.

| Role in mattpocock/skills | Value in our tracker | Meaning                                  |
| ------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`            | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`              | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`         | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`         | `ready-for-human`    | Requires human implementation            |
| `wontfix`                 | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), write the corresponding value into the `Status:` line.

## Category

The triage skill also uses two category roles, separate from the five state roles above. Record them as a `Category:` line near `Status:`:

```text
Status: needs-triage
Category: bug
```

Allowed values: `bug`, `enhancement`. These are not tracker labels; they are local metadata.

# 01 · Runtime Foundation

Status: ready-for-agent
Category: enhancement

## Problem Statement

The gateway cannot start safely until it has one validated configuration, one durable place to remember the mirror switch, and one consistent way to report startup failures. Without this foundation, every later module would parse configuration differently, unsafe placeholder tokens could reach production, and a corrupt state file could prevent the gateway from starting.

## Solution

Build the runtime foundation: a strictly validated TOML configuration loaded once at startup, an atomic state store for the mirror switch, consistent logging and exit-code conventions, and a process skeleton that later specs can extend. The foundation owns configuration and persistence; it does not talk to tmux, QQ, or NapCat.

## User Stories

1. As an operator, I want to start the gateway with only the three required secrets/identifiers configured, so that I can bring it up with minimal setup.
2. As an operator, I want optional settings to have documented defaults, so that I do not need to copy a large configuration file.
3. As an operator, I want all configuration errors reported in one startup attempt, so that I can fix them in one pass instead of restarting repeatedly.
4. As an operator, I want unknown configuration keys to produce warnings, so that typos do not silently do nothing.
5. As an operator, I want the gateway to reject empty or placeholder access tokens, so that I cannot accidentally deploy an unauthenticated service.
6. As an operator, I want access tokens masked in logs, so that secrets never reach journald or terminal history.
7. As an operator, I want a warning when the HTTP listener is configured on a non-loopback address, so that accidental exposure is visible.
8. As an operator, I want the mirror switch persisted across gateway restarts, so that I do not have to re-enable it after a deploy or crash.
9. As an operator, I want a missing state file treated as “mirror off” with an informational log, so that first startup is clean.
10. As an operator, I want a corrupt or type-invalid state file to fall back to “mirror off” with a warning, so that a bad file cannot block startup.
11. As an operator, I want state writes to be atomic and serialized, so that a crash or concurrent save cannot leave a half-written file.
12. As an operator, I want distinct exit codes for configuration errors, tmux/preflight failures, port conflicts, and thread death, so that systemd and scripts can react correctly.
13. As an implementer, I want one typed, immutable configuration object, so that later modules never parse TOML or apply their own defaults.
14. As an implementer, I want configuration lists represented immutably, so that no module can mutate shared configuration at runtime.
15. As an implementer, I want a consistent log format with module names, so that journald output is searchable.
16. As an implementer, I want a startup and shutdown skeleton with signal handling, so that later specs can add threads without re-inventing lifecycle code.
17. As a maintainer, I want the configuration schema and validation rules documented in one place, so that changes are reviewable.
18. As a maintainer, I want a clear rule that configuration is read only at startup, so that runtime edits do not create hidden behavior changes.

## Implementation Decisions

- The configuration module loads TOML once and returns a frozen, typed configuration object with sections for server, auth, OneBot, tmux, mirror, recovery, state, and logging.
- Required fields are the inbound token, the outbound token, and the main QQ number. All other fields have defaults defined by the product configuration contract.
- Validation is strict: booleans are not integers, strings are not integers, tokens must be non-empty and must not equal the placeholder value, paths must be absolute where required, and lists of regular expressions must compile.
- Validation collects all errors and raises one aggregated configuration error whose message lists every problem on its own line. Startup prints the full message and exits with code 2.
- Unknown keys and unknown sections produce warnings but do not block startup.
- List-valued configuration fields are converted to tuples so the configuration is deeply immutable.
- The state store owns only the mirror boolean. It uses a unique temporary file in the target directory, writes and flushes it, fsyncs it, then atomically replaces the target. A process-local lock serializes saves.
- State load falls back to `mirror=false` for a missing file with INFO, and for corrupt JSON, a non-object root, or a non-boolean mirror value with WARNING.
- State save failures are logged at ERROR and do not stop the gateway; the in-memory mirror state remains effective.
- Logging goes to stdout in a single format containing timestamp, level, logger name, and message. Token values are masked in configuration summaries. Message bodies, injected text, and kimico output are not logged by default.
- Exit codes are reserved: 0 normal shutdown, 2 configuration error, 3 kimico-command preflight or tmux self-check failure, 4 startup bind/port failure, 5 runtime thread death.
- The process skeleton parses the optional config path (defaulting to the product path), initializes logging, loads state, and installs SIGTERM/SIGINT handlers that set a stop event. Later specs add tmux and thread startup to this skeleton.
- Configuration is read once; runtime changes require a restart. The state file is the only runtime state that survives a restart.

## Testing Decisions

A good test asserts externally observable behavior: the returned configuration values, the aggregated error text, the exit code, the persisted file content, and the log level/contents. Tests must not assert private helper names or internal call order unless that order is a public contract.

**Seam:** the highest seam is the public configuration loader plus the public state store API, and the process entry point for exit-code behavior. Do not test TOML parsing helpers directly.

- Configuration tests use temporary TOML files and cover: minimal configuration, full configuration, missing required fields, wrong types, range violations, invalid regular expressions, placeholder tokens, unknown keys, trailing slashes, case-normalized log levels, and deep immutability.
- State tests use temporary directories and cover: missing file, valid file, corrupt file, wrong type, extra keys, round-trip save/load, parent-directory creation, no temporary-file residue, save failure without raising, and concurrent saves.
- Logging tests capture records and assert that token values are masked and that message bodies are absent.
- Startup tests run the process with a bad configuration and assert exit code 2 and a multi-line error message.
- There is no existing test suite; the prior art is the test tables in the product PRD.

## Out of Scope

- tmux session creation, pane detection, or recovery.
- HTTP ingress, OneBot protocol handling, QQ delivery.
- Output capture, mirroring, echo suppression, or batching.
- Deployment units, NapCat, or systemd hardening.

## Further Notes

- Spec 02 depends on this configuration contract; it must not add its own config parsing.
- Spec 05 owns the final deployment and exit-code integration.
- The configuration schema is a public contract. Adding a field requires updating the configuration spec and tests, not silently accepting an unknown key.

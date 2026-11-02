# 02: Complete configuration contract

**What to build:** An operator can configure every supported section and field, and invalid values are reported together in one startup attempt. Empty or placeholder tokens are rejected, tokens are masked in logs, unknown keys and non-loopback listeners produce warnings, and the resulting configuration object is deeply immutable.

**Blocked by:** 01 (Walking skeleton — minimal config startup).

**Status:** ready-for-agent

- [ ] Every supported section and field loads from TOML with documented defaults and overrides.
- [ ] Wrong types, out-of-range values, invalid regular expressions, and invalid paths are all collected into one exit-code-2 error message.
- [ ] Empty, whitespace-only, and placeholder access tokens are rejected.
- [ ] Access tokens are masked in every configuration log line.
- [ ] Unknown keys and unknown sections produce warnings without blocking startup.
- [ ] A non-loopback listener address produces a warning.
- [ ] List-valued fields and the configuration object are immutable at runtime.
- [ ] Trailing slashes on the OneBot base URL are normalized, and log-level casing is normalized.

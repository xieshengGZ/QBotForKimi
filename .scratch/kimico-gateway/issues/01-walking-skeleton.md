# 01: Walking skeleton — minimal config startup

**What to build:** An operator can start the gateway with only the three required settings, see one consistent log stream, receive all configuration errors in one exit, and stop it with SIGTERM/SIGINT without a traceback. This is the first end-to-end path from the command line through configuration and logging to process exit.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] Starting with a minimal configuration logs a startup line in the standard timestamp/level/logger/message format.
- [ ] SIGTERM and SIGINT both stop the process cleanly with exit code 0.
- [ ] A configuration missing any required field exits with code 2 and prints every missing field, one per line.
- [ ] A malformed TOML file exits with code 2 and a clear parse error.
- [ ] Editing the configuration file while the process runs does not change behavior until restart.

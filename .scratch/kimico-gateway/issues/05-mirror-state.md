# 05: Mirror state persistence

**What to build:** An operator’s mirror choice survives a gateway restart. A missing state file starts as mirror off; a corrupt or wrong-type file falls back to off with a warning; writes are atomic, serialized, and leave no temporary residue; concurrent saves cannot produce a half-written file.

**Blocked by:** 02 (Complete configuration contract).

**Status:** in-progress

- [ ] A missing state file loads as mirror off and logs at INFO.
- [ ] A valid true/false state file loads the matching mirror value.
- [ ] Corrupt JSON, a non-object root, or a non-boolean mirror value falls back to off with a WARNING.
- [ ] Extra keys in the state file are ignored.
- [ ] Saving creates parent directories when needed and round-trips through load.
- [ ] Saves use a unique temporary file, flush and fsync before an atomic replace, and leave no temporary residue.
- [ ] Two concurrent saves always leave a valid JSON file and no temporary residue.
- [ ] A save failure is logged at ERROR and does not raise or stop the process.

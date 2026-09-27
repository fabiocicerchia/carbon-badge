# TODO

Open items only. Completed work is dropped from here — the CHANGELOG is the
record of what shipped.

---

## Self-reported measurement (`record/`)

Nothing open.

## Assumptions

- [ ] **`arm` and `gpu` have no measured curve.** Both are extrapolations —
      `arm` from the x86 baseline, `gpu` from a T4 TDP plus host. Anyone running
      either seriously should declare `--runner-watts`, but better defaults
      would be worth having.

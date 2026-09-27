# TODO

Open items only. Completed work is dropped from here — the CHANGELOG is the
record of what shipped.

---

## Self-reported measurement (`record/`)

Nothing open.

## Assumptions

- [ ] **A flat wattage cannot be right.** Real draw swings 1.76–8.18 W with CPU
      load and the API exposes no utilisation, so the table uses the full-load
      figure and overstates I/O-bound jobs. See `docs/assumptions.md`.

- [ ] **`arm` and `gpu` have no measured curve.** Both are extrapolations —
      `arm` from the x86 baseline, `gpu` from a T4 TDP plus host. Anyone running
      either seriously should declare `--runner-watts`, but better defaults
      would be worth having.

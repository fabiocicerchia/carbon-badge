# TODO

Open items only. Completed work is dropped from here — the CHANGELOG is the
record of what shipped.

---

## Self-reported measurement (`record/`)

- [ ] **Reconcile the two paths on real data.** `docs/getting-started.md`
      prescribes comparing the default against `--ignore-self-reported`; it has
      never been run against a repo with meaningful coverage. With the
      termination paths now all confirmed to record, the setup-time gap above is
      the only bias left that the comparison should reveal — so it doubles as a
      check on that measurement.

## Assumptions

- [ ] **A flat wattage cannot be right.** Real draw swings 1.76–8.18 W with CPU
      load and the API exposes no utilisation, so the table uses the full-load
      figure and overstates I/O-bound jobs. See `docs/assumptions.md`.

- [ ] **`arm` and `gpu` have no measured curve.** Both are extrapolations —
      `arm` from the x86 baseline, `gpu` from a T4 TDP plus host. Anyone running
      either seriously should declare `--runner-watts`, but better defaults
      would be worth having.

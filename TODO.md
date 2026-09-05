# TODO

Open items only. Completed work is dropped from here — the CHANGELOG is the
record of what shipped.

---

## Self-reported measurement (`record/`)

- [ ] **The recorder undercounts by the job's setup time.** It starts its clock
      when its own step executes, which is *after* runner provisioning, "Set up
      job", and downloading the action itself. That time is real energy — the
      VM is booted and drawing power — and is invisible from inside the job.

      Measured on the first live trial
      ([cron-translate#26](https://github.com/fabiocicerchia/cron-translate/pull/26),
      run `31265537416`, 6 jobs):

| recorded | real    | gap           |
| -------- | ------- | ------------- |
| 4s       | 10s     | 6             |
| 7s       | 10s     | 3             |
| 8s       | 13s     | 5             |
| 9s       | 16s     | 7             |
| 10s      | 17s     | 7             |
| 15s      | 21s     | 6             |
| **53s**  | **87s** | **34s (39%)** |

      The gap is a near-constant ~5.7 s/job, so the error scales inversely with
      job length: ~39% on these 10-second jobs, ~1% on a ten-minute build. It
      pushes the self-reported figure *below* the API figure, the same
      direction as the cancelled-job gap below, so the two compound.

      Candidate fix: on GitHub-hosted runners the VM is fresh per job, so
      `/proc/uptime` read in the post step covers the whole job including
      setup — arguably more correct, since boot energy is genuinely spent.
      It is wrong for self-hosted runners, which are long-lived, so it would
      have to be conditional on the runner being hosted. That is a choice
      between two different errors, not an obvious win, which is why it is
      parked here rather than applied.

- [ ] **Reconcile the two paths on real data.** `docs/getting-started.md`
      prescribes comparing the default against `--ignore-self-reported`; it has
      never been run against a repo with meaningful coverage. With the
      termination paths now all confirmed to record, the setup-time gap above is
      the only bias left that the comparison should reveal — so it doubles as a
      check on that measurement.

## Assumptions

- [ ] **PUE may be double-counted.** The Eco-CI / Cloud Energy curves that
      `RUNNER_POWER_W` derives from are documented as machine draw, but whether
      they already include datacentre overhead is not stated anywhere I could
      find. If they do, every figure is ~15% high. Isolated in the `PUE`
      constant, so it is a one-line correction if confirmed.

- [ ] **A flat wattage cannot be right.** Real draw swings 1.76–8.18 W with CPU
      load and the API exposes no utilisation, so the table uses the full-load
      figure and overstates I/O-bound jobs. See `docs/assumptions.md`.

- [ ] **`arm` and `gpu` have no measured curve.** Both are extrapolations —
      `arm` from the x86 baseline, `gpu` from a T4 TDP plus host. Anyone running
      either seriously should declare `--runner-watts`, but better defaults
      would be worth having.

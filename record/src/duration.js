// How long the job actually drew power for.
//
// The obvious answer — stop the clock started by main.js — is short by
// everything that happened before this action's own step ran: runner
// provisioning, "Set up job", and downloading the action itself. That time is
// real energy, the VM is booted and drawing power for all of it, and it is
// invisible from inside the job. Measured at ~5.7 s/job, which is ~1% of a
// ten-minute build and ~39% of a ten-second one.
//
// On a GitHub-hosted runner the VM is fresh per job, so its uptime *is* the
// job's whole billable life and is the better figure. On a self-hosted runner
// the machine is long-lived and its uptime says nothing about this job — it
// could be weeks — so the wall clock stays.
//
// See docs/assumptions.md for why this counts boot time as the job's.

// MAX_SETUP_S bounds how much pre-step time is believable. Provisioning plus
// setup runs to seconds, not minutes; anything beyond this means uptime is not
// measuring what we think — a warm hosted image, a runner reused despite the
// environment label, a clock jump — and the wall clock is used instead. Better
// a known small undercount than an unbounded overcount.
export const MAX_SETUP_S = 600;

// isHosted reads the runner class from the environment GitHub sets on every
// runner: "github-hosted" or "self-hosted". Read from the passed env rather
// than process.env so it is testable, and treated as self-hosted when absent —
// the conservative direction, since that leaves the old behaviour in place.
export function isHosted(env = process.env) {
  return String(env.RUNNER_ENVIRONMENT || '').toLowerCase() === 'github-hosted';
}

// jobSeconds returns the duration to record.
//
//   startedMs   Date.now() as stamped by main.js
//   nowMs       Date.now() in the post step
//   uptimeS     os.uptime() in the post step
//   hosted      isHosted()
export function jobSeconds(startedMs, nowMs, uptimeS, hosted) {
  const elapsed = Math.max(0, Math.round((nowMs - Number(startedMs)) / 1000));
  if (!hosted) return elapsed;

  const uptime = Math.round(uptimeS);
  const setup = uptime - elapsed;
  // setup < 0 means the machine booted after the job started, which cannot
  // happen and marks the reading as untrustworthy.
  if (!Number.isFinite(uptime) || setup < 0 || setup > MAX_SETUP_S) return elapsed;
  return uptime;
}

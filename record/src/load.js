// The job's average CPU utilisation, which is what decides where on the
// 1.76-8.18 W curve it sat. The API exposes none, so this is the only place
// the figure can come from, and without it every job is priced at full load.
//
// Measured over the same interval duration.js records. On a GitHub-hosted
// runner that is the VM's whole life, so the counters since boot are the
// reading; elsewhere it is the stretch since main.js took its snapshot. Busy
// is user + nice + sys + irq; idle is the rest.

// cpuTimes sums os.cpus() into the two totals the ratio needs. Taken as an
// argument rather than read here so it is testable.
export function cpuTimes(cpus) {
  let busy = 0;
  let total = 0;
  for (const { times } of cpus || []) {
    const b = times.user + times.nice + times.sys + times.irq;
    busy += b;
    total += b + times.idle;
  }
  return { busy, total };
}

// cpuLoadPercent returns whole-percent utilisation over the interval, or null
// when it cannot be trusted — the reader then falls back to full load, which
// is the conservative direction.
//
//   start      cpuTimes() from main.js, or null if no snapshot was saved
//   now        cpuTimes() in the post step
//   sinceBoot  true when the recorded duration is the VM's uptime, so the
//              counters since boot are the reading and start is ignored
export function cpuLoadPercent(start, now, sinceBoot) {
  const from = sinceBoot ? { busy: 0, total: 0 } : start;
  if (!from || !now) return null;
  const busy = now.busy - from.busy;
  const total = now.total - from.total;
  // A counter that went backwards or did not move is not a reading.
  if (!(total > 0) || busy < 0 || busy > total) return null;
  return Math.round((100 * busy) / total);
}

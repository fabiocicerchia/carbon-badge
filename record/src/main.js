// Runs where the `uses:` line sits, at the top of the job. All it does is
// stamp the start time; the measurement happens in post.js, which the runner
// invokes automatically when the job ends.
const core = require('@actions/core');

core.saveState('carbonBadgeStart', String(Date.now()));

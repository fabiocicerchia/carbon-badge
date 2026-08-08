// Runs automatically when the job ends — including when the job is cancelled,
// which an `if: always()` step does not reliably cover. That is the main reason
// this is a JavaScript action rather than a second composite step.
//
// The measurement is encoded in the artifact *name*, because the artifacts API
// returns names in its listing, 100 per request. A month of data therefore
// costs one request and downloads nothing.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const core = require('@actions/core');
const { DefaultArtifactClient } = require('@actions/artifact');

function jobSlug() {
  const vars = process.env;
  const base = `${vars.GITHUB_JOB || 'job'}-${core.getInput('index') || '0'}`;
  // Artifact names may not contain " : < > | * ? \ / and must be unique within
  // a run. The dot is our field separator, so the slug must not contain one
  // either — anything outside [A-Za-z0-9_-] is replaced.
  return base.replace(/[^A-Za-z0-9_-]/g, '-').slice(0, 80);
}

async function run() {
  const started = core.getState('carbonBadgeStart');
  if (!started) {
    core.warning('carbon-badge: no start state recorded; skipping.');
    return;
  }

  const seconds = Math.max(0, Math.round((Date.now() - Number(started)) / 1000));
  const vcpu = os.cpus().length || 2;
  const memMb = Math.round(os.totalmem() / (1024 * 1024));
  const name = `carbon.v1.${seconds}.${vcpu}.${memMb}.${jobSlug()}`;

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'carbon-badge-'));
  const file = path.join(dir, 'carbon-badge.txt');
  fs.writeFileSync(file, 'the measurement is in the artifact name\n');

  try {
    await new DefaultArtifactClient().uploadArtifact(name, [file], dir, {
      retentionDays: Number(core.getInput('retention-days') || 45),
    });
    core.info(`carbon-badge: ${seconds}s on ${vcpu} vCPU / ${memMb} MB -> ${name}`);
  } catch (err) {
    // Telemetry must never fail somebody's build.
    core.warning(`carbon-badge: could not record this job (${err.message})`);
  }
}

run();

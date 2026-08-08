// Runs automatically when the job ends — including when the job is cancelled,
// which an `if: always()` step does not reliably cover. That is the main reason
// this is a JavaScript action rather than a second composite step.
//
// The measurement is encoded in the artifact *name*, because the artifacts API
// returns names in its listing, 100 per request. A month of data therefore
// costs one request and downloads nothing.
//
//   carbon.v1.<seconds>.<vcpu>.<memMB>.<platform>.<slug>
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const core = require('@actions/core');
const { DefaultArtifactClient } = require('@actions/artifact');

// CPU and memory alone do not determine draw: the same 4 vCPU / 16 GiB reading
// means a very different wattage on Apple silicon than on a shared x86 VM, so
// the reader has to be told which. These names match its power table.
function platform() {
  if (os.platform() === 'darwin') return 'macos';
  if (os.platform() === 'win32') return 'windows';
  return os.arch() === 'arm64' ? 'arm' : 'ubuntu';
}

// Artifact names must be unique within a run, and a matrix produces several
// jobs sharing one GITHUB_JOB. Requiring the caller to pass
// ${{ strategy.job-index }} turned a one-line action into a two-line one and
// silently dropped every matrix leg but the first when they forgot. A random
// suffix removes the question — nothing reads the slug, it only disambiguates.
//
// Artifact names may not contain " : < > | * ? \ / and the dot is our field
// separator, so anything outside [A-Za-z0-9_-] is replaced.
function slug() {
  const base = `${process.env.GITHUB_JOB || 'job'}`
    .replace(/[^A-Za-z0-9_-]/g, '-')
    .slice(0, 60);
  return `${base}-${crypto.randomBytes(4).toString('hex')}`;
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
  const name = `carbon.v1.${seconds}.${vcpu}.${memMb}.${platform()}.${slug()}`;

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'carbon-badge-'));
  const file = path.join(dir, 'carbon-badge.txt');
  fs.writeFileSync(file, 'the measurement is in the artifact name\n');

  try {
    await new DefaultArtifactClient().uploadArtifact(name, [file], dir, {
      retentionDays: Number(core.getInput('retention-days') || 45),
    });
    core.info(
      `carbon-badge: ${seconds}s on ${vcpu} vCPU / ${memMb} MB (${platform()})`,
    );
  } catch (err) {
    // Telemetry must never fail somebody's build.
    core.warning(`carbon-badge: could not record this job (${err.message})`);
  }
}

run();

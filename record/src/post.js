// Runs automatically when the job ends — including when the job is cancelled,
// which an `if: always()` step does not reliably cover. That is the main reason
// this is a JavaScript action rather than a second composite step.
//
// The measurement is encoded in the artifact *name*, because the artifacts API
// returns names in its listing, 100 per request. A month of data therefore
// costs one request and downloads nothing.
//
//   carbon.v1.<seconds>.<vcpu>.<memMB>.<platform>.<region>.<slug>
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
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

// Which Azure region this job landed in, from the Instance Metadata Service.
//
// GitHub allocates hosted runners wherever it likes and exposes no region, so
// this cannot be declared up front — the same repo lands in northcentralus one
// run and somewhere else the next. IMDS is the authoritative answer: a
// link-local address, no key, no third party, no rate limit, ~12 ms.
//
// Geolocating the egress IP was the alternative and is worse on every count —
// it reads a NAT gateway rather than the VM, HTTPS costs money on the free
// providers, and the limits are per-source-IP on addresses every GitHub user
// shares.
function azureRegion(timeoutMs = 1000) {
  return new Promise((resolve) => {
    const req = http.request(
      {
        host: '169.254.169.254',
        path: '/metadata/instance/compute?api-version=2021-02-01',
        headers: { Metadata: 'true' },
        timeout: timeoutMs,
      },
      (res) => {
        let body = '';
        res.on('data', (c) => (body += c));
        res.on('end', () => {
          try {
            const loc = JSON.parse(body).location;
            resolve(/^[a-z0-9-]+$/.test(loc || '') ? loc : null);
          } catch {
            resolve(null);
          }
        });
      },
    );
    // Never let a metadata probe hold up or fail somebody's job.
    req.on('error', () => resolve(null));
    req.on('timeout', () => {
      req.destroy();
      resolve(null);
    });
    req.end();
  });
}

// Artifact names must be unique within a run, and a matrix produces several
// jobs sharing one GITHUB_JOB. A random suffix removes the question — nothing
// reads the slug, it only disambiguates.
//
// Artifact names may not contain " : < > | * ? \ / and the dot is our field
// separator, so anything outside [A-Za-z0-9_-] is replaced.
function slug() {
  const base = `${process.env.GITHUB_JOB || 'job'}`
    .replace(/[^A-Za-z0-9_-]/g, '-')
    .slice(0, 60);
  return `${base}-${crypto.randomBytes(4).toString('hex')}`;
}

function sanitiseRegion(value) {
  const clean = String(value || '')
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, '')
    .slice(0, 40);
  return clean || 'unknown';
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

  // A declared region wins: self-hosted runners are not Azure VMs, so IMDS
  // either does not answer or answers about somebody else's infrastructure.
  const declared = core.getInput('region');
  const region = sanitiseRegion(declared || (await azureRegion()));

  const name = `carbon.v1.${seconds}.${vcpu}.${memMb}.${platform()}.${region}.${slug()}`;

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'carbon-badge-'));
  const file = path.join(dir, 'carbon-badge.txt');
  fs.writeFileSync(file, 'the measurement is in the artifact name\n');

  try {
    await new DefaultArtifactClient().uploadArtifact(name, [file], dir, {
      retentionDays: Number(core.getInput('retention-days') || 35),
    });
    core.info(
      `carbon-badge: ${seconds}s on ${vcpu} vCPU / ${memMb} MB ` +
        `(${platform()}, ${region})`,
    );
  } catch (err) {
    // Telemetry must never fail somebody's build.
    core.warning(`carbon-badge: could not record this job (${err.message})`);
  }
}

run();

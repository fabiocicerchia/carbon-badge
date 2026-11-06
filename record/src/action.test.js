// End to end: run the shipped dist/ bundles the way the runner does — main at
// the top of the job, post at the end — and check the artifact name post asks
// the backend to create. The backend is a local stand-in for the Actions
// results service that records CreateArtifact and refuses it, so nothing is
// uploaded and the job must still succeed.
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const run = promisify(execFile);
const dist = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "dist");

// core.saveState appends `name<<delim\nvalue\ndelim` blocks to GITHUB_STATE;
// the runner hands them to post as STATE_<name> variables.
function readState(file) {
  const state = {};
  const re = /^(\w+)<<(\S+)\n([\s\S]*?)\n\2$/gm;
  for (const [, name, , value] of fs.readFileSync(file, "utf8").matchAll(re)) {
    state[`STATE_${name}`] = value;
  }
  return state;
}

function fakeResults(requests) {
  return http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      requests.push({ url: req.url, body: JSON.parse(body || "{}") });
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ ok: false }));
    });
  });
}

test("main then post names the artifact after the job's measurement", async (t) => {
  const requests = [];
  const server = fakeResults(requests);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.close());

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "carbon-badge-e2e-"));
  t.after(() => fs.rmSync(tmp, { recursive: true, force: true }));
  const stateFile = path.join(tmp, "state");
  fs.writeFileSync(stateFile, "");

  const claims = Buffer.from(JSON.stringify({ scp: "Actions.Results:run-id:job-id" })).toString("base64url");
  const env = {
    PATH: process.env.PATH,
    GITHUB_STATE: stateFile,
    GITHUB_JOB: "build/linux",
    RUNNER_ENVIRONMENT: "self-hosted",
    INPUT_REGION: "West Europe",
    "INPUT_RETENTION-DAYS": "35",
    ACTIONS_RUNTIME_TOKEN: `e30.${claims}.sig`,
    ACTIONS_RESULTS_URL: `http://127.0.0.1:${server.address().port}/`,
  };

  await run(process.execPath, [path.join(dist, "main", "index.mjs")], { env });
  const state = readState(stateFile);
  assert.ok(Number(state.STATE_carbonBadgeStart) > 0);
  assert.ok(JSON.parse(state.STATE_carbonBadgeCpu).total > 0);

  // A refused upload is a warning, never a failed job.
  const { stdout } = await run(process.execPath, [path.join(dist, "post", "index.mjs")], {
    env: { ...env, ...state },
  });
  assert.match(stdout, /::warning::carbon-badge: could not record this job/);

  const create = requests.find((r) => r.url.endsWith("/CreateArtifact"));
  assert.ok(create, "post never asked the backend to create an artifact");
  assert.equal(create.body.workflow_run_backend_id, "run-id");
  assert.equal(create.body.workflow_job_run_backend_id, "job-id");
  // carbon.v2.<seconds>.<vcpu>.<memMB>.<platform>.<region>.<cpu%>.<slug>
  const m = create.body.name.match(
    /^carbon\.v[12]\.(\d+)\.(\d+)\.(\d+)\.(ubuntu|arm|macos|windows)\.westeurope\.(?:(\d+)\.)?build-linux-[0-9a-f]{8}$/,
  );
  assert.ok(m, `unexpected artifact name ${create.body.name}`);
  assert.equal(Number(m[2]), os.cpus().length);
  assert.equal(Number(m[3]), Math.round(os.totalmem() / (1024 * 1024)));
});

// A results service that accepts the artifact, plus the blob store its signed
// URL points at, so the real @actions/artifact client does the whole upload.
function acceptingResults(requests, blobs) {
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const body = Buffer.concat(chunks);
      if (req.method === "PUT") {
        // Staged blocks carry the zip; the closing comp=blocklist PUT is XML.
        if (/[?&]comp=block(&|$)/.test(req.url)) blobs.push(body);
        res.statusCode = 201;
        return res.end();
      }
      requests.push({ url: req.url, body: JSON.parse(body.toString() || "{}") });
      res.setHeader("Content-Type", "application/json");
      const { port } = server.address();
      res.end(
        JSON.stringify(
          req.url.endsWith("/CreateArtifact")
            ? { ok: true, signed_upload_url: `http://127.0.0.1:${port}/blob/artifact.zip?sig=x` }
            : { ok: true, artifact_id: "42" },
        ),
      );
    });
  });
  return server;
}

test("post uploads and finalises the artifact when the backend accepts it", async (t) => {
  const requests = [];
  const blobs = [];
  const server = acceptingResults(requests, blobs);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.close());

  const claims = Buffer.from(JSON.stringify({ scp: "Actions.Results:run-id:job-id" })).toString("base64url");
  const env = {
    PATH: process.env.PATH,
    GITHUB_JOB: "test",
    RUNNER_ENVIRONMENT: "self-hosted",
    INPUT_REGION: "northeurope",
    "INPUT_RETENTION-DAYS": "7",
    ACTIONS_RUNTIME_TOKEN: `e30.${claims}.sig`,
    ACTIONS_RESULTS_URL: `http://127.0.0.1:${server.address().port}/`,
    STATE_carbonBadgeStart: String(Date.now() - 5000),
  };

  const { stdout } = await run(process.execPath, [path.join(dist, "post", "index.mjs")], { env });
  assert.doesNotMatch(stdout, /::warning::/);
  assert.match(stdout, /carbon-badge: \d+s on \d+ vCPU \/ \d+ MB at unmeasured CPU \(\w+, northeurope, self-hosted/);

  const create = requests.find((r) => r.url.endsWith("/CreateArtifact"));
  const finalize = requests.find((r) => r.url.endsWith("/FinalizeArtifact"));
  assert.ok(finalize, "post never finalised the artifact");
  assert.equal(finalize.body.name, create.body.name);
  // No CPU snapshot was saved, so the name falls back to v1.
  assert.match(create.body.name, /^carbon\.v1\.\d+\.\d+\.\d+\.\w+\.northeurope\.test-[0-9a-f]{8}$/);
  assert.ok(create.body.expires_at, "retention-days was not passed on");

  // The uploaded bytes are the zip the finalise call reports.
  const zip = Buffer.concat(blobs);
  assert.equal(zip.subarray(0, 2).toString(), "PK");
  assert.equal(Number(finalize.body.size), zip.length);
});

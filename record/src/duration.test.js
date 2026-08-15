import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MAX_SETUP_S, isHosted, jobSeconds } from './duration.js';

const START = 1_700_000_000_000;
const s = (n) => START + n * 1000;

test('self-hosted records step time only', () => {
  // The machine has been up for eleven days; none of that is this job.
  assert.equal(jobSeconds(START, s(53), 950_400, false), 53);
});

test('hosted records the VM lifetime, which includes setup', () => {
  // The 39% gap from the issue: 53s of step time on a VM alive for 87s.
  assert.equal(jobSeconds(START, s(53), 87, true), 87);
});

test('hosted falls back when uptime is not measuring this job', () => {
  // A warm image or a mislabelled long-lived runner: an hour of uptime behind
  // a 53s job is not 53s of setup, so the wall clock is the safer figure.
  assert.equal(jobSeconds(START, s(53), 3600, true), 53);
  assert.equal(jobSeconds(START, s(53), 53 + MAX_SETUP_S + 1, true), 53);
  // Exactly at the bound is still believable.
  assert.equal(jobSeconds(START, s(53), 53 + MAX_SETUP_S, true), 53 + MAX_SETUP_S);
});

test('hosted falls back when uptime is below the elapsed time', () => {
  // Cannot happen physically, so it means the reading is wrong, not that the
  // job outlived its VM.
  assert.equal(jobSeconds(START, s(53), 10, true), 53);
});

test('a missing or garbage uptime never produces NaN', () => {
  assert.equal(jobSeconds(START, s(53), NaN, true), 53);
  assert.equal(jobSeconds(START, s(53), Infinity, true), 53);
});

test('a clock that goes backwards floors at zero', () => {
  assert.equal(jobSeconds(START, START - 5000, 900_000, false), 0);
});

test('the start state is read as a string, as saveState returns it', () => {
  assert.equal(jobSeconds(String(START), s(53), 950_400, false), 53);
});

test('runner class comes from RUNNER_ENVIRONMENT', () => {
  assert.equal(isHosted({ RUNNER_ENVIRONMENT: 'github-hosted' }), true);
  assert.equal(isHosted({ RUNNER_ENVIRONMENT: 'GitHub-Hosted' }), true);
  assert.equal(isHosted({ RUNNER_ENVIRONMENT: 'self-hosted' }), false);
  // Absent: assume self-hosted, which leaves the previous behaviour in place.
  assert.equal(isHosted({}), false);
});

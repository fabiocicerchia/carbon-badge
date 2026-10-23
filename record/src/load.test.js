import assert from "node:assert/strict";
import { test } from "node:test";
import { cpuLoadPercent, cpuTimes } from "./load.js";

const cpu = (user, idle) => ({ times: { user, nice: 0, sys: 0, irq: 0, idle } });

test("cpuTimes sums every core", () => {
  assert.deepEqual(cpuTimes([cpu(10, 30), cpu(20, 40)]), { busy: 30, total: 100 });
  assert.deepEqual(cpuTimes([]), { busy: 0, total: 0 });
});

test("a hosted runner reads the counters since boot", () => {
  // The snapshot is ignored: uptime is the recorded duration, so boot is the start.
  assert.equal(cpuLoadPercent({ busy: 90, total: 100 }, { busy: 25, total: 100 }, true), 25);
});

test("elsewhere it reads the interval since the snapshot", () => {
  assert.equal(cpuLoadPercent({ busy: 1000, total: 4000 }, { busy: 1100, total: 4400 }, false), 25);
});

test("no snapshot and no uptime reading is no figure", () => {
  assert.equal(cpuLoadPercent(null, { busy: 1, total: 2 }, false), null);
});

test("counters that did not move or went backwards are refused", () => {
  assert.equal(cpuLoadPercent({ busy: 5, total: 10 }, { busy: 5, total: 10 }, false), null);
  assert.equal(cpuLoadPercent({ busy: 9, total: 10 }, { busy: 5, total: 20 }, false), null);
});

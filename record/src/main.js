// Runs where the `uses:` line sits, at the top of the job. All it does is
// stamp the start time and the CPU counters; the measurement happens in
// post.js, which the runner invokes automatically when the job ends.
import os from "node:os";
import * as core from "@actions/core";
import { cpuTimes } from "./load.js";

core.saveState("carbonBadgeStart", String(Date.now()));
core.saveState("carbonBadgeCpu", JSON.stringify(cpuTimes(os.cpus())));

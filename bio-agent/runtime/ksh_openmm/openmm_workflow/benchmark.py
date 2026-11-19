"""Bounded performance observations, separate from trajectory science metrics."""
import math
import statistics
import time

from .common import WorkflowError, require


def measure(config, step_budget, timestep_fs, step, energy, canceled, clock=time.perf_counter, record_sample=None):
    report = {"schemaVersion": "ksh.benchmark.v1", "status": "incomplete", "samples": [],
              "totalIntegratedSteps": 0, "stepBudget": step_budget, "timestepFs": timestep_fs,
              "configuration": config, "scientificConvergenceAssessed": False,
              "timingScope": "Integration in <=1000-step chunks including Python budget checks and final energy read; excludes warmup, preparation and sample persistence.",
              "note": "A bounded adapted benchmark, not proof of original-paper timing equivalence. All calibration and measurement samples retained."}

    def advance(count):
        require(report["totalIntegratedSteps"] + count <= step_budget, "BENCHMARK_STEP_BUDGET_EXCEEDED")
        while count:
            require(not canceled(), "CANCELED")
            chunk = min(count, 1000)
            try:
                step(chunk)
            except Exception:
                report["integrationProgressUncertain"] = True
                raise
            report["totalIntegratedSteps"] += chunk
            count -= chunk

    def sample(phase, count):
        require(report["totalIntegratedSteps"] + config["warmupSteps"] + count <= step_budget,
                "BENCHMARK_STEP_BUDGET_EXCEEDED")
        advance(config["warmupSteps"])
        require(math.isfinite(energy()), "NONFINITE_ENERGY")
        start = clock()
        advance(count)
        require(math.isfinite(energy()), "NONFINITE_ENERGY")
        elapsed = clock() - start
        require(math.isfinite(elapsed) and elapsed > 0, "INVALID_BENCHMARK_CLOCK")
        require(not canceled(), "CANCELED")
        rate = count * timestep_fs * 1e-6 * 86400 / elapsed
        require(math.isfinite(rate), "INVALID_BENCHMARK_RATE")
        report["samples"].append({"phase": phase, "steps": count, "seconds": elapsed, "nsPerDay": rate})
        if record_sample is not None:
            record_sample(len(report["samples"]), {**report["samples"][-1],
                          "totalIntegratedSteps": report["totalIntegratedSteps"]})
        return elapsed

    try:
        count = config["initialSteps"]
        calibrated = False
        for index in range(config["maxCalibrationRounds"]):
            elapsed = sample("calibration", count)
            if elapsed >= config["targetSeconds"]:
                calibrated = True
                break
            if index + 1 == config["maxCalibrationRounds"]:
                break
            # Never expand beyond the approved remaining total budget.
            remaining = step_budget - report["totalIntegratedSteps"]
            estimate = count * config["targetSeconds"] / elapsed
            require(math.isfinite(estimate) and estimate <= remaining, "BENCHMARK_STEP_BUDGET_EXCEEDED")
            next_count = max(count + 1, math.ceil(estimate))
            require(next_count <= remaining, "BENCHMARK_STEP_BUDGET_EXCEEDED")
            count = next_count
        require(calibrated, "BENCHMARK_CALIBRATION_LIMIT")
        for _ in range(config["repetitions"]):
            sample("measurement", count)
        values = [r["nsPerDay"] for r in report["samples"] if r["phase"] == "measurement"]
        report.update(status="completed", meanNsPerDay=statistics.mean(values),
                      sampleStddevNsPerDay=statistics.stdev(values) if len(values) > 1 else None)
    except WorkflowError as error:
        report["reason"] = str(error)
    except Exception as error:
        # A scientific engine exception must not discard already completed samples
        # or fabricate a successful average. Do not publish arbitrary exception text.
        report["reason"] = "BENCHMARK_EXECUTION_FAILED"
        report["errorType"] = type(error).__name__
    return report

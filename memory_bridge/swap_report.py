#!/usr/bin/env python3
"""Aggregate swap-lab JSON reports into one Markdown table per scenario.

Groups runs by (pattern, disk latency, RAM/dataset/swap sizes, arm); fault
runs are listed separately. Medians are over completed runs; nothing is
extrapolated. Observed values only; interpretation belongs in the docs.
"""

import argparse
import json
from pathlib import Path
import statistics


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def fmt(value, scale=1.0, digits=1):
    return "–" if value is None else f"{value * scale:.{digits}f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    groups = {}
    faults = []
    for path in args.files:
        report = json.loads(path.read_text())
        p = report["parameters"]
        scenario = (p["pattern"], p["disk_latency_ms"], p["ram_mib"], p["dataset_mib"], p["swap_mib"], p["rounds"])
        for run in report["runs"]:
            if run.get("fault"):
                faults.append((path.name, run))
                continue
            groups.setdefault(scenario, {}).setdefault(run["arm"], []).append((path.name, run))
    for scenario, arms in sorted(groups.items()):
        pattern, latency, ram, dataset, swap, rounds = scenario
        print(f"\n### {pattern}, emulerad disklatens {latency} ms, RAM {ram} MiB, dataset {dataset} MiB, "
              f"swap {swap} MiB, {rounds} varv\n")
        print("| Alternativ | Körningar | Klara | OOM | Verifieringsfel | random-fas median s (min–max) "
              "| sidläsning median µs | p99 µs | max ms | swap-in/ut median | bridge-RSS samtidig MiB | filer |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for arm in ("noswap", "local-swap", "fuse-local", "bridge", "bridge-sync", "more-ram"):
            runs = arms.get(arm)
            if not runs:
                continue
            done = [r for _, r in runs if r.get("workload")]
            walls = [r["workload"]["phases"]["random"]["wall_seconds"] for r in done]
            reads = [r["workload"]["phases"]["random_reads"] for r in done]
            print(f"| {arm} | {len(runs)} | {len(done)} | {sum(1 for _, r in runs if r.get('oom_killed'))} "
                  f"| {sum(r['workload']['verification_errors'] for r in done)} "
                  f"| {fmt(median(walls))} ({fmt(min(walls, default=None))}–{fmt(max(walls, default=None))}) "
                  f"| {fmt(median(r['median_seconds'] for r in reads), 1e6, 0)} "
                  f"| {fmt(median(r['p99_seconds'] for r in reads), 1e6, 0)} "
                  f"| {fmt(median(r['max_seconds'] for r in reads), 1e3, 1)} "
                  f"| {fmt(median(r['swap_io']['pswpin'] for r in done), 1, 0)}/{fmt(median(r['swap_io']['pswpout'] for r in done), 1, 0)} "
                  f"| {fmt(median(r['memory']['bridge_peak_concurrent_rss_kib'] for r in done if r['memory']['bridge_peak_by_process_kib']), 1 / 1024, 0)} "
                  f"| {', '.join(sorted(set(name for name, _ in runs)))} |")
    if faults:
        print("\n### Injicerade givarfel (bridge)\n")
        print("| Fel | Injicerat efter s | Fallback efter s | Verifieringsfel | max sidläsning ms | remote_enabled efteråt | fil |")
        print("|---|---|---|---|---|---|---|")
        for name, run in faults:
            f = run["fault"]
            storage = ((run["after"]["export"] or {}).get("storage") or {})
            print(f"| {f['kind']} | {fmt(f['injected_after_seconds'])} | {fmt(f['fallback_observed_after_seconds'], 1, 2)} "
                  f"| {run['workload']['verification_errors'] if run.get('workload') else '–'} "
                  f"| {fmt(run['workload']['phases']['random_reads']['max_seconds'], 1e3) if run.get('workload') else '–'} "
                  f"| {storage.get('remote_enabled')} | {name} |")


if __name__ == "__main__":
    main()

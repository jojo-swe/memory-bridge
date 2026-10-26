#!/usr/bin/env python3
"""Summarise runs.jsonl against the pre-registered criteria (no re-running).

Gates and decisions are evaluated per output directory; when workloads were
run in separate directories, combine the gate results by hand (see
docs/SWAP-EXPERIMENT-RESULTAT.md). With fewer than 5 rounds the "4 of 5"
rule degrades to "all rounds", which is stricter, never looser.

Prints a Markdown report and writes summary.json next to runs.jsonl. The
decision rules are those of docs/SWAP-EXPERIMENT-KRITERIER.md; this script
reports observations and the rule outcomes separately so a reader can check
both. Nothing here filters runs: every record counts, including failures.
"""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics

MIB = 1024 * 1024


def load(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def median(values):
    return statistics.median(values) if values else None


def fmt(value, digits=1):
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def rng(values, digits=1):
    return f"{fmt(min(values), digits)}–{fmt(max(values), digits)}" if values else "–"


def per_run_metrics(record):
    cg = record.get("cgroup", {})
    vm = record.get("vmstat_delta", {})
    ps = record.get("pagestress") or {}
    lat = ps.get("latency_ms") or {}
    sampler = record.get("sampler") or {}
    cpu = record.get("cpu_s") or {}
    return {
        "status": record.get("status"), "elapsed_s": record.get("elapsed_s"),
        "oom_kills": cg.get("oom_kills", 0), "failcnt": cg.get("failcnt"),
        "pswpin": vm.get("pswpin"), "pswpout": vm.get("pswpout"),
        "requests": ps.get("requests_completed"), "populate_s": ps.get("populate_s"),
        "p50": lat.get("p50"), "p99": lat.get("p99"), "p999": lat.get("p999"),
        "max_ms": lat.get("max"), "verify_errors": ps.get("verify_errors"),
        "sweep_errors": ps.get("final_sweep_errors"),
        "xz_ok": record.get("xz_output_ok"),
        "bridge_rss_mib": (sampler.get("bridge_rss_kib") or 0) / 1024,
        "donor_rss_mib": (sampler.get("donor_rss_kib") or 0) / 1024,
        "cgroup_peak_mib": (cg.get("peak_bytes") or 0) / MIB,
        "bridge_cpu_s": sum(v for k, v in cpu.items() if k in ("export", "nbdfuse", "worker") and v),
        "donor_cpu_s": cpu.get("donor"),
        "remote_read_mib": ((record.get("export_stats") or {}).get("store", {}).get("remote_read_bytes") or 0) / MIB,
        "remote_enabled_end": (record.get("export_stats") or {}).get("store", {}).get("remote_enabled"),
        "remote_errors": (record.get("export_stats") or {}).get("store", {}).get("remote_errors"),
        "fault": record.get("fault_report"), "fault_window_max_ms": record.get("fault_window_max_latency_ms"),
        "errors": record.get("errors"),
    }


def paired(groups, workload, a, b, key):
    """Per-round pairs (a, b) for one metric; only rounds where both exist."""
    rounds = defaultdict(dict)
    for record in groups[workload]:
        if record["config"] in (a, b):
            rounds[record["round"]][record["config"]] = per_run_metrics(record).get(key)
    pairs = [(r[a], r[b]) for r in rounds.values() if r.get(a) is not None and r.get(b) is not None]
    return pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    records = load(args.out / "runs.jsonl")
    plan = json.loads((args.out / "plan.json").read_text())["args"]
    groups = defaultdict(list)
    for record in records:
        if record["phase"] == "matrix":
            groups[record["workload"]].append(record)
    lines = []
    summary = {"args": plan, "matrix": {}, "faults": [], "stress": [], "gates": {}, "decision": {}}

    lines.append(f"# Resultat: {args.out.name}\n")
    lines.append(f"Körningar: {len(records)} (matris {sum(len(v) for v in groups.values())}, "
                 f"fel {sum(r['phase'] == 'fault' for r in records)}, "
                 f"stress {sum(r['phase'] == 'stress' for r in records)}). "
                 f"RAM-gräns {plan['limit_mib']} MiB (Ref {plan['ref_limit_mib']}), swap {plan['swap_mib']} MiB.\n")

    for workload in ("w2", "w1"):
        if not groups[workload]:
            continue
        by_config = defaultdict(list)
        for record in groups[workload]:
            by_config[record["config"]].append(per_run_metrics(record))
        lines.append(f"\n## {workload.upper()} – {'pagestress' if workload == 'w2' else 'xz'}\n")
        if workload == "w2":
            lines.append("| Konfig | n | status | begäranden median (min–max) | p50 ms | p99 ms | p99.9 ms | max ms | "
                         "swap-in/ut sidor | OOM | datafel | adapter-RSS MiB | givar-RSS MiB | adapter-CPU s |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        else:
            lines.append("| Konfig | n | status | jobbtid s median (min–max) | swap-in/ut sidor | OOM | utdata korrekt | "
                         "adapter-RSS MiB | givar-RSS MiB | adapter-CPU s |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for config in ("N", "L", "P", "R", "R1", "Ref"):
            runs = by_config.get(config)
            if not runs:
                continue
            statuses = ",".join(sorted({r["status"] for r in runs}))
            ok = [r for r in runs if r["status"] == "ok"]
            swap = f"{fmt(median([r['pswpin'] for r in runs]), 0)}/{fmt(median([r['pswpout'] for r in runs]), 0)}"
            oom = sum(r["oom_kills"] or 0 for r in runs)
            rss = fmt(max(r["bridge_rss_mib"] for r in runs))
            drss = fmt(max(r["donor_rss_mib"] for r in runs))
            cpu = fmt(median([r["bridge_cpu_s"] for r in runs]))
            entry = {"n": len(runs), "statuses": statuses, "oom": oom}
            if workload == "w2":
                req = [r["requests"] for r in ok if r["requests"] is not None]
                errors = sum((r["verify_errors"] or 0) + (r["sweep_errors"] or 0) for r in runs if r["requests"] is not None)
                entry.update(requests_median=median(req), requests=req, p99_median=median([r["p99"] for r in ok]),
                             data_errors=errors)
                lines.append(f"| {config} | {len(runs)} | {statuses} | {fmt(median(req), 0)} ({rng(req, 0)}) | "
                             f"{fmt(median([r['p50'] for r in ok]), 3)} | {fmt(median([r['p99'] for r in ok]), 2)} | "
                             f"{fmt(median([r['p999'] for r in ok]), 1)} | {fmt(median([r['max_ms'] for r in ok]), 0)} | "
                             f"{swap} | {oom} | {errors} | {rss} | {drss} | {cpu} |")
            else:
                times = [r["elapsed_s"] for r in ok]
                correct = sum(1 for r in runs if r["xz_ok"])
                entry.update(time_median=median(times), times=times, correct=correct)
                lines.append(f"| {config} | {len(runs)} | {statuses} | {fmt(median(times))} ({rng(times)}) | {swap} | "
                             f"{oom} | {correct}/{len(runs)} | {rss} | {drss} | {cpu} |")
            summary["matrix"][f"{workload}-{config}"] = entry

    # Gates
    def all_failed(workload, config):
        runs = [r for r in groups[workload] if r["config"] == config]
        return bool(runs) and all(r["status"] != "ok" for r in runs)

    def all_ok(workload, config):
        runs = [r for r in groups[workload] if r["config"] == config]
        return bool(runs) and all(r["status"] == "ok" for r in runs)

    gates = {"N_fails_both": all_failed("w2", "N") and all_failed("w1", "N"),
             "Ref_ok_both": all_ok("w2", "Ref") and all_ok("w1", "Ref"),
             "L_ok_both": all_ok("w2", "L") and all_ok("w1", "L")}
    summary["gates"] = gates
    lines.append("\n## Validitetsgrindar\n")
    for key, value in gates.items():
        lines.append(f"- {key}: {'uppfylld' if value else 'EJ uppfylld'}")

    # Decision R vs L
    lines.append("\n## Beslutskriterier R mot L (förregistrerade)\n")
    decision = {}
    pairs_w1 = paired(groups, "w1", "L", "R", "elapsed_s")
    pairs_w2 = paired(groups, "w2", "L", "R", "requests")
    pairs_w2_p99 = paired(groups, "w2", "L", "R", "p99")
    if pairs_w1:
        med_l, med_r = median([a for a, _ in pairs_w1]), median([b for _, b in pairs_w1])
        wins = sum(1 for a, b in pairs_w1 if b < a)
        decision["w1"] = {"L_median_s": med_l, "R_median_s": med_r, "R_faster_pct": 100 * (1 - med_r / med_l),
                          "R_wins": wins, "pairs": len(pairs_w1), "criterion_met": med_r <= 0.9 * med_l and wins >= min(4, len(pairs_w1))}
        lines.append(f"- W1 jobbtid: L median {fmt(med_l)} s, R median {fmt(med_r)} s "
                     f"({fmt(decision['w1']['R_faster_pct'])} % snabbare för R; krav ≥ 10 %). "
                     f"R vann {wins}/{len(pairs_w1)} parade omgångar (krav ≥ 4/5). "
                     f"→ {'UPPFYLLT' if decision['w1']['criterion_met'] else 'ej uppfyllt'}")
    if pairs_w2:
        med_l, med_r = median([a for a, _ in pairs_w2]), median([b for _, b in pairs_w2])
        wins = sum(1 for a, b in pairs_w2 if b > a)
        p99_l, p99_r = median([a for a, _ in pairs_w2_p99]), median([b for _, b in pairs_w2_p99])
        decision["w2"] = {"L_median_requests": med_l, "R_median_requests": med_r,
                          "R_more_pct": 100 * (med_r / med_l - 1), "R_wins": wins, "pairs": len(pairs_w2),
                          "p99_L": p99_l, "p99_R": p99_r,
                          "criterion_met": med_r >= 1.1 * med_l and wins >= min(4, len(pairs_w2)) and p99_r <= 1.1 * p99_l}
        lines.append(f"- W2 begäranden: L median {fmt(med_l, 0)}, R median {fmt(med_r, 0)} "
                     f"({fmt(decision['w2']['R_more_pct'])} % fler för R; krav ≥ 10 %); p99 L {fmt(p99_l, 2)} ms, "
                     f"R {fmt(p99_r, 2)} ms (krav R ≤ 110 % av L). R vann {wins}/{len(pairs_w2)} parade omgångar. "
                     f"→ {'UPPFYLLT' if decision['w2']['criterion_met'] else 'ej uppfyllt'}")
    r_runs = [per_run_metrics(r) for r in records if r["phase"] == "matrix" and r["config"] == "R"]
    l_runs = [per_run_metrics(r) for r in records if r["phase"] == "matrix" and r["config"] == "L"]
    data_errors = sum((r["verify_errors"] or 0) + (r["sweep_errors"] or 0) for r in r_runs if r["requests"] is not None)
    data_errors += sum(1 for r in r_runs if r["xz_ok"] is False)
    failures_r = sum(1 for r in r_runs if r["status"] != "ok")
    failures_l = sum(1 for r in l_runs if r["status"] != "ok")
    rss_peak = max((r["bridge_rss_mib"] for r in r_runs), default=0)
    decision["no_data_errors_R"] = data_errors == 0
    decision["failures_R_vs_L"] = (failures_r, failures_l)
    decision["adapter_rss_peak_mib"] = rss_peak
    decision["adapter_rss_ok"] = rss_peak <= 64
    lines.append(f"- Datafel i R: {data_errors} (krav 0). Misslyckade jobb R/L: {failures_r}/{failures_l}.")
    lines.append(f"- Samtidig topp-RSS adapter+worker+nbdfuse i R: {fmt(rss_peak)} MiB (krav ≤ 64).")
    benefit = any(d.get("criterion_met") for d in (decision.get("w1", {}), decision.get("w2", {}))) \
        and decision["no_data_errors_R"] and failures_r <= failures_l and decision["adapter_rss_ok"]
    decision["benefit_shown"] = benefit
    lines.append(f"\n**Nytta enligt förregistrerade kriterier: {'VISAD' if benefit else 'EJ VISAD'}.**")
    summary["decision"] = decision

    faults = [r for r in records if r["phase"] == "fault"]
    if faults:
        lines.append("\n## Felinjektion (R, W2)\n")
        lines.append("| Körning | fel | injicerat efter s | status | begäranden | datafel | max svarstid ms (fönster) | "
                     "max svarstid ms (hela) | fjärr av vid slut | fjärrfel |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for record in faults:
            m = per_run_metrics(record)
            fr = m["fault"] or {}
            errors = (m["verify_errors"] or 0) + (m["sweep_errors"] or 0) if m["requests"] is not None else "–"
            lines.append(f"| {record['id']} | {record['fault']} | {fmt(fr.get('inject_after_marker_s'))} | {m['status']} | "
                         f"{fmt(m['requests'], 0)} | {errors} | {fmt(m['fault_window_max_ms'], 0)} | {fmt(m['max_ms'], 0)} | "
                         f"{'ja' if m['remote_enabled_end'] is False else 'nej' if m['remote_enabled_end'] else '–'} | "
                         f"{fmt(m['remote_errors'])} |")
            summary["faults"].append({"id": record["id"], "fault": record["fault"], "status": m["status"],
                                      "requests": m["requests"], "data_errors": errors,
                                      "window_max_ms": m["fault_window_max_ms"], "max_ms": m["max_ms"],
                                      "remote_enabled_end": m["remote_enabled_end"], "errors": m["errors"]})
    stress = [r for r in records if r["phase"] == "stress"]
    if stress:
        lines.append("\n## Stressvariant S (adapter i arbetslastens cgroup)\n")
        for record in stress:
            m = per_run_metrics(record)
            lines.append(f"- {record['id']}: status {m['status']}, {fmt(m['elapsed_s'])} s, begäranden {fmt(m['requests'], 0)}, "
                         f"OOM {m['oom_kills']}, datafel {(m['verify_errors'] or 0) + (m['sweep_errors'] or 0) if m['requests'] is not None else '–'}, "
                         f"max svarstid {fmt(m['max_ms'], 0)} ms, fel: {m['errors']}")
            summary["stress"].append({"id": record["id"], **{k: m[k] for k in ("status", "elapsed_s", "requests", "oom_kills", "max_ms", "errors")}})

    report = "\n".join(lines) + "\n"
    (args.out / "summary.md").write_text(report)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(report)


if __name__ == "__main__":
    main()

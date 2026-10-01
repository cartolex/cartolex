# SPDX-License-Identifier: MIT
"""Measure a collection of people « from institutions » on a very large invented institution.

Usage::

    python tools/large_collect_probe.py --works 100000 [--fault status:503:skip=40:times=9]
        [--select-check] [--keep DIR]

Starts the fake OpenAlex of :mod:`cartolex.demo.services.large` in a child
process (its memory is not counted), creates a project, and runs the
institutions action as the app runs it: :class:`~cartolex.app.collect_service.ServiceCollection`
through a :class:`~cartolex.app.jobs.LocalJobRunner`. It prints the job's end
state, its error or pause, the time, the peak memory of this process and the
rates per 10⁴ works, and the job log's last lines.

A fault is ``kind:status[:skip=N][:times=N][:retry_after=S]`` (kinds of
:class:`~cartolex.demo.services.http.FaultPlan`), applied to the works pages.
Run it through ``~/cartolex-work/heavy.sh`` from 10⁵ works up.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import resource
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _rss_mb() -> float:
    with open("/proc/self/status", encoding="ascii") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    return float("nan")


def _serve(works: int, faults: list[str], ready: mp.Queue, stop: mp.Event) -> None:
    from cartolex.demo.services.http import DemoServer
    from cartolex.demo.services.large import LargeInstitutionService

    server = DemoServer({"openalex": LargeInstitutionService(works)})
    server.start()
    for spec in faults:
        kind, status, *rest = spec.split(":")
        opts = dict(r.split("=", 1) for r in rest)
        server.faults.add(
            kind,  # type: ignore[arg-type]
            service="openalex",
            path=r"^works\?.*cursor=",
            status=int(status or 503),
            skip=int(opts.get("skip", 0)),
            times=int(opts["times"]) if "times" in opts else 1,
            retry_after=opts.get("retry_after"),
        )
    ready.put(server.base_url)
    stop.wait()
    server.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--works", type=int, default=10_000)
    parser.add_argument("--fault", action="append", default=[])
    parser.add_argument("--keep", type=Path, default=None)
    parser.add_argument("--min-works", type=int, default=2)
    args = parser.parse_args(argv)

    from cartolex.app.collect_service import ServiceCollection
    from cartolex.app.jobs import LocalJobRunner
    from cartolex.collect import RetryPolicy, local_settings
    from cartolex.demo.services.large import ROOT_ID
    from cartolex.project import Project

    ctx = mp.get_context("spawn")
    ready, stop = ctx.Queue(), ctx.Event()
    child = ctx.Process(target=_serve, args=(args.works, args.fault, ready, stop), daemon=True)
    child.start()
    base = ready.get(timeout=60)
    folder = args.keep or Path(tempfile.mkdtemp(prefix="large-collect-"))
    try:
        project = Project.init(folder / "p", name="Large", domain_title="Invented field")
        settings = local_settings(
            {"openalex": f"{base}/openalex"},
            retry=RetryPolicy(max_attempts=3, base_delay=0.01, max_delay=0.05),
        )
        collection = ServiceCollection(settings, local=True)
        runner = LocalJobRunner()
        before = _rss_mb()
        start = time.monotonic()
        info = runner.submit(
            project="large",
            jobs_dir=project.layout.jobs,
            kind="collection",
            work=lambda control: dict(
                collection.collect(
                    project,
                    control,
                    "institutions",
                    {"institutions": [ROOT_ID], "min_works": args.min_works},
                )
            ),
            title="collect: institutions",
            title_code="collect_institutions",
        )
        peak_seen = before
        while True:
            done = runner.wait(info.id, timeout=0.5)
            peak_seen = max(peak_seen, _rss_mb())
            if done is not None and done.state not in ("queued", "running", "cancelling"):
                break
        seconds = time.monotonic() - start
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        after = _rss_mb()
        per = args.works / 1e4
        print(f"works: {args.works}  state: {done.state}")
        print(f"error: {json.dumps(done.error)}")
        result = dict(done.result or {})
        result.pop("egress", None)
        print(f"result: {json.dumps(result)[:600]}")
        print(f"time: {seconds:.1f} s  ({seconds / per:.2f} s per 10^4 works)")
        print(
            f"memory: before {before:.0f} MB, peak {peak:.0f} MB, after {after:.0f} MB; "
            f"peak growth {(peak - before) / per:.1f} MB per 10^4 works"
        )
        log = sorted(project.layout.jobs.glob("*.jsonl"))[-1].read_text().splitlines()
        print("log (last lines):")
        for line in log[-4:]:
            print("  " + line[:300])
        project.close()
        runner.shutdown()
    finally:
        stop.set()
        child.join(timeout=10)
        if args.keep is None:
            shutil.rmtree(folder, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

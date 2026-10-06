# SPDX-License-Identifier: MIT
"""Make the screenshots of the user documentation, from the demo world, in the real app.

Usage::

    python tools/docs_screenshots.py                  # every picture, into docs/images/
    python tools/docs_screenshots.py --only map-      # the pictures whose name starts so
    python tools/docs_screenshots.py --work DIR --keep  # keep the projects it made

It starts the app (``cartolex app --services demo``: the demo services answer for
OpenAlex and the other bibliographic services, on this computer) with a home and an app
folder of its own, and drives it in headless Chromium, offline, in the light theme and in
English, as a person following the tutorials would: the demo project and its build, a
copilot's keyword triage (answered here from the demo world's truth, by cartolex's own
kit), the themes, the map, a shared site, then a project from an institution and one
from a list of names. Each picture is written as a palette PNG (``docs/images/<name>.png``),
its name stable, so that the pages keep pointing at it when it is made again.

The demo world is deterministic; the pictures differ from one run to the next only where
the app shows a time or a date. Needs the development extras (Playwright and its
Chromium) and the language models.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
#: Where the pictures go: the pages name them ``images/<name>.png``.
OUT = ROOT / "docs" / "images"
#: The window the pictures are taken in.
VIEWPORT = {"width": 1280, "height": 800}
#: Colours of a picture: a palette this large keeps the app's flat colours and its text.
COLOURS = 128
#: The folder the Projects screen shows as the home of new projects.
SHOWN_HOME = "/home/you/cartolex-projects"
PREFS_KEY = "cartolex.prefs/1"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}

#: The demo world of the app's demo project.
WORLD = ("S", 0)


def save_png(data: bytes, path: Path) -> int:
    """Write the screenshot *data* as a palette PNG at *path*; answers its size in bytes."""
    from PIL import Image

    image = Image.open(io.BytesIO(data)).convert("RGB")
    quantized = image.quantize(colors=COLOURS, method=Image.Quantize.MEDIANCUT, dither=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    quantized.save(path, optimize=True)
    return path.stat().st_size


class App:
    """The app on a home of its own, serving the demo services, until :meth:`stop`."""

    def __init__(self, work: Path) -> None:
        self.home = work / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "HOME": str(self.home), "PYTHONPATH": str(ROOT)}
        env.pop("MISTRAL_API_KEY", None)
        env.pop("OPENALEX_API_KEY", None)
        self.log = (work / "app.log").open("w", encoding="utf-8")
        code = "import sys; from cartolex.cli import main; sys.exit(main(sys.argv[1:]))"
        self.process = subprocess.Popen(
            [sys.executable, "-c", code, "app", "--no-browser", "--port", "0",
             "--data-dir", str(work / "app"), "--services", "demo",
             "--world", f"{WORLD[0]}:{WORLD[1]}"],
            cwd=work, env=env, stdout=subprocess.PIPE, stderr=self.log, text=True,
        )  # fmt: skip
        assert self.process.stdout is not None
        deadline = time.monotonic() + 120
        self.link = ""
        while time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if not line:
                break
            found = re.search(r"(http://127\.0\.0\.1:\d+)/launch\?token=\S+", line)
            if found:
                self.link, self.base = found.group(0), found.group(1)
                break
        if not self.link:
            self.stop()
            raise SystemExit(f"the app did not start: see {work / 'app.log'}")

    def stop(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.log.close()


class Shots:
    """The browser on the app, and the pictures it takes."""

    def __init__(self, app: App, out: Path, only: list[str], work: Path) -> None:
        from playwright.sync_api import sync_playwright

        self.app, self.out, self.only, self.work = app, out, only, work
        self.sizes: dict[str, int] = {}
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch()
        self.context = self.browser.new_context(
            viewport=VIEWPORT, color_scheme="light", device_scale_factor=1, accept_downloads=True
        )
        self.context.route("**/*", self._only_loopback)
        prefs = json.dumps({"theme": "light", "locale": "en", "dismissedJobs": []})
        self.context.add_init_script(
            f"try {{ localStorage.setItem({json.dumps(PREFS_KEY)}, {json.dumps(prefs)}); }} "
            "catch (e) {}"
        )
        self.page = self.context.new_page()
        self.page.set_default_timeout(60_000)
        self.page.goto(app.link)
        self.page.wait_for_load_state("networkidle")

    @staticmethod
    def _only_loopback(route) -> None:  # type: ignore[no-untyped-def]
        if urlsplit(route.request.url).hostname in LOOPBACK:
            route.continue_()
        else:
            route.abort()

    def close(self) -> None:
        self.context.close()
        self.browser.close()
        self._pw.stop()

    # -- moving around -------------------------------------------------------------------

    def go(self, path: str, settle: int = 1200) -> None:
        self.page.goto(self.app.base + path)
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(settle)

    def button(self, name: str | re.Pattern[str], *, exact: bool = False, within=None):  # type: ignore[no-untyped-def]
        return (within or self.page).get_by_role("button", name=name, exact=exact).first

    def menu(self, button: str, item: str | re.Pattern[str]) -> None:
        self.button(button, exact=True).click()
        self.page.get_by_role("menuitem", name=item).first.click()
        self.page.wait_for_timeout(600)

    def dialog(self):  # type: ignore[no-untyped-def]
        d = self.page.locator("dialog[open]").last
        d.wait_for()
        return d

    def wait_job(self, timeout: float = 600) -> None:
        """Wait until no job runs (the header's activity says so)."""
        deadline = time.monotonic() + timeout
        self.page.wait_for_timeout(1500)
        while time.monotonic() < deadline:
            jobs = self.page.evaluate("async () => (await (await fetch('/api/jobs')).json())")
            items = jobs.get("jobs", jobs) if isinstance(jobs, dict) else jobs
            if not any(j.get("state") in ("queued", "running") for j in items or []):
                return
            self.page.wait_for_timeout(1500)
        raise SystemExit("a job ran for too long")

    def consent_and_start(self, d) -> None:  # type: ignore[no-untyped-def]
        """Read what leaves the computer, agree, start."""
        notice = d.get_by_role("button", name="What leaves the computer")
        if notice.count():
            notice.first.click()
            self.page.wait_for_timeout(800)
        box = d.get_by_role("checkbox", name="I have read what leaves the computer")
        if box.count():
            box.first.check()
        d.get_by_role("button", name="Start").first.click()

    # -- pictures ------------------------------------------------------------------------

    def wanted(self, name: str) -> bool:
        return not self.only or any(name.startswith(prefix) for prefix in self.only)

    def shot(  # type: ignore[no-untyped-def]
        self, name: str, target=None, *, clip_height: int | None = None, max_height: int = 0
    ) -> None:
        """The picture *name*: the element *target* (its top *max_height* pixels), or the
        window (its top *clip_height* pixels)."""
        if not self.wanted(name):
            return
        self.page.mouse.move(0, 0)
        self.page.wait_for_timeout(400)
        if target is not None and max_height:
            target.evaluate("e => e.scrollIntoView({block: 'start'})")
            self.page.wait_for_timeout(400)
            box = target.bounding_box()
            clip = {"x": box["x"], "y": box["y"], "width": box["width"],
                    "height": min(box["height"], max_height, VIEWPORT["height"] - box["y"])}  # fmt: skip
            data = self.page.screenshot(clip=clip, animations="disabled")
        elif target is not None:
            data = target.screenshot(animations="disabled")
        elif clip_height:
            clip = {"x": 0, "y": 0, "width": VIEWPORT["width"], "height": clip_height}
            data = self.page.screenshot(clip=clip, animations="disabled")
        else:
            data = self.page.screenshot(animations="disabled")
        self.sizes[name] = save_png(data, self.out / f"{name}.png")
        print(f"  {name}.png  {self.sizes[name] / 1000:.0f} kB", flush=True)


# -- the scenes, in the order of the tutorials ----------------------------------------------


def first_map(s: Shots) -> None:
    """The Projects screen, the demo project's overview, the Build screen, the map."""
    page = s.page

    def shown_home(route) -> None:  # type: ignore[no-untyped-def]
        reply = route.fetch()
        body = reply.json()
        if body.get("folder"):
            body["folder"] = SHOWN_HOME
        route.fulfill(response=reply, json=body)

    page.route("**/api/projects/defaults", shown_home)
    s.go("/start")
    s.shot("first-map-start", clip_height=470)
    page.unroute("**/api/projects/defaults")
    s.go("/start")
    s.button("Create the demo project").click()
    page.wait_for_url(re.compile(r"/overview"))
    s.go("/overview", settle=2000)
    s.shot("first-map-overview", clip_height=760)
    s.button("Build…", exact=True).click()
    page.wait_for_url(re.compile(r"/build"))
    page.wait_for_timeout(2000)
    s.shot("first-map-build")
    s.button(re.compile(r"^Build \d+ stages")).click()
    s.wait_job()
    s.go("/map", settle=3000)
    s.shot("first-map-map")


def _truth() -> list[dict]:
    from cartolex.demo.writers import lexicon_truth

    return lexicon_truth(["en", "fr"])


def _judge(term: str, lang: str, truth: list[dict]) -> dict:
    """What the demo world's truth says of a candidate: a field term (with its English
    name when it is one whole), a phrase of the sentence templates, or neither."""
    padded = f" {term.lower()} "
    whole = [r for r in truth if r["lang"] == lang and r["text"].lower() == term.lower()]
    if whole:
        return whole[0]
    inside = [r for r in truth if r["lang"] == lang and padded in f" {r['text'].lower()} "]
    if any(r["kind"] == "template" for r in inside):
        return {"field": False, "kind": "template"}
    field = [r for r in inside if r["field"]]
    if field and len(term.split()) > 1:
        return {**field[0], "canonical": term}
    return {"field": False, "kind": "other"}


def answer_triage(folder: Path) -> Path:
    """Answer a triage bundle as an attentive assistant would, from the demo world's truth:
    every group read, the field's terms kept (a French term merged into its English name),
    the rest excluded. Answers the result file."""
    from cartolex.copilot import open_bundle

    session = open_bundle(folder)
    while session.next_batch(50).strip():
        if not any(i not in session.shown for i in range(len(session.items))):
            break
    truth = _truth()
    rows = []
    for item in session.items:
        term, lang = item["term"], item["lang"]
        known = _judge(term, lang, truth)
        if known["field"]:
            code = "M" if known["kind"] == "method" or known.get("technique") else "C"
            if lang != "en" and known["canonical"].lower() != term.lower():
                rows.append({"term": term, "lang": lang, "decision": "merge", "code": code,
                             "target": known["canonical"], "reason": "the same term in English"})  # fmt: skip
            else:
                rows.append({"term": term, "lang": lang, "decision": "keep", "code": code,
                             "reason": "a term of the field"})  # fmt: skip
        elif known["kind"] in ("driver", "setting"):
            rows.append({"term": term, "lang": lang, "decision": "exclude", "code": "H",
                         "reason": "a setting or a driver: not a subject of its own here"})  # fmt: skip
        else:
            rows.append({"term": term, "lang": lang, "decision": "exclude", "code": "G",
                         "reason": "generic phrasing of academic writing"})  # fmt: skip
    session.decide_many(rows, unread_ok=True)
    return session.write_result(
        "The field's terms are kept, French ones merged into their English names; "
        "generic phrasing is excluded.",
        curator_agreed=True,
    )


def keywords(s: Shots) -> None:
    """The Keywords screen, a copilot's triage exported, answered, reviewed and accepted, the
    build again, the Lexicon tab."""
    page = s.page
    s.go("/keywords?band=check", settle=2000)
    s.shot("keywords-list")
    s.menu("Triage with AI", re.compile("copilot"))
    d = s.dialog()
    link = d.locator("a[download]").first
    link.wait_for()
    s.shot("keywords-copilot", d)
    with page.expect_download() as download:
        link.click()
    folder = s.work / "triage-bundle"
    shutil.rmtree(folder, ignore_errors=True)
    zipfile.ZipFile(download.value.path()).extractall(folder)
    result = answer_triage(folder)
    d.get_by_role("button", name="I have the result").click()
    d.locator("input[type=file]").set_input_files(str(result))
    page.wait_for_timeout(2500)
    s.shot("keywords-review", d)
    d.get_by_role("button", name=re.compile(r"^Accept \d")).click()
    page.wait_for_timeout(1500)
    s.go("/build", settle=2000)
    s.button(re.compile(r"^Build \d+ stages")).click()
    s.wait_job()
    s.go("/keywords?band=lexicon", settle=3000)
    tab = page.get_by_role("tab", name=re.compile("^Lexicon"))
    if tab.count():
        tab.first.click()
        page.wait_for_timeout(3000)
    s.shot("keywords-lexicon")


def themes(s: Shots) -> None:
    """The theme editor with a theme open in its panel."""
    page = s.page
    s.go("/themes", settle=3000)
    page.get_by_role("treeitem").first.click()
    page.wait_for_timeout(1500)
    s.shot("themes-editor")


def map_screen(s: Shots) -> None:
    """The map with a person selected, and the comparison of two people."""
    page = s.page
    s.go("/map", settle=3000)
    find = page.get_by_role("combobox", name="Find")
    find.fill("Ioana")
    page.wait_for_timeout(800)
    page.keyboard.press("Enter")  # the first match
    page.locator(".cx-atlas-card h4", has_text="Co-authors").wait_for()
    page.wait_for_timeout(1500)
    s.shot("map-person")


def share(s: Shots) -> None:
    """The Share screen with its questions answered, then a site built."""
    page = s.page
    page.set_viewport_size({"width": VIEWPORT["width"], "height": 1300})
    s.go("/share", settle=2000)
    page.get_by_role("radio", name=re.compile("^Pseudonyms")).first.check()
    page.wait_for_timeout(1500)
    s.shot("share-site")
    s.button("Build the site", exact=True).click()
    s.wait_job()
    s.go("/share", settle=2000)
    builds = page.locator("section, .cx-card").filter(has_text="Site builds").last
    s.shot("share-built", builds)
    page.set_viewport_size(VIEWPORT)


def institution(s: Shots) -> None:
    """A project from an institution: found, its people read and proposed, then taken."""
    page = s.page
    _new_project(s, "Institutions", "A coastal institute", "institute")
    page.get_by_role("textbox", name="Name of an institution").fill("Pontrevel")
    s.button("Find", exact=True).click()
    page.wait_for_timeout(2000)
    page.get_by_role("checkbox", name=re.compile("Pontrevel Institute")).first.check()
    s.button(re.compile("^Read their people")).click()
    s.dialog().get_by_role("button", name="Continue").click()
    page.wait_for_timeout(800)
    if page.locator("dialog[open]").count():
        s.consent_and_start(s.dialog())
    s.wait_job()
    s.go("/people?tab=organisations", settle=2000)
    box = page.locator("section, .cx-card").filter(has_text="People of institutions").last
    box.scroll_into_view_if_needed()
    s.shot("institution-proposal", box, max_height=520)
    s.button(re.compile("^Take everyone")).click()
    s.shot("institution-take", s.dialog())
    s.dialog().get_by_role("button", name=re.compile("^Take all")).click()
    page.wait_for_timeout(1500)


def names(s: Shots) -> None:
    """A project from a list of names: imported, a duplicate compared, identities checked."""
    page = s.page
    people = _people_list(s.work / "people.csv")
    _new_project(s, None, "Two coastal teams", "teams")
    if not page.locator("dialog[open]").count():  # the project's start opens it
        s.menu("Import", "A list of people")
    d = s.dialog()
    d.locator("input[type=file]").set_input_files(str(people))
    d.get_by_role("button", name="Read the list").click()
    page.wait_for_timeout(1200)
    s.shot("names-import", d)
    d.get_by_role("button", name=re.compile(r"^Import \d+ rows")).click()
    page.wait_for_timeout(1500)
    s.go("/people?tab=duplicates", settle=1500)
    page.get_by_role("row", name=re.compile("Stordomaro")).first.click()
    page.wait_for_timeout(1000)
    s.shot("names-duplicates")
    s.button(re.compile("^One person, keep")).click()
    page.wait_for_timeout(1000)
    s.menu("Collect", "Find identities")
    s.consent_and_start(s.dialog())
    s.wait_job()
    s.go("/people?tab=identities", settle=1500)
    page.get_by_role("row", name=re.compile("Paola Mabaley")).first.click()
    page.wait_for_timeout(1200)
    s.shot("names-identities")


def _new_project(s: Shots, start: str | None, name: str, folder: str) -> None:
    page = s.page
    s.go("/start")
    s.button("Create a project").click()
    page.wait_for_timeout(600)
    if start:
        page.get_by_role("radio", name=re.compile(f"^{start}")).check()
    page.get_by_role("textbox", name="Name").fill(name)
    page.get_by_role("textbox", name=re.compile("title")).fill("Coastal and marine systems")
    page.get_by_role("checkbox", name="French (fr)").check()
    page.get_by_role("textbox", name="Folder").fill(str(s.app.home / "cartolex-projects" / folder))
    s.button("Create the project").click()
    page.wait_for_url(re.compile(r"/people"))
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1000)


def _people_list(path: Path) -> Path:
    """A list of names of the demo world (two of its groups), with one person twice."""
    from cartolex.demo import generate

    world = generate(*WORLD)
    acronym = {g.group_id: g.acronym for g in world.groups}
    cohort = [p for p in world.people if p.role == "cohort"][:14]
    with path.open("w", newline="", encoding="utf-8") as f:
        out = csv.writer(f)
        out.writerow(["Name", "Lab", "Career stage"])
        for p in cohort:
            out.writerow([f"{p.last_name}, {p.first_name}", acronym[p.group], p.career_stage])
        twice = cohort[1]
        out.writerow([f"{twice.last_name}, {twice.first_name[0]}.", acronym[twice.group],
                      twice.career_stage])  # fmt: skip
    return path


#: The scenes, in order: each starts from where the one before left the app.
SCENES: list[tuple[str, Callable[[Shots], None]]] = [
    ("first-map", first_map),
    ("keywords", keywords),
    ("themes", themes),
    ("map", map_screen),
    ("share", share),
    ("institution", institution),
    ("names", names),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=OUT, help="where the pictures go")
    parser.add_argument("--only", nargs="*", default=[], help="pictures whose name starts so")
    parser.add_argument("--work", type=Path, help="a folder for the app and its projects")
    parser.add_argument("--keep", action="store_true", help="keep the work folder")
    args = parser.parse_args(argv)
    work = args.work or Path(tempfile.mkdtemp(prefix="cartolex-docs-shots-"))
    work.mkdir(parents=True, exist_ok=True)
    app = App(work)
    shots = None
    try:
        shots = Shots(app, args.out, args.only, work)
        for name, scene in SCENES:
            print(f"{name}…", flush=True)
            scene(shots)
    finally:
        if shots is not None:
            shots.close()
        app.stop()
        if not args.keep and args.work is None:
            shutil.rmtree(work, ignore_errors=True)
    total = sum(shots.sizes.values()) if shots else 0
    print(f"{len(shots.sizes) if shots else 0} pictures, {total / 1e6:.2f} MB in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

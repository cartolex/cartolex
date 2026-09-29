# SPDX-License-Identifier: MIT
"""The theme editor on the real app, on the S demo world at depth 2 (L for the budget).

The main flows only, each from a fresh copy of the built project: search and
rename, undo and redo, move by the menu, merge, split, set aside and put back,
a draft restored after a reload, a save refused (412) then reloaded and
merged, apply in the background, and the AI handoff imported and accepted.
Then a keyboard script of the menu actions, axe in one theme, and one budget
on the L world: page ready and search.
"""

from __future__ import annotations

import os
import re

import pytest
from browser_harness import write_measures
from test_accessibility import blocking, run_axe

MEASURES = os.environ.get("CARTOLEX_UI_MEASURES")

API = """async ([method, path, body, ifMatch]) => {
  const manifest = await (await fetch('/api/app/manifest')).json();
  const sec = manifest.security || {};
  const cookie = document.cookie.split(';').map((c) => c.trim().split('='))
    .find(([k]) => k === (sec.csrf_cookie || 'cartolex_csrf'));
  const headers = { 'Content-Type': 'application/json', Accept: 'application/json' };
  headers[sec.csrf_header || 'X-Cartolex-CSRF'] = sec.csrf_token || (cookie ? decodeURIComponent(cookie[1]) : '');
  if (ifMatch) headers['If-Match'] = ifMatch;
  const r = await fetch(path, { method, headers, body: body === null ? undefined : JSON.stringify(body) });
  return { status: r.status, etag: r.headers.get('ETag'), data: await r.json().catch(() => null) };
}"""


def api(ui, method: str, path: str, body=None, if_match: str | None = None) -> dict:
    """A call to the app from the page (its session and CSRF token)."""
    return ui.page.evaluate(API, [method, path, body, if_match])


def open_editor(ui) -> None:
    """Go to the theme editor and wait until it shows the tree."""
    ui.navigate("/themes")
    ui.page.locator(".cx-themes__body").wait_for()


def tree(ui) -> dict:
    return api(ui, "GET", "/api/themes")["data"]["tree"]


def outline(ui):
    return ui.page.locator(".cx-themes-outline [role=tree]")


def status(ui) -> str:
    return ui.page.locator(".cx-themes__status").inner_text()


def undo_label(ui) -> str:
    return ui.page.locator(".cx-themes__undo").get_attribute("aria-label") or ""


def wait_status(ui, pattern: str, timeout: float = 5000) -> None:
    """Wait until the status says *pattern* and no dialog is open (the change is done)."""
    ui.page.wait_for_function(
        "(p) => new RegExp(p).test(document.querySelector('.cx-themes__status').textContent)"
        " && !document.querySelector('dialog[open]')",
        arg=pattern,
        timeout=timeout,
    )


def name_of(node: dict) -> str:
    return node["names"].get("en") or next(iter(node["names"].values()), node["id"])


def row(ui, text: str):
    """The outline row that shows *text* (it must be in view)."""
    return outline(ui).locator("[role=treeitem]", has_text=re.compile(rf"^{re.escape(text)}"))


def menu_item(ui, name: str):
    return ui.page.get_by_role("menu").get_by_role("menuitem", name=name, exact=True)


def dialog(ui):
    return ui.page.locator("dialog[open]")


def pick(ui, text: str) -> None:
    """In an open « pick a node » dialog: filter by *text*, take the first match."""
    d = dialog(ui)
    d.get_by_role("combobox").fill(text)
    d.get_by_role("combobox").press("Enter")
    d.wait_for(state="detached")


@pytest.fixture()
def editor(demo_s, app_for, open_app):
    """The editor on a fresh copy of the S demo world."""
    server = app_for(demo_s)
    ui = open_app(server)
    open_editor(ui)
    ui.server = server
    return ui


# ── scenarios ────────────────────────────────────────────────────────────────


def test_open_search_rename_undo_and_redo(editor):
    ui = editor
    page = ui.page
    t = tree(ui)
    assert "The grouping's proposal" in status(ui)
    # search: the matches are marked and their paths opened
    keyword = sorted(t["keywords"])[7]
    word = keyword.split()[0]
    page.keyboard.press("/")
    assert page.evaluate("() => document.activeElement.id") == "cx-themes-search"
    page.keyboard.type(word)
    page.locator(".cx-themes-search__status").get_by_text(re.compile(r"keyword")).wait_for()
    assert outline(ui).locator("mark").count() >= 1
    page.keyboard.press("Enter")
    active = page.evaluate(
        "() => document.getElementById(document.activeElement.getAttribute('aria-activedescendant')).textContent"
    )
    assert word.lower() in active.lower()
    page.locator("#cx-themes-search").fill("")
    # rename the first theme with F2
    first = next(n for n in t["nodes"] if n["parent"] is None)
    outline(ui).focus()
    page.keyboard.press("Home")
    page.keyboard.press("F2")
    d = dialog(ui)
    d.get_by_label("Name in English").fill("Climate archives")
    d.get_by_label("Name in English").press("Enter")
    d.wait_for(state="detached")
    wait_status(ui, "1 unsaved change")
    assert row(ui, "Climate archives").count() == 1
    assert undo_label(ui) == f"Undo: Rename “{name_of(first)}” to “Climate archives”"
    page.keyboard.press("Control+z")
    wait_status(ui, "proposal")
    assert row(ui, name_of(first)).count() >= 1
    page.keyboard.press("Control+Shift+z")
    wait_status(ui, "1 unsaved change")
    assert row(ui, "Climate archives").count() == 1


def test_move_by_the_menu(editor):
    ui = editor
    page = ui.page
    t = tree(ui)
    tops = [n for n in t["nodes"] if n["parent"] is None]
    topic = next(n for n in t["nodes"] if n["parent"] == tops[0]["id"])
    kws = sorted(k for k, n in t["keywords"].items() if n == topic["id"])
    # open the topic, choose its first keyword, move it by the menu
    row(ui, name_of(topic)).first.click()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Shift+F10")
    menu_item(ui, "Move to…").click()
    pick(ui, name_of(tops[1]))
    wait_status(ui, "1 unsaved change")
    assert undo_label(ui).startswith("Undo: Move 1 keyword to")
    moved = [k for k in kws if tree_after(ui).get(k) != topic["id"]]
    assert len(moved) == 1


def tree_after(ui) -> dict:
    """The keywords of the tree being edited (from the draft the editor keeps)."""
    draft = ui.page.evaluate(
        "() => { for (const k of Object.keys(localStorage)) if (k.startsWith('cartolex.themes-draft/1:'))"
        " return JSON.parse(localStorage.getItem(k)); return null; }"
    )
    return draft["tree"]["keywords"]


def test_merge_split_set_aside_and_put_back(editor):
    ui = editor
    page = ui.page
    t = tree(ui)
    tops = [n for n in t["nodes"] if n["parent"] is None]
    # merge the second theme into the third
    row(ui, name_of(tops[1])).first.click()
    page.keyboard.press("Shift+F10")
    menu_item(ui, "Merge with…").click()
    pick(ui, name_of(tops[2]))
    wait_status(ui, "1 unsaved change")
    assert undo_label(ui) == f"Undo: Merge “{name_of(tops[1])}” into “{name_of(tops[2])}”"
    # split a topic: two keywords go to a new topic beside it
    topic = next(n for n in t["nodes"] if n["parent"] == tops[0]["id"])
    row(ui, name_of(topic)).first.click()
    page.keyboard.press("Shift+F10")
    menu_item(ui, "Split…").click()
    d = dialog(ui)
    boxes = d.locator("input[type=checkbox]")
    boxes.nth(0).check()
    boxes.nth(1).check()
    d.get_by_label("Name in English").fill("A finer topic")
    d.get_by_role("button", name="Split 2 off").click()
    d.wait_for(state="detached")
    wait_status(ui, "2 unsaved changes")
    assert row(ui, "A finer topic").count() == 1
    # set a keyword aside with Delete, then put it back from the tray
    row(ui, "A finer topic").first.click()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowDown")
    term = page.evaluate(
        "() => document.getElementById(document.activeElement.getAttribute('aria-activedescendant'))"
        ".querySelector('.cx-themes-row__term').textContent"
    )
    page.keyboard.press("Delete")
    d = dialog(ui)
    d.get_by_label("a broken or partial phrase").check()
    d.locator("button[type=submit]").click()
    d.wait_for(state="detached")
    wait_status(ui, "3 unsaved changes")
    page.get_by_role("tab", name=re.compile("^Set aside")).click()
    tray = page.get_by_role("tree", name="Keywords set aside")
    tray.locator("[role=treeitem]", has_text=term).click(button="right")
    menu_item(ui, "Put back").click()
    wait_status(ui, "4 unsaved changes")
    assert tree_after(ui)[term] is not None
    assert undo_label(ui) == "Undo: Put back 1 keyword"


#: The draft the editor keeps (written a moment after each change), or null.
DRAFT = (
    "(() => { for (const k of Object.keys(localStorage)) if (k.startsWith('cartolex.themes-draft/1:'))"
    " return JSON.parse(localStorage.getItem(k)); return null; })()"
)


def wait_placed(ui, keyword: str, node: str) -> None:
    """Wait until the draft places *keyword* on *node*."""
    ui.page.wait_for_function(
        f"([k, n]) => ({DRAFT})?.tree.keywords[k] === n", arg=[keyword, node], timeout=5000
    )


def test_borderline_keywords_and_suggested_places_by_the_keyboard(editor):
    ui = editor
    page = ui.page
    page.get_by_role("tab", name=re.compile("^Borderline")).click()
    border = page.get_by_role("tree", name="Borderline keywords")
    border.locator("[role=treeitem]").first.wait_for()
    t = tree(ui)
    listed = api(ui, "POST", "/api/themes/borderline", {"tree": t, "limit": 3})["data"]["items"]
    first, second = listed[0], listed[1]
    # O moves the first to the other node; A keeps the next one here (marked kept)
    border.locator("[role=treeitem]").first.click()
    page.keyboard.press("o")
    wait_status(ui, "1 unsaved change")
    wait_placed(ui, first["keyword"], first["other"])
    assert undo_label(ui).startswith("Undo: Move 1 keyword to")
    page.wait_for_function(
        "(k) => document.getElementById(document.activeElement.getAttribute('aria-activedescendant'))"
        "?.textContent.includes(k)",
        arg=second["keyword"],
    )
    page.keyboard.press("a")
    wait_status(ui, "2 unsaved changes")
    # S sets the next one aside; in the tray, 1 puts it at its first suggested place
    page.keyboard.press("s")
    dialog(ui).locator("button[type=submit]").click()
    dialog(ui).wait_for(state="detached")
    wait_status(ui, "3 unsaved changes")
    page.wait_for_function(f"() => ({DRAFT})?.past.length === 3")
    draft = page.evaluate(f"() => ({DRAFT}).tree")
    aside = [k for k in draft["set_aside"] if k not in t["set_aside"]]
    assert len(aside) == 1 and draft["review"][second["keyword"]] == "kept"
    places = api(ui, "POST", "/api/themes/suggestions", {"tree": draft})["data"]["suggestions"]
    page.get_by_role("tab", name=re.compile("^Set aside")).click()
    tray = page.get_by_role("tree", name="Keywords set aside")
    tray.locator("[role=treeitem]").first.wait_for()
    # the tray is virtualised and also holds the proposal's keywords too broad for any
    # theme: scroll it until the keyword is drawn
    item = tray.locator("[role=treeitem]", has_text=aside[0])
    for step in range(21):
        if item.count():
            break
        tray.evaluate(
            "(el, at) => { let s = el; while (s && s.scrollHeight <= s.clientHeight) "
            "s = s.parentElement; if (s) s.scrollTop = at * s.scrollHeight; }",
            step / 20,
        )
        page.wait_for_timeout(50)
    tray.locator("[role=treeitem]", has_text=aside[0]).click()
    page.locator(".cx-themes-suggest button").first.wait_for()
    page.keyboard.press("1")
    wait_status(ui, "4 unsaved changes")
    wait_placed(ui, aside[0], places[aside[0]][0]["node"])
    assert undo_label(ui) == "Undo: Put back 1 keyword"


def test_a_draft_survives_a_reload(editor):
    ui = editor
    page = ui.page
    t = tree(ui)
    first = next(n for n in t["nodes"] if n["parent"] is None)
    row(ui, name_of(first)).first.click()
    page.keyboard.press("F2")
    dialog(ui).get_by_label("Name in English").fill("Kept in the draft")
    dialog(ui).get_by_label("Name in English").press("Enter")
    wait_status(ui, "1 unsaved change")
    page.reload()
    ui.wait_ready(0)
    page.locator(".cx-themes__body").wait_for()
    banner = page.locator(".cx-themes-banner", has_text="were restored")
    banner.wait_for()
    assert row(ui, "Kept in the draft").count() == 1
    wait_status(ui, "1 unsaved change")
    banner.get_by_role("button", name="Discard them").click()
    wait_status(ui, "proposal")
    assert row(ui, "Kept in the draft").count() == 0


@pytest.mark.allow_console_errors
def test_a_stale_save_reloads_and_merges(editor):
    ui = editor
    page = ui.page
    t = tree(ui)
    first, second = [n for n in t["nodes"] if n["parent"] is None][:2]
    # the editor saves once, then edits again
    row(ui, name_of(first)).first.click()
    page.keyboard.press("F2")
    dialog(ui).get_by_label("Name in English").fill("Mine")
    dialog(ui).get_by_label("Name in English").press("Enter")
    wait_status(ui, "1 unsaved change")
    page.keyboard.press("Control+s")
    wait_status(ui, "Saved")
    row(ui, name_of(second)).first.click()
    page.keyboard.press("F2")
    dialog(ui).get_by_label("Name in English").fill("Mine too")
    dialog(ui).get_by_label("Name in English").press("Enter")
    wait_status(ui, "1 unsaved change")
    # someone else saves a newer version meanwhile
    current = api(ui, "GET", "/api/themes")
    other = current["data"]["tree"]
    third = [n for n in other["nodes"] if n["parent"] is None][2]
    edited = api(
        ui,
        "POST",
        "/api/themes/ops",
        {
            "tree": other,
            "ops": [{"op": "rename_node", "node_id": third["id"], "names": {"en": "Theirs"}}],
        },
    )
    saved = api(
        ui,
        "PUT",
        "/api/themes",
        {"tree": edited["data"]["tree"], "action": "their rename"},
        current["etag"],
    )
    assert saved["status"] == 200
    # saving now is refused (412): reload and merge
    page.keyboard.press("Control+s")
    question = page.get_by_role("alertdialog", name="The tree was saved elsewhere")
    question.wait_for()
    question.get_by_role("button", name="Reload and merge").click()
    page.locator(".cx-toast", has_text="Reloaded").wait_for()
    assert row(ui, "Theirs").count() == 1 and row(ui, "Mine too").count() == 1
    page.keyboard.press("Control+s")
    wait_status(ui, "Saved")
    names = {name_of(n) for n in tree(ui)["nodes"]}
    assert {"Mine", "Mine too", "Theirs"} <= names
    network = [e for e in ui.collected.console_errors if "412" not in e]
    assert network == [], network
    assert ui.collected.page_errors == []


def test_apply_runs_in_the_background(editor):
    ui = editor
    page = ui.page
    page.locator(".cx-themes__toolbar").get_by_role("button", name="Save and apply").click()
    page.locator(".cx-themes-banner", has_text="Applying the themes").wait_for()
    # the editor stays usable while the job runs
    outline(ui).focus()
    page.keyboard.press("Home")
    page.keyboard.press("F2")
    dialog(ui).get_by_label("Name in English").fill("Edited while applying")
    dialog(ui).get_by_label("Name in English").press("Enter")
    wait_status(ui, "1 unsaved change")
    page.locator(".cx-toast", has_text="The themes are applied").wait_for(timeout=180_000)
    page.get_by_role("tab", name="Map", exact=True).click()
    page.locator(".cx-map-frame__canvas").wait_for()
    assert page.locator(".cx-themes-legend li").count() >= 5


def test_ai_handoff_export_import_and_accept(editor):
    ui = editor
    page = ui.page
    t = tree(ui)
    tops = [n for n in t["nodes"] if n["parent"] is None]
    kws = sorted(t["keywords"])
    page.locator(".cx-themes__toolbar").get_by_role("button", name="More").click()
    menu_item(ui, "AI curation…").click()
    d = dialog(ui)
    d.get_by_text("One part holds the whole tree").wait_for()
    assert d.get_by_role("button", name="Copy the instructions").count() == 1
    d.get_by_role("button", name="I have the answer").click()
    answer = "\n".join(
        [
            f"1 | RENAME | {tops[0]['id']} | Coastal climate records | its keywords are archives",
            f"2 | MOVE | {kws[0]} | {tops[1]['id']} | belongs there",
            f"3 | SET ASIDE | {kws[3]} | too general",
            "4 | MERGE | nowhere | nothing | no such node",
            "A closing sentence.",
        ]
    )
    d.locator("textarea").fill(answer)
    d.get_by_role("button", name="Read the answer").click()
    d.get_by_text("The answer proposes 3 changes").wait_for()
    assert "1 line could not be read" in d.inner_text()
    d.locator(".cx-themes-ai__item").nth(2).locator("input[type=checkbox]").uncheck()
    d.get_by_role("button", name="Preview on the tree").click()
    preview = page.locator(".cx-themes-banner", has_text="Preview of 2 proposed changes")
    preview.wait_for()
    assert row(ui, "Coastal climate records").count() == 1
    preview.get_by_role("button", name="Apply 2 changes").click()
    page.locator(".cx-toast", has_text="2 proposed changes applied").wait_for()
    wait_status(ui, "1 unsaved change")
    assert undo_label(ui) == "Undo: Apply 2 AI proposals"
    page.keyboard.press("Control+z")
    wait_status(ui, "proposal")
    proposals = api(ui, "GET", "/api/themes/handoff/proposals")["data"]["items"]
    assert len(proposals) == 1


def test_every_action_by_the_keyboard_alone(editor):
    """Rename, merge, move, set aside, attribution, split, create, delete, put back, a level,
    undo, redo and save, with keys only (a focus() only starts the script)."""
    ui = editor
    page = ui.page
    keys = page.keyboard
    t = tree(ui)
    tops = [n for n in t["nodes"] if n["parent"] is None]
    topics = [n for n in t["nodes"] if n["parent"] == tops[0]["id"]]
    count = iter(range(1, 100))

    def changed() -> None:
        n = next(count)
        wait_status(ui, rf"^{n} unsaved change")

    def menu(prefix: str) -> None:
        keys.press("Shift+F10")
        page.locator("[role=menu]").wait_for()
        keys.type(prefix)
        keys.press("Enter")

    def closed() -> None:
        page.wait_for_function("() => !document.querySelector('dialog[open]')")

    def active_kind() -> str:
        return page.evaluate(
            "() => { const el = document.getElementById(document.activeElement"
            ".getAttribute('aria-activedescendant')); return el ? (el.hasAttribute('aria-expanded')"
            " ? 'node' : 'keyword') : 'none'; }"
        )

    outline(ui).focus()
    keys.press("Home")
    keys.press("F2")  # rename the first theme
    dialog(ui).wait_for()
    keys.press("Control+a")
    keys.type("Keyboard theme")
    keys.press("Enter")
    closed()
    changed()
    keys.press("ArrowDown")  # its first topic: merge it into its sibling
    menu("Mer")
    dialog(ui).wait_for()
    keys.type(name_of(topics[1]))
    keys.press("Enter")
    closed()
    changed()
    keys.press("Home")
    keys.press("ArrowDown")
    keys.press("ArrowRight")  # open the topic
    keys.press("ArrowDown")
    assert active_kind() == "keyword"
    menu("Mo")  # move the keyword to another theme
    dialog(ui).wait_for()
    keys.type(name_of(tops[2]))
    keys.press("Enter")
    closed()
    changed()
    keys.press("Delete")  # set the next keyword aside
    dialog(ui).wait_for()
    keys.press("Enter")
    closed()
    changed()
    assert active_kind() == "keyword"
    menu("At")  # count the next one nowhere
    dialog(ui).wait_for()
    keys.press("ArrowDown")
    keys.press("ArrowDown")
    keys.press("Enter")
    closed()
    changed()
    keys.press("ArrowUp")  # back to the topic: split two keywords off
    while active_kind() != "node":
        keys.press("ArrowUp")
    menu("Sp")
    dialog(ui).wait_for()
    keys.type("Keyboard topic")
    for _ in range(2):
        while page.evaluate("() => document.activeElement.type") != "checkbox":
            keys.press("Tab")
        keys.press("Space")
        keys.press("Tab")
    while page.evaluate("() => document.activeElement.type") != "text":
        keys.press("Shift+Tab")
    keys.press("Enter")
    closed()
    changed()
    menu("New")  # a node beside it, then delete it (it is empty); a space would choose
    dialog(ui).wait_for()
    keys.type("Empty for a moment")
    keys.press("Enter")
    closed()
    changed()
    page.wait_for_function(
        "() => { const el = document.getElementById(document.activeElement"
        ".getAttribute('aria-activedescendant')); return Boolean(el && el.textContent"
        ".startsWith('Empty for a moment')); }"
    )
    keys.press("Delete")
    changed()
    for k in ("Shift+Tab", "Shift+Tab", "ArrowRight", "Tab", "Tab", "Home"):
        keys.press(k)  # to the tabs, the « Set aside » tab, its list, its first keyword
    keys.press("Shift+F10")  # « Put back » is the first item
    page.locator("[role=menu]").wait_for()
    keys.press("Enter")
    changed()
    page.locator(".cx-themes__toolbar").get_by_role("button", name="Levels").focus()
    keys.press("Enter")  # a new level at the bottom
    page.locator("[role=menu]").wait_for()
    keys.type("Ins")
    keys.press("Enter")
    dialog(ui).wait_for()
    keys.press("Enter")
    closed()
    changed()
    assert tree_after(ui) is not None
    keys.press("Control+z")
    wait_status(ui, r"^9 unsaved change")
    keys.press("Control+Shift+z")
    wait_status(ui, r"^10 unsaved change")
    keys.press("Control+s")
    wait_status(ui, "^Saved")
    saved = tree(ui)
    assert saved["depth"] == 3
    assert any(name_of(n) == "Keyboard theme" for n in saved["nodes"])
    assert any(name_of(n) == "Keyboard topic" for n in saved["nodes"])
    assert not any(name_of(n) == "Empty for a moment" for n in saved["nodes"])
    # one keyword set aside, one put back (the first of the tray: one the comb set aside)
    assert 0 in saved["attribution"].values()
    assert len(saved["set_aside"]) == len(t["set_aside"])


# ── accessibility ────────────────────────────────────────────────────────────


def test_the_editor_and_its_dialogs_have_no_serious_violation(
    demo_s, app_for, open_app, axe_source
):
    ui = open_app(app_for(demo_s), bypass_csp=True)
    open_editor(ui)
    page = ui.page
    problems = blocking(run_axe(ui, axe_source))
    assert problems == [], "editor:\n" + "\n".join(problems)
    outline(ui).focus()
    page.keyboard.press("Home")
    for opener, name in (("F2", "rename"), ("Shift+F10", "menu")):
        page.keyboard.press(opener)
        target = "dialog[open]" if name == "rename" else "[role=menu]"
        page.locator(target).wait_for()
        problems = blocking(run_axe(ui, axe_source, target))
        assert problems == [], f"{name}:\n" + "\n".join(problems)
        page.keyboard.press("Escape")
    page.keyboard.press("Shift+F10")
    menu_item(ui, "Merge with…").click()
    problems = blocking(run_axe(ui, axe_source, "dialog[open]"))
    assert problems == [], "pick a node:\n" + "\n".join(problems)
    page.keyboard.press("Escape")
    page.get_by_role("tab", name="Map", exact=True).click()
    page.locator(".cx-map-frame__canvas").wait_for()
    problems = blocking(run_axe(ui, axe_source))
    assert problems == [], "map:\n" + "\n".join(problems)
    page.locator(".cx-themes__toolbar").get_by_role("button", name="More").click()
    menu_item(ui, "AI curation…").click()
    dialog(ui).get_by_text("One part holds the whole tree").wait_for()
    problems = blocking(run_axe(ui, axe_source, "dialog[open]"))
    assert problems == [], "AI handoff:\n" + "\n".join(problems)


# ── the budget on the L world ────────────────────────────────────────────────


@pytest.mark.slow
def test_page_ready_and_search_on_the_l_world(demo_l, app_for, open_app):
    ui = open_app(app_for(demo_l))
    page = ui.page
    before = ui.token()
    ui.navigate("/themes")
    page.locator(".cx-themes__body").wait_for()
    ready = ui.ready_log()[-1]
    assert ready["pageId"] == "themes"
    t = tree(ui)
    n_keywords = len(t["keywords"])
    # search over the whole vocabulary: from a key press to the rows on screen
    field = page.locator("#cx-themes-search")
    field.focus()
    searches = []
    for query in ("coast", "sediment", "a", "zz", "flux"):
        searches.append(
            page.evaluate(
                """(q) => new Promise((resolve) => {
                  const field = document.getElementById('cx-themes-search');
                  const status = document.querySelector('.cx-themes-search__status');
                  const before = status.textContent;
                  const start = performance.now();
                  field.value = q;
                  field.dispatchEvent(new Event('input', {bubbles: true}));
                  const check = () => (status.textContent !== before || performance.now() - start > 2000)
                    ? resolve(performance.now() - start) : requestAnimationFrame(check);
                  requestAnimationFrame(check);
                })""",
                query,
            )
        )
    field.fill("")
    measures = {
        "keywords": n_keywords,
        "nodes": len(t["nodes"]),
        "ready_ms": round(ready["duration"], 1),
        "search_ms": [round(x, 1) for x in searches],
    }
    write_measures(MEASURES, "themes_budgets_l", measures)
    assert before < ui.token()
    assert ready["duration"] < 1000, measures
    assert max(searches) < 100, measures


# ── screenshots of every state (--ui-screenshots) ────────────────────────────


@pytest.mark.slow
def test_screenshots_of_every_state(demo_s, app_for, open_app, pytestconfig):
    target = pytestconfig.getoption("--ui-screenshots")
    if not target:
        pytest.skip("pass --ui-screenshots DIR to write the screenshots")
    from pathlib import Path

    out = Path(target) / "themes"
    out.mkdir(parents=True, exist_ok=True)
    for theme, locale in (("light", "en"), ("dark", "en"), ("light", "fr")):
        ui = open_app(app_for(demo_s), theme=theme, locale=locale)
        _states(ui, out, f"{theme}-{locale}")
        ui.page.context.close()


def _states(ui, out, suffix: str) -> None:
    """Every state of the editor, in the interface's language (menus found by their item ids)."""
    page = ui.page

    def shot(name: str) -> None:
        page.wait_for_timeout(250)
        page.screenshot(path=str(out / f"{name}-{suffix}.png"))

    def closed() -> None:
        page.wait_for_function("() => !document.querySelector('dialog[open]')")

    def act(target, item: str) -> None:
        target.click(button="right")
        page.locator(f'[role=menu] [data-item="{item}"]').click()

    open_editor(ui)
    # a few keywords wait in the « To check » queue, as after a rebase
    current = api(ui, "GET", "/api/themes")
    t = current["data"]["tree"]
    marked = api(
        ui,
        "POST",
        "/api/themes/ops",
        {
            "tree": t,
            "ops": [
                {"op": "set_review", "keywords": sorted(t["keywords"])[:3], "state": "to_check"}
            ],
        },
    )["data"]["tree"]
    api(ui, "PUT", "/api/themes", {"tree": marked, "action": "mark"}, current["etag"])
    page.reload()
    ui.wait_ready(0)
    page.locator(".cx-themes__body").wait_for()
    shot("open")
    t = tree(ui)
    tops = [n for n in t["nodes"] if n["parent"] is None]
    page.locator("#cx-themes-search").fill(sorted(t["keywords"])[5].split()[0])
    shot("search")
    page.locator("#cx-themes-search").fill("")
    first_top = outline(ui).locator("[role=treeitem][aria-level='1']").first
    first_top.click(button="right")
    page.locator("[role=menu]").wait_for()
    shot("menu")
    page.keyboard.press("Escape")
    act(first_top, "rename")
    dialog(ui).wait_for()
    shot("rename")
    dialog(ui).locator("input").first.fill("Coastal climate records")
    dialog(ui).locator("input").first.press("Enter")
    closed()
    topic = outline(ui).locator("[role=treeitem][aria-level='2']").first
    topic.click()
    page.keyboard.press("ArrowRight")
    keyword = outline(ui).locator("[role=treeitem]:not([aria-expanded])").first
    act(keyword, "move-keywords")
    dialog(ui).wait_for()
    shot("move")
    page.keyboard.press("Escape")
    closed()
    act(topic, "split")
    dialog(ui).wait_for()
    boxes = dialog(ui).locator("input[type=checkbox]")
    boxes.nth(0).check()
    boxes.nth(1).check()
    dialog(ui).locator("input:not([type=checkbox]):not([type=search])").first.fill("A finer topic")
    shot("split")
    page.keyboard.press("Escape")
    closed()
    act(outline(ui).locator("[role=treeitem]:not([aria-expanded])").first, "aside")
    dialog(ui).wait_for()
    dialog(ui).locator("button[type=submit]").click()
    closed()
    page.locator(".cx-themes-outline .cx-tabs__tab").nth(1).click()
    page.locator(".cx-themes-outline [role=tree] [role=treeitem]").first.click()
    shot("aside")
    page.locator(".cx-themes-outline .cx-tabs__tab").nth(2).click()
    page.locator(".cx-themes-outline [role=tree] [role=treeitem]").first.click()
    shot("check")
    page.locator(".cx-themes-outline .cx-tabs__tab").nth(3).click()
    page.locator(".cx-themes-outline [role=tree] [role=treeitem]").first.click()
    shot("borderline")
    page.locator(".cx-themes-outline .cx-tabs__tab").nth(0).click()
    page.locator(".cx-themes-centre .cx-tabs__tab").nth(1).click()
    page.locator(".cx-map-frame__canvas").wait_for()
    shot("map")
    page.locator(".cx-themes-centre .cx-tabs__tab").nth(0).click()
    page.keyboard.press("Control+s")
    page.wait_for_function(
        "() => !document.querySelector('.cx-themes__status .cx-themes-state.is-dirty')"
    )
    toolbar = page.locator(".cx-themes__toolbar")
    toolbar.locator(".cx-menubutton button").nth(1).click()
    page.locator('[role=menu] [data-item="versions"]').click()
    dialog(ui).locator(".cx-themes-versions__item").nth(1).wait_for()
    shot("versions")
    dialog(ui).locator(".cx-themes-versions__item").nth(1).locator("button").nth(1).click()
    page.locator("dialog[open] .cx-themes-changes").wait_for()
    shot("compare")
    page.keyboard.press("Escape")  # the comparison, then the versions
    page.wait_for_function("() => document.querySelectorAll('dialog[open]').length === 1")
    page.keyboard.press("Escape")
    closed()
    toolbar.locator(".cx-menubutton button").nth(1).click()
    page.locator('[role=menu] [data-item="ai"]').click()
    dialog(ui).locator(".cx-themes-ai__part").wait_for()
    shot("handoff")
    dialog(ui).locator(".cx-dialog__footer button").nth(1).click()
    answer = "\n".join(
        [
            f"1 | RENAME | {tops[2]['id']} | Coastal observation | clearer",
            f"2 | MERGE | {tops[3]['id']} | {tops[4]['id']} | one theme",
            f"3 | MOVE | nope | {tops[0]['id']} | x",
        ]
    )
    dialog(ui).locator("textarea").fill(answer)
    dialog(ui).locator(".cx-dialog__footer button").nth(1).click()
    dialog(ui).locator(".cx-themes-ai__item").first.wait_for()
    shot("review")
    dialog(ui).locator(".cx-dialog__footer button").nth(1).click()
    closed()
    page.locator(".cx-themes-banner").first.wait_for()
    shot("preview")
    page.locator(".cx-themes-banner button").nth(2).click()
    act(outline(ui).locator("[role=treeitem][aria-level='1']").first, "rename")
    dialog(ui).locator("input").first.fill("Unsaved name")
    dialog(ui).locator("input").first.press("Enter")
    closed()
    page.reload()
    ui.wait_ready(0)
    page.locator(".cx-themes-banner").first.wait_for()
    shot("restored")

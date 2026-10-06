# SPDX-License-Identifier: MIT
"""Static checks of the web interface (``cartolex/app/static/``), without npm.

Checks, each returning a list of problems (empty: it passes):

``parse``
    Every JavaScript file parses: ES modules as modules, the one classic
    script (``core/boot-theme.js``) as a script. It uses Node (the one bundled
    with Playwright's Python package, ``$CARTOLEX_NODE`` or ``node`` on the
    path) through ``vm.SourceTextModule``: a real JavaScript parser, nothing
    executed. Without Node it is skipped, with the reason.
``imports``
    Every relative import resolves to a file; no bare specifier outside
    ``vendor/``; no import cycle among the ``core/`` modules. When Node runs,
    its list of each module's imports must equal this checker's.
``literals``
    User-visible text in templates comes from the catalogues: no letters in
    the text of an ``html`` template or in a user-visible attribute (``aria-*``
    labels, ``title``, ``placeholder``, ``alt``, ``label``), and no literal
    text assigned to ``textContent``, ``title`` or the like. The allow-list
    (:data:`LITERAL_ALLOW`) is narrow: text inside ``<code>`` (identifiers,
    token names) and text without any letter (punctuation, digits, symbols).
``bans``
    No ``eval``, ``new Function`` or string timers; no ``innerHTML``,
    ``outerHTML``, ``insertAdjacentHTML`` or ``document.write`` with anything
    but a constant string; no ``dangerouslySetInnerHTML``; no inline event
    handler or ``style`` attribute in templates or in the shell document; no
    ``javascript:`` URL; no inline ``<script>`` or ``<style>`` in the shell.
``vendor``
    The vendored files match the SHA-256 in their ``VENDOR.md``.
``catalogues``
    The English, French and Portuguese (Brazil) catalogues hold the same keys;
    every message parses (the ICU subset of ``core/i18n.js``); a key's
    arguments are the same in every language; every key the sources name
    literally (``t('…')``) exists.
``contrast``
    WCAG contrast of the tokens in both themes: 4.5:1 for every text colour
    on every background, 3:1 for control boundaries and the focus ring; the
    two dark-theme blocks of ``tokens.css`` are identical.

Usage::

    python tools/ui_check.py            # every check, one line each
    python tools/ui_check.py --only literals catalogues
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "cartolex" / "app" / "static"
TEST_VENDOR = ROOT / "tests" / "browser" / "vendor"
LOCALES = ("en", "fr", "pt-BR")
#: Classic (non-module) scripts: loaded with a plain <script src> before the modules.
CLASSIC_SCRIPTS = {"core/boot-theme.js"}
CHECKS = ("parse", "imports", "literals", "bans", "vendor", "catalogues", "contrast")

#: What the literal-text lint lets through, and why. Keep it short.
LITERAL_ALLOW = {
    "code": "text inside <code>…</code> is an identifier or a token name, not language",
    "no-letter": "text without a letter (punctuation, digits, symbols) needs no translation",
}

#: Attributes whose value a person sees or hears.
VISIBLE_ATTRIBUTES = {
    "aria-label",
    "aria-description",
    "aria-roledescription",
    "aria-valuetext",
    "aria-placeholder",
    "title",
    "placeholder",
    "alt",
    "label",
}

LETTER = re.compile(r"[^\W\d_]")


# ── JavaScript scanning ─────────────────────────────────────────────────────


@dataclass
class Template:
    """A template literal: its tag (or '') and its static parts, with their lines."""

    tag: str
    line: int
    chunks: list[str] = field(default_factory=list)


@dataclass
class Scan:
    """A JavaScript source with its comments blanked out, and its template literals."""

    code: str
    templates: list[Template]


_KEYWORDS_BEFORE_REGEX = {
    "return",
    "typeof",
    "case",
    "in",
    "of",
    "void",
    "throw",
    "new",
    "delete",
    "else",
    "do",
    "yield",
    "await",
}


def _regex_allowed(src: str, i: int) -> bool:
    """Whether a '/' at *i* starts a regular expression (not a division)."""
    j = i - 1
    while j >= 0 and src[j] in " \t\r\n":
        j -= 1
    if j < 0:
        return True
    prev = src[j]
    if prev.isalnum() or prev in "_$":
        k = j
        while k >= 0 and (src[k].isalnum() or src[k] in "_$"):
            k -= 1
        return src[k + 1 : j + 1] in _KEYWORDS_BEFORE_REGEX
    return prev not in ")]}"


def scan_js(src: str) -> Scan:
    """Blank the comments of *src* (keeping line breaks) and collect its template literals."""
    out = list(src)
    n = len(src)
    templates: list[Template] = []
    # Stack of contexts: ["code", brace depth] or ["template", Template, chunk start].
    stack: list[list] = [["code", 0]]
    i = 0

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        ctx = stack[-1]
        c = src[i]
        if ctx[0] == "template":
            if c == "\\":
                i += 2
                continue
            if c == "`":
                ctx[1].chunks.append(src[ctx[2] : i])
                stack.pop()
                i += 1
                continue
            if c == "$" and src[i + 1 : i + 2] == "{":
                ctx[1].chunks.append(src[ctx[2] : i])
                stack.append(["code", 0])
                i += 2
                continue
            i += 1
            continue
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            i = j + 1
            continue
        if c == "`":
            m = re.search(r"([A-Za-z_$][\w$]*)\s*$", src[max(0, i - 40) : i])
            tpl = Template(tag=m.group(1) if m else "", line=src.count("\n", 0, i) + 1)
            templates.append(tpl)
            stack.append(["template", tpl, i + 1])
            i += 1
            continue
        if c == "/" and src[i + 1 : i + 2] == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
            continue
        if c == "/" and src[i + 1 : i + 2] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
            continue
        if c == "/" and _regex_allowed(src, i):
            j = i + 1
            in_class = False
            while j < n and src[j] != "\n":
                ch = src[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    break
                j += 1
            i = j + 1
            while i < n and src[i].isalpha():
                i += 1
            continue
        if c == "{":
            ctx[1] += 1
        elif c == "}":
            if ctx[1] == 0 and len(stack) > 1:
                stack.pop()
                parent = stack[-1]
                if parent[0] == "template":
                    parent[2] = i + 1
                i += 1
                continue
            ctx[1] -= 1
        i += 1
    return Scan("".join(out), templates)


_STATIC_IMPORT = re.compile(
    r"(?:^|[;\n}])\s*(?:import\s*(?:[\w$*{}\s,]+?\s*from\s*)?|export\s*(?:\*(?:\s*as\s+[\w$]+)?|\{[^}]*\})\s*from\s*)"
    r"(['\"])([^'\"\n]+)\1"
)
_DYNAMIC_IMPORT = re.compile(r"\bimport\(\s*(['\"])([^'\"\n]+)\1\s*\)")


def static_imports(scan: Scan) -> list[str]:
    """The specifiers of the static imports and re-exports of a scanned module."""
    return [m.group(2) for m in _STATIC_IMPORT.finditer(scan.code)]


def dynamic_imports(scan: Scan) -> list[str]:
    """The literal specifiers of the dynamic ``import()`` calls of a scanned module."""
    return [m.group(2) for m in _DYNAMIC_IMPORT.finditer(scan.code)]


#: Built, not written here: the documentation (tools/build_docs.py checks its own pages).
GENERATED = ("docs",)


def _generated(path: Path, root: Path) -> bool:
    return path.relative_to(root).parts[0] in GENERATED


def js_files(root: Path = STATIC, *, vendor: bool = False) -> list[Path]:
    """The JavaScript files under *root*, the vendored ones only when asked, never the
    built documentation's."""
    files = sorted(p for p in root.rglob("*.js") if not _generated(p, root))
    return [p for p in files if vendor or "vendor" not in p.relative_to(root).parts]


def rel(path: Path, root: Path = STATIC) -> str:
    """*path* relative to *root*, with forward slashes."""
    return path.relative_to(root).as_posix()


# ── parse (Node) ─────────────────────────────────────────────────────────────

NODE_SCRIPT = r"""
const vm = require('vm');
const fs = require('fs');
const files = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = [];
for (const f of files) {
  const src = fs.readFileSync(f.path, 'utf8');
  try {
    if (f.kind === 'module') {
      const m = new vm.SourceTextModule(src, { identifier: f.path });
      const deps = m.moduleRequests ? m.moduleRequests.map((r) => r.specifier) : m.dependencySpecifiers;
      out.push({ path: f.path, ok: true, deps: [...deps] });
    } else {
      new vm.Script(src, { filename: f.path });
      out.push({ path: f.path, ok: true, deps: [] });
    }
  } catch (e) {
    const where = e && e.stack ? String(e.stack).split('\n')[0] : '';
    out.push({ path: f.path, ok: false, error: String(e && e.message) + ' ' + where });
  }
}
process.stdout.write(JSON.stringify(out));
"""


def find_node() -> str | None:
    """A Node executable: $CARTOLEX_NODE, Playwright's bundled one, or `node` on the path."""
    env = os.environ.get("CARTOLEX_NODE")
    if env and Path(env).is_file():
        return env
    spec = importlib.util.find_spec("playwright")
    if spec and spec.origin:
        driver = Path(spec.origin).parent / "driver"
        for name in ("node", "node.exe"):
            if (driver / name).is_file():
                return str(driver / name)
    return shutil.which("node")


def node_parse(files: list[Path], node: str, root: Path = STATIC) -> list[dict]:
    """Parse *files* with Node; one result per file (``ok``, ``deps`` or ``error``)."""
    payload = [
        {"path": str(p), "kind": "script" if rel(p, root) in CLASSIC_SCRIPTS else "module"}
        for p in files
    ]
    proc = subprocess.run(
        [node, "--experimental-vm-modules", "--no-warnings", "-e", NODE_SCRIPT],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"node failed: {proc.stderr.strip()[:400]}")
    return json.loads(proc.stdout)


def check_parse(node: str | None = None, root: Path = STATIC) -> tuple[list[str], str]:
    """Every JavaScript file parses. Returns (problems, a note: how it ran or why it did not)."""
    node = node or find_node()
    if node is None:
        return [], "skipped: no Node (install the dev extra's Playwright, or set $CARTOLEX_NODE)"
    files = js_files(root, vendor=True)
    problems = [
        f"{rel(Path(r['path']), root)}: {r['error']}"
        for r in node_parse(files, node, root)
        if not r["ok"]
    ]
    return problems, f"{len(files)} files parsed with {Path(node).name}"


# ── imports ─────────────────────────────────────────────────────────────────


def _resolve(spec: str, importer: Path, root: Path) -> Path | None:
    if spec.startswith("/static/"):
        return root / spec.removeprefix("/static/")
    if spec.startswith(("./", "../")):
        return (importer.parent / spec).resolve()
    return None


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Every elementary cycle found by a depth-first search (one per back edge)."""
    found: list[list[str]] = []
    state: dict[str, int] = {}
    path: list[str] = []

    def visit(node: str) -> None:
        state[node] = 1
        path.append(node)
        for nxt in sorted(graph.get(node, ())):
            if state.get(nxt) == 1:
                found.append([*path[path.index(nxt) :], nxt])
            elif nxt not in state:
                visit(nxt)
        path.pop()
        state[node] = 2

    for node in sorted(graph):
        if node not in state:
            visit(node)
    return found


def check_imports(root: Path = STATIC, node: str | None = None) -> list[str]:
    """Relative imports resolve, no bare specifier outside vendor/, no cycle in core/."""
    problems: list[str] = []
    graph: dict[str, set[str]] = {}
    scans: dict[Path, list[str]] = {}
    for path in js_files(root, vendor=True):
        name = rel(path, root)
        if name in CLASSIC_SCRIPTS:
            continue
        scan = scan_js(path.read_text(encoding="utf-8"))
        specs = static_imports(scan)
        scans[path] = specs
        in_vendor = name.startswith("vendor/")
        for spec in [*specs, *dynamic_imports(scan)]:
            target = _resolve(spec, path, root)
            if target is None:
                problems.append(f"{name}: bare specifier {spec!r}" + ("" if not in_vendor else ""))
                continue
            if not target.is_file():
                problems.append(f"{name}: {spec!r} does not resolve to a file")
                continue
            if spec in specs and name.startswith("core/"):
                other = rel(target, root)
                if other.startswith("core/"):
                    graph.setdefault(name, set()).add(other)
    for cycle in _cycles(graph):
        problems.append("import cycle in core/: " + " -> ".join(cycle))
    node = node or find_node()
    if node is not None:
        for result in node_parse(sorted(scans), node, root):
            if result["ok"] and set(result["deps"]) != set(scans[Path(result["path"])]):
                problems.append(
                    f"{rel(Path(result['path']), root)}: this checker read imports "
                    f"{sorted(scans[Path(result['path'])])}, the parser {sorted(result['deps'])}"
                )
    return problems


# ── literal text ────────────────────────────────────────────────────────────

_ATTR = re.compile(r"([A-Za-z_:][\w:.-]*)\s*=\s*(\"([^\"]*)\"|'([^']*)')")
_JS_TEXT = re.compile(
    r"(?:\.(?:textContent|innerText|title|placeholder|ariaLabel|alt)\s*=\s*"
    r"|document\.title\s*=\s*"
    r"|setAttribute\(\s*['\"](?:aria-label|title|placeholder|alt|aria-description)['\"]\s*,\s*)"
    r"(['\"`])((?:\\.|(?!\1).)*)\1"
)


def template_markup(tpl: Template) -> Iterator[tuple[str, str]]:
    """The (kind, text) pieces of an html template: 'text', or the name of an attribute."""
    markup = "\0".join(tpl.chunks)
    i, n = 0, len(markup)
    code_depth = 0
    text_start = 0
    while i < n:
        if markup[i] == "<" and i + 1 < n and (markup[i + 1].isalpha() or markup[i + 1] in "/!\0"):
            text = markup[text_start:i]
            if code_depth == 0:
                yield "text", text
            j = i + 1
            quote = None
            while j < n:
                ch = markup[j]
                if quote:
                    if ch == quote:
                        quote = None
                elif ch in "\"'":
                    quote = ch
                elif ch == ">":
                    break
                j += 1
            tag = markup[i + 1 : j]
            name = re.match(r"/?([A-Za-z][\w-]*)", tag)
            if name and name.group(1).lower() == "code":
                code_depth += -1 if tag.startswith("/") else 1
            for m in _ATTR.finditer(tag):
                value = m.group(3) if m.group(3) is not None else m.group(4)
                yield m.group(1).lower(), value
            i = j + 1
            text_start = i
            continue
        i += 1
    if code_depth == 0:
        yield "text", markup[text_start:]


def check_literals(root: Path = STATIC) -> list[str]:
    """No user-visible literal text in templates or DOM text assignments."""
    problems: list[str] = []
    for path in js_files(root):
        name = rel(path, root)
        scan = scan_js(path.read_text(encoding="utf-8"))
        for tpl in scan.templates:
            if tpl.tag != "html":
                continue
            for kind, value in template_markup(tpl):
                visible = value.replace("\0", " ")
                if kind != "text" and kind not in VISIBLE_ATTRIBUTES:
                    continue
                if LETTER.search(visible):
                    where = "text" if kind == "text" else f"attribute {kind}"
                    problems.append(
                        f"{name}:{tpl.line}: literal {where} {visible.strip()[:60]!r}; "
                        "use a catalogue key"
                    )
        for m in _JS_TEXT.finditer(scan.code):
            if LETTER.search(m.group(2)):
                line = scan.code.count("\n", 0, m.start()) + 1
                problems.append(f"{name}:{line}: literal text {m.group(2)[:60]!r} set on the page")
    return problems


# ── bans ────────────────────────────────────────────────────────────────────

_CONSTANT = r"""(?:'[^'\\\n]*'|"[^"\\\n]*"|`[^`$\\]*`)"""
_BANS = [
    (re.compile(r"(?<![\w$.])eval\s*\("), "eval()"),
    (re.compile(r"\bnew\s+Function\s*\(|(?<![\w$.])Function\s*\("), "new Function()"),
    (re.compile(r"\bset(?:Timeout|Interval)\s*\(\s*['\"`]"), "a timer given a string"),
    (
        re.compile(rf"\.(?:inner|outer)HTML\s*\+?=(?!\s*{_CONSTANT}\s*[;\n)])"),
        "innerHTML/outerHTML with a non-constant value",
    ),
    (
        re.compile(rf"\binsertAdjacentHTML\s*\(\s*[^,]+,(?!\s*{_CONSTANT}\s*\))"),
        "insertAdjacentHTML with a non-constant value",
    ),
    (re.compile(r"\bdocument\.write(?:ln)?\s*\("), "document.write"),
    (re.compile(r"\bdangerouslySetInnerHTML\b"), "dangerouslySetInnerHTML"),
    (
        re.compile(r"\.style\.cssText\s*=|setAttribute\(\s*['\"]style['\"]"),
        "a style set from a string",
    ),
    (re.compile(r"javascript:", re.IGNORECASE), "a javascript: URL"),
]
_INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=\s*[\"']", re.IGNORECASE)
_STYLE_ATTR = re.compile(r"\sstyle\s*=\s*[\"']", re.IGNORECASE)


def check_bans(root: Path = STATIC) -> list[str]:
    """No eval, no HTML from strings, no inline handlers or styles (CSP)."""
    problems: list[str] = []
    for path in js_files(root):
        name = rel(path, root)
        scan = scan_js(path.read_text(encoding="utf-8"))
        for pattern, what in _BANS:
            for m in pattern.finditer(scan.code):
                line = scan.code.count("\n", 0, m.start()) + 1
                problems.append(f"{name}:{line}: {what}")
        for tpl in scan.templates:
            if tpl.tag != "html":
                continue
            markup = "\0".join(tpl.chunks)
            if _INLINE_HANDLER.search(markup):
                problems.append(f"{name}:{tpl.line}: an inline event handler in a template")
            if _STYLE_ATTR.search(markup):
                problems.append(f"{name}:{tpl.line}: a style attribute from a string in a template")
    for page in sorted(root.rglob("*.html")):
        if "vendor" in page.relative_to(root).parts or _generated(page, root):
            continue
        text = page.read_text(encoding="utf-8")
        name = rel(page, root)
        for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", text, re.DOTALL | re.IGNORECASE):
            if "src=" not in m.group(1) or m.group(2).strip():
                problems.append(f"{name}: an inline script")
        if re.search(r"<style\b", text, re.IGNORECASE):
            problems.append(f"{name}: an inline <style> element")
        if _INLINE_HANDLER.search(text):
            problems.append(f"{name}: an inline event handler")
        if _STYLE_ATTR.search(text):
            problems.append(f"{name}: a style attribute")
    return problems


# ── vendored files ──────────────────────────────────────────────────────────


def check_vendor() -> list[str]:
    """The vendored files match their VENDOR.md."""
    spec = importlib.util.spec_from_file_location("vendor_ui", ROOT / "tools" / "vendor_ui.py")
    assert spec and spec.loader
    module = sys.modules.get("vendor_ui") or importlib.util.module_from_spec(spec)
    if "vendor_ui" not in sys.modules:
        sys.modules["vendor_ui"] = module
        spec.loader.exec_module(module)
    problems = []
    for folder in (STATIC / "vendor", TEST_VENDOR):
        problems += [f"{folder.relative_to(ROOT)}: {p}" for p in module.check(folder)]
    return problems


# ── catalogues ──────────────────────────────────────────────────────────────


class MessageError(ValueError):
    """A message that is not valid in the ICU subset."""


def parse_message(text: str) -> list:
    """Parse an ICU message (the subset of core/i18n.js) into nodes; raises MessageError."""
    state = {"i": 0}
    nodes = _nodes(text, state, in_plural=False)
    if state["i"] < len(text):
        raise MessageError(f"unexpected '}}' at {state['i']}")
    return nodes


def _nodes(text: str, state: dict, in_plural: bool) -> list:
    nodes: list = []
    buf = ""
    while state["i"] < len(text):
        i = state["i"]
        ch = text[i]
        if ch == "'":
            nxt = text[i + 1 : i + 2]
            if nxt == "'":
                buf += "'"
                state["i"] += 2
            elif nxt in ("{", "}") or (in_plural and nxt == "#"):
                end = text.find("'", i + 1)
                if end < 0:
                    raise MessageError("unterminated quote")
                buf += text[i + 1 : end]
                state["i"] = end + 1
            else:
                buf += ch
                state["i"] += 1
        elif ch == "{":
            if buf:
                nodes.append(buf)
            buf = ""
            nodes.append(_argument(text, state))
        elif ch == "}":
            break
        elif ch == "#" and in_plural:
            if buf:
                nodes.append(buf)
            buf = ""
            nodes.append({"type": "pound"})
            state["i"] += 1
        else:
            buf += ch
            state["i"] += 1
    if buf:
        nodes.append(buf)
    return nodes


def _word(text: str, state: dict) -> str:
    m = re.compile(r"\s*([^\s{},]+)").match(text, state["i"])
    if not m:
        raise MessageError(f"expected a word at {state['i']}")
    state["i"] = m.end()
    return m.group(1)


def _expect(text: str, state: dict, ch: str) -> None:
    m = re.compile(r"\s*").match(text, state["i"])
    state["i"] = m.end() if m else state["i"]
    if text[state["i"] : state["i"] + 1] != ch:
        raise MessageError(f"expected {ch!r} at {state['i']}")
    state["i"] += 1


def _argument(text: str, state: dict) -> dict:
    _expect(text, state, "{")
    name = _word(text, state)
    state["i"] = re.compile(r"\s*").match(text, state["i"]).end()
    if text[state["i"] : state["i"] + 1] == "}":
        state["i"] += 1
        return {"type": "arg", "name": name}
    _expect(text, state, ",")
    kind = _word(text, state)
    if kind in ("plural", "selectordinal", "select"):
        _expect(text, state, ",")
        options: dict[str, list] = {}
        while True:
            state["i"] = re.compile(r"\s*").match(text, state["i"]).end()
            if text[state["i"] : state["i"] + 1] == "}":
                state["i"] += 1
                break
            if state["i"] >= len(text):
                raise MessageError(f"unterminated {{{name}, {kind}}}")
            key = _word(text, state)
            if key.startswith("offset:"):
                continue
            _expect(text, state, "{")
            options[key] = _nodes(text, state, in_plural=kind != "select")
            _expect(text, state, "}")
        if "other" not in options:
            raise MessageError(f"{{{name}, {kind}}} has no 'other' case")
        return {"type": kind, "name": name, "options": options}
    style = ""
    state["i"] = re.compile(r"\s*").match(text, state["i"]).end()
    if text[state["i"] : state["i"] + 1] == ",":
        state["i"] += 1
        style = _word(text, state)
    _expect(text, state, "}")
    if kind not in ("number", "date", "time", "datetime", "list"):
        raise MessageError(f"unknown argument type {kind!r}")
    return {"type": kind, "name": name, "style": style}


def message_arguments(nodes: list) -> set[tuple[str, str]]:
    """The (name, type) of every argument of a parsed message, nested ones included."""
    found: set[tuple[str, str]] = set()
    for node in nodes:
        if isinstance(node, dict) and "name" in node:
            found.add((node["name"], node["type"]))
            for branch in node.get("options", {}).values():
                found |= message_arguments(branch)
    return found


def load_catalogue(code: str, root: Path = STATIC) -> dict[str, str]:
    """A catalogue's messages (metadata keys removed)."""
    data = json.loads((root / "i18n" / f"{code}.json").read_text(encoding="utf-8"))
    if data.get("$format") != "cartolex-i18n/1":
        raise ValueError(f"i18n/{code}.json: not cartolex-i18n/1")
    return {k: v for k, v in data.items() if not k.startswith("$")}


_T_CALL = re.compile(r"(?<![\w$.])(?:t|has)\(\s*(['\"])([A-Za-z0-9_.-]+)\1")


def used_keys(root: Path = STATIC) -> dict[str, list[str]]:
    """Catalogue keys the sources name literally: key → files."""
    keys: dict[str, list[str]] = {}
    for path in js_files(root):
        scan = scan_js(path.read_text(encoding="utf-8"))
        for m in _T_CALL.finditer(scan.code):
            keys.setdefault(m.group(2), []).append(rel(path, root))
        for m in re.finditer(r"\blabel:\s*(['\"])((?:nav|ext)\.[\w.-]+)\1", scan.code):
            keys.setdefault(m.group(2), []).append(rel(path, root))
    return keys


def check_catalogues(root: Path = STATIC) -> list[str]:
    """Same keys in every language, valid messages, same arguments, used keys exist."""
    problems: list[str] = []
    catalogues = {code: load_catalogue(code, root) for code in LOCALES}
    base = catalogues["en"]
    for code, cat in catalogues.items():
        for key in sorted(set(base) - set(cat)):
            problems.append(f"i18n/{code}.json: missing {key}")
        for key in sorted(set(cat) - set(base)):
            problems.append(f"i18n/{code}.json: {key} is not in en.json")
    for key in sorted(base):
        args = {}
        for code, cat in catalogues.items():
            if key not in cat:
                continue
            try:
                args[code] = message_arguments(parse_message(cat[key]))
            except MessageError as exc:
                problems.append(f"i18n/{code}.json: {key}: {exc}")
        if len({frozenset(a) for a in args.values()}) > 1:
            detail = "; ".join(f"{c}: {sorted(n for n, _ in a)}" for c, a in args.items())
            problems.append(f"{key}: the languages use different arguments ({detail})")
    for key, files in sorted(used_keys(root).items()):
        if key not in base:
            problems.append(f"{files[0]}: the key {key} is in no catalogue")
    return problems


# ── contrast ────────────────────────────────────────────────────────────────

#: Text colours (4.5:1) and the backgrounds they must read on.
TEXT_PAIRS = [
    (fg, bg)
    for fg in ("text", "text-muted", "accent", "danger", "warning")
    for bg in ("bg", "surface", "surface-alt", "surface-raised", "selected")
] + [
    ("text", "danger-surface"),
    ("text-muted", "danger-surface"),
    ("danger", "danger-surface"),
    ("text", "warning-surface"),
    ("warning", "warning-surface"),
    ("text-inverse", "text"),
    ("text-inverse", "text-muted"),
    ("accent-text", "accent"),
]
#: Boundaries of controls and the focus ring (3:1). `--cx-rule` is decorative: exempt.
UI_PAIRS = [
    (fg, bg)
    for fg in ("border", "border-strong", "focus")
    for bg in ("bg", "surface", "surface-alt", "surface-raised", "selected")
] + [("focus", "danger-surface"), ("danger", "danger-surface"), ("danger", "bg")]
#: The hue families of the themes (treemap and map marks): graphics, 3:1 on the page.
UI_PAIRS += [(f"hue-{k}", bg) for k in range(1, 13) for bg in ("bg", "surface")]


def css_blocks(css: str) -> list[tuple[str, str, dict[str, str]]]:
    """(at-rule context, selector, custom properties) of each rule of a style sheet."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    blocks = []
    i, n = 0, len(css)
    context: list[str] = []
    start = 0
    while i < n:
        if css[i] == "{":
            head = css[start:i].strip()
            if head.startswith("@"):
                context.append(head)
                start = i + 1
            else:
                end = css.index("}", i)
                props = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", css[i + 1 : end]))
                blocks.append((" ".join(context), head, {k: v.strip() for k, v in props.items()}))
                i = end
                start = i + 1
        elif css[i] == "}":
            if context:
                context.pop()
            start = i + 1
        i += 1
    return blocks


def theme_tokens(css: str) -> dict[str, dict[str, str]]:
    """The colour tokens of each theme: light, dark (chosen) and dark (from the system)."""
    themes: dict[str, dict[str, str]] = {"light": {}, "dark": {}, "dark-system": {}}
    for context, selector, props in css_blocks(css):
        sel = selector.replace('"', "'").replace(" ", "")
        if sel == ":root" and not context:
            themes["light"].update(props)
        elif sel == ":root[data-theme='dark']":
            themes["dark"].update(props)
        elif (
            sel == ":root:not([data-theme='light'])"
            and "prefers-color-scheme:dark" in context.replace(" ", "")
        ):
            themes["dark-system"].update(props)
    return themes


def _hex(value: str, tokens: dict[str, str]) -> tuple[float, float, float]:
    seen = 0
    while value.startswith("var("):
        # var(--name, fallback): the token when the sheet defines it, else the fallback.
        name, _, fallback = value[4:].rsplit(")", 1)[0].partition(",")
        value = tokens.get(name.strip(), fallback.strip())
        seen += 1
        if seen > 10:
            raise ValueError("var() loop")
    m = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", value.strip())
    if not m:
        raise ValueError(f"not a hex colour: {value}")
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[k : k + 2], 16) / 255 for k in (0, 2, 4))  # type: ignore[return-value]


def luminance(rgb: tuple[float, float, float]) -> float:
    """WCAG relative luminance."""

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    """WCAG contrast ratio of two colours."""
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def contrast_table(root: Path = STATIC) -> list[tuple[str, str, str, float, float]]:
    """(theme, foreground, background, ratio, minimum) for every checked pair."""
    themes = theme_tokens((root / "css" / "tokens.css").read_text(encoding="utf-8"))
    rows = []
    for theme in ("light", "dark"):
        tokens = themes[theme]
        for pairs, minimum in ((TEXT_PAIRS, 4.5), (UI_PAIRS, 3.0)):
            for fg, bg in pairs:
                ratio = contrast(
                    _hex(tokens[f"--cx-{fg}"], tokens), _hex(tokens[f"--cx-{bg}"], tokens)
                )
                rows.append((theme, fg, bg, ratio, minimum))
    return rows


def check_contrast(root: Path = STATIC) -> list[str]:
    """Every text pair at 4.5:1, every boundary at 3:1; the dark blocks identical."""
    themes = theme_tokens((root / "css" / "tokens.css").read_text(encoding="utf-8"))
    problems = []
    if themes["dark"] != themes["dark-system"]:
        diff = sorted(set(themes["dark"].items()) ^ set(themes["dark-system"].items()))
        problems.append(f"tokens.css: the two dark blocks differ: {diff[:4]}")
    try:
        rows = contrast_table(root)
    except (KeyError, ValueError) as exc:
        return [*problems, f"tokens.css: {exc}"]
    for theme, fg, bg, ratio, minimum in rows:
        if ratio < minimum:
            problems.append(f"{theme}: --cx-{fg} on --cx-{bg} is {ratio:.2f}:1 (needs {minimum}:1)")
    return problems


# ── command line ────────────────────────────────────────────────────────────


def run(names: list[str], node: str | None = None) -> tuple[bool, list[str]]:
    """Run the named checks; returns (all passed, one line per check then the problems)."""
    lines, details, ok = [], [], True
    for name in names:
        note = ""
        if name == "parse":
            problems, note = check_parse(node)
        elif name == "imports":
            problems = check_imports(node=node)
        elif name == "literals":
            problems = check_literals()
        elif name == "bans":
            problems = check_bans()
        elif name == "vendor":
            problems = check_vendor()
        elif name == "catalogues":
            problems = check_catalogues()
        else:
            problems = check_contrast()
        ok &= not problems
        status = "FAIL" if problems else ("SKIP" if note.startswith("skipped") else "PASS")
        lines.append(
            f"{status:4}  {name:<10}  {len(problems)} problem(s){f'  ({note})' if note else ''}"
        )
        details += [f"  {name}: {p}" for p in problems]
    return ok, lines + details


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Static checks of the web interface.")
    parser.add_argument("--only", nargs="+", choices=CHECKS, help="run only these checks")
    parser.add_argument("--node", help="the Node executable to parse with")
    args = parser.parse_args(argv)
    names = args.only or list(CHECKS)
    ok, lines = run(names, args.node)
    for line in lines:
        print(line)
    skipped = [line.split()[1] for line in lines if line.startswith("SKIP")]
    summary = "ui: " + ("all passed" if ok else "FAILED")
    if skipped:
        summary += f" (skipped: {', '.join(skipped)})"
    print(summary)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

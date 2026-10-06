# Installing cartolex

cartolex runs on Linux, macOS and Windows with Python 3.10 to 3.14. It installs
as one Python package with the `cartolex` command. The language models that
read the texts are installed separately, one per corpus language.

There are two ways to install it:

- **The installer kit**, for everyone: a small zip attached to each release,
  with a launcher to double-click on each system. It needs no programming and
  no administrator rights. See [The installer kit](#the-installer-kit).
- **pip or uv**, for people at ease with a terminal: see
  [With uv](#with-uv) and [With pip](#with-pip).

## What you need

- Python 3.10 or later: the one of your system, or one that
  [uv](https://docs.astral.sh/uv/) installs for you.
- About 1 GB of disk for the package and its libraries, plus about 50 MB for
  each language model.
- A network connection for the installation: the packages come from the
  Python package index, the language models from the releases of the spaCy
  models on GitHub. Once installed, cartolex works offline, except for
  collection and the optional AI clean-up (see {doc}`privacy`).
- A memory that suits the project: see {doc}`sizes`. A laptop with 16 GB
  builds projects of up to about ten thousand people.

## The installer kit

`cartolex-installer-<version>.zip` holds one launcher per system and a short
guide in English (`README-en.txt`), French (`LISEZMOI-fr.txt`) and Brazilian
Portuguese (`LEIAME-pt-BR.txt`):

| system | launcher | the first time |
| --- | --- | --- |
| macOS | `Install cartolex.command` | right-click › Open, then Open (Gatekeeper; on recent systems: System Settings › Privacy & Security › Open Anyway) |
| Windows | `Install cartolex.bat` (extract the zip first) | SmartScreen: More info › Run anyway |
| Linux | `install-cartolex.sh` | `chmod +x install-cartolex.sh`, then `./install-cartolex.sh` |

The launcher installs the release the kit was made for, with everything in
one folder of the user's home, `~/cartolex` (`%USERPROFILE%\cartolex` on
Windows): a Python environment, the language models of English, French and
Portuguese (pinned and checked against their hashes, as `cartolex models add`
does), and `install.log`. It then adds a shortcut that opens the app in the
browser: `cartolex.command` on the macOS desktop, a desktop and Start menu
shortcut on Windows, an entry in the Linux applications menu. Running the
launcher again updates the installation; deleting the folder and the shortcut
removes it.

It tries three routes in turn, and the log says which one worked and why the
others did not:

1. [uv](https://docs.astral.sh/uv/), downloaded from astral.sh into the
   folder (or the one on the path), with a Python of its own;
2. uv again, trusting the system's certificate store (`UV_NATIVE_TLS=1`),
   which gets through most networks that inspect HTTPS;
3. the Python 3.10 or later already installed, with pip. On Windows without
   one, the launcher stops and asks to install Python from python.org, then
   to run it again.

`HTTPS_PROXY`, `HTTP_PROXY` and `NO_PROXY` are followed. The addresses it
needs are astral.sh (uv, first route only), pypi.org and
files.pythonhosted.org (the packages), github.com and
objects.githubusercontent.com (uv, its Python and the language models).
The kit bundles nothing: it is built with `python tools/installer_zip.py`
(see {doc}`dev/checks`). A **test build** of the kit, for people who try a
version before its release, carries the cartolex wheel beside the launchers,
which install it instead of fetching cartolex, and a `build.txt` naming the
build: `python tools/installer_zip.py --wheel <the wheel> --label <name>`.

## With uv

A release comes as a wheel file (`cartolex-<version>-py3-none-any.whl`). The
commands below install it from the Python package index; to install a wheel
file you were given, put its path in place of `cartolex`
(`uv tool install ./cartolex-<version>-py3-none-any.whl`,
`python -m pip install ./cartolex-<version>-py3-none-any.whl`).

```bash
uv tool install cartolex            # the command, in an environment of its own
cartolex models add en fr           # the language models your texts need
cartolex                            # opens the app in your browser
```

`uv tool install "cartolex[llm]"` adds the AI clean-up by API; see
[Optional parts](#optional-parts). Update with `uv tool upgrade cartolex`.

## With pip

In a virtual environment of its own:

```bash
python -m venv cartolex-env
source cartolex-env/bin/activate        # Windows: cartolex-env\Scripts\activate
python -m pip install cartolex
cartolex models add en fr
cartolex
```

## The language models

Each corpus language has one pinned model: English (`en_core_web_md`, MIT),
French (`fr_core_news_md`, LGPL-LR) and Portuguese (`pt_core_news_md`, CC BY-SA
4.0). They are separate installs with their own licences:

```bash
cartolex models list                # which are installed, which are needed
cartolex models add en fr pt        # asks before each download; --yes accepts
```

`cartolex models add` installs the exact version cartolex was tested with,
checked against its hash, with pip, or with uv when the environment has no
pip. A build that needs a model that is missing stops at the keyword extraction
and names the command that installs it.

## Optional parts

| extra | installs | for |
| --- | --- | --- |
| `llm` | `mistralai` | the AI clean-up of the keywords by API (the other way, the copy-paste hand-off, needs nothing) |
| `tsne` | `openTSNE` | the t-SNE layout of the map, chosen by default from a thousand people |

Install one with `pip install "cartolex[llm]"` (or `"cartolex[llm,tsne]"`).
Without it, the feature says which extra it needs.

The installer kit installs `cartolex[tsne]`, and plain `cartolex` where openTSNE
cannot be installed: openTSNE (BSD-3-Clause) has wheels for Python 3.10 to 3.14
on Linux x86-64, macOS (Intel and Apple silicon) and Windows, but none for Linux
on ARM, where pip would have to compile it. Without it the screens still list
t-SNE, switched off, with the reason and the command that installs it.

## The first run

`cartolex` starts the app on a port of this computer and opens it in your
browser with a link that works once. It keeps the same port from one launch
to the next when it is free, so the browser keeps your settings (light or
dark, unsaved drafts). The first time, the start screen offers a demo project
(an invented research community, see {doc}`demo`) or a new one; afterwards
`cartolex` opens the project you had open last, unless it was moved or
another cartolex holds it (then the start screen opens). Stop
the app with Ctrl-C in its terminal, or close its terminal; it also stops by
itself two minutes after its last browser tab closed, once no build or
collection runs (`--idle-stop MINUTES` changes the delay, `0` keeps it running).

**A project open elsewhere.** One app at a time writes a project. If another
cartolex has it open, opening it says so: close that one first (its terminal,
not only its tab). A project left locked by an app that was force-quit or
crashed opens without a question. If the other app is stuck, or runs on a
computer that is off, **Open anyway** overrides its lock after a warning
(`cartolex project unlock --force FOLDER` on the command line); the other app
then stops saving to the project.

Without a browser, `cartolex app --no-browser` prints the link;
`cartolex api` serves the app for hosting ({doc}`hosting`). The command line
does everything the app does: `cartolex init`, `cartolex build`,
`cartolex status` (see {doc}`build`).

## Troubleshooting

**`cartolex: command not found`.** The environment's scripts folder is not on
the path: activate the environment, or with uv run `uv tool update-shell` and
open a new terminal.

**A build stops at the keyword extraction: a language model is missing.** Run
the command it prints (`cartolex models add <language>`). A model at another
version is refused too: the same command installs the pinned one.

**The models cannot be downloaded (proxy, firewall).** pip and uv follow the
`HTTPS_PROXY` variable. On a computer without access to GitHub, download the
model wheels elsewhere, from the releases of `explosion/spacy-models` on
GitHub, at the versions `cartolex models list` names, and install the files
with `python -m pip install <file>.whl`.

**The browser does not open.** Use the link the command printed; it works
once. `cartolex app --no-browser` prints it without trying.

**Port already in use.** `cartolex` takes another free port by itself
(the browser then starts with its own settings again);
`cartolex api` uses 8000 unless given `--port` (0 picks a free one).

**Windows: a path is too long.** Windows limits paths to 260 characters
unless long paths are switched on. The paths inside a project stay under
about 80 characters, so a project folder near the root of a drive
(`C:\maps\coast`) is safe; long paths can also be switched on by an
administrator (`LongPathsEnabled` in the registry, or the group policy
"Enable Win32 long paths").

**Windows: odd characters in the terminal.** The terminal's code page may lack
some characters of names and terms; cartolex writes them as escapes instead of
stopping. `set PYTHONUTF8=1` (or a terminal set to UTF-8) shows them.

**Windows: a file is in use.** A file of the project open in another program
(a table in a spreadsheet, for example) cannot be replaced while it is open.
Close it, and run the step again.

**The AI clean-up says the `mistralai` package is missing.** Install the
extra: `pip install "cartolex[llm]"`.

**Anything else.** Settings → Care → Diagnostic in the app gives the
versions, the language models and the size of the computer, with nothing of
the project: paste it into your report.

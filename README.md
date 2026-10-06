<p><img src="cartolex/app/static/brand/mark.svg" width="128" height="128" alt="The cartolex logo"></p>

# cartolex

**cartolex draws the map of a research field from the texts of the people who
work in it.** Give it a list of people, a laboratory or an institution: it
collects their publications from open bibliographic services (OpenAlex, the
ORCID registry, HAL), finds the keywords of the field in their titles and
abstracts, groups those keywords into themes, and places every person,
organisation and text on a map, near what they write about. You check each
step on screen (who is who, which keywords count, what the themes are called)
and share the result as a website that opens offline.

It is made for researchers who want to see their field from above, and for
the heads of laboratories, research administrators and librarians who need a
picture of who works on what. No programming is needed: everything happens in
an app that runs in your web browser, on your own computer. The map shows what
people write about, never how good their work is.

![The map of the demo project: the themes on the left, people and keywords on the map, the selected person's themes, keywords and nearest people on the right.](docs/images/map-person.png)

## Install

- **The installer kit** (no programming, no administrator rights): download
  `cartolex-installer-<version>.zip` from the releases, unzip it and
  double-click the launcher of your system (`Install cartolex.command` on
  macOS, `Install cartolex.bat` on Windows, `install-cartolex.sh` on Linux).
  It installs everything in a `cartolex` folder of your home and adds a
  shortcut that opens the app.
- **With pip or uv**, for people at ease with a terminal (Python 3.10 to
  3.14):

  ```bash
  uv tool install cartolex          # or: python -m pip install cartolex
  cartolex models add en fr         # the language models of your texts
  cartolex                          # opens the app in your browser
  ```

Details and troubleshooting: [Installing cartolex](docs/install.md).

## Your first map

The app opens on its Projects screen: press **Create the demo project** (an
invented community of coastal and marine scientists; nothing leaves your
computer), then **Build…** on the overview and **Build 9 stages**. A minute
later the map is there.
The tutorial [Your first map](docs/first-map.md) follows it step by step.

## Documentation

The documentation comes with the app: the **Documentation** button (the
question mark at the top of every screen) opens it for the version you run.
Its sources are in [docs/](docs/index.md):

- [What cartolex does, in its own words](docs/introduction.md): people, texts,
  keywords, themes, the map, the AI steps, sharing;
- tutorials: [your first map](docs/first-map.md),
  [map an institution](docs/tutorial-institution.md),
  [start from a list of names](docs/tutorial-names.md),
  [clean the keywords with an AI copilot](docs/tutorial-keywords.md),
  [shape the themes](docs/tutorial-themes.md),
  [explore the map](docs/tutorial-map.md), [share a site](docs/tutorial-share.md);
- guides: [collection](docs/collection.md), [keywords](docs/keywords.md),
  [the build](docs/build.md), [privacy](docs/privacy.md),
  [very large projects](docs/large-projects.md);
- [the method and its scientific references](docs/about.md).

## Privacy

Everything stays on your computer. Only two steps reach the network, both said
before they start: the collection of publications (names and identifiers go to
the open bibliographic services) and the optional AI clean-up of the keywords
by API (keyword strings only, never texts or people). See
[Privacy and personal data](docs/privacy.md).

## How to cite

If cartolex helped your work, please cite it with the version you used (the
app's About page gives it):

> Klüger, E., & Ronceray, P. (2026). *cartolex* (version 1.0.0.dev0)
> [Computer software]. https://github.com/cartolex/cartolex

[`CITATION.cff`](CITATION.cff) (« Cite this repository » on GitHub) and
[`codemeta.json`](codemeta.json) carry the same reference for reference
managers; [How to cite](docs/cite.md) gives a BibTeX entry and the references
of the method.

## Authors and licence

cartolex is written by Elisa Klüger and Pierre Ronceray. It is free software
under the MIT licence: see [LICENSE](LICENSE).

## For developers

cartolex is a Python package (`cartolex.lexicon` and `cartolex.atlas` for the
engine, `cartolex.collect` for collection, `cartolex.project` and
`cartolex.build` for the project format and its build, `cartolex.app` for the
web app) with a command line that does everything the app does
([docs/dev/command-line.md](docs/dev/command-line.md)). Setting up, checking a
change, the corpus contract and the public programming interface are in
[CONTRIBUTING.md](CONTRIBUTING.md); the rules of the repository in
[AGENTS.md](AGENTS.md); the code's map in [ARCHITECTURE.md](ARCHITECTURE.md);
driving the engine from your own application in
[INTEGRATION.md](INTEGRATION.md); the changes of this release line in
[CHANGELOG.md](CHANGELOG.md).

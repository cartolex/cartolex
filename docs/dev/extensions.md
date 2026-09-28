# Extensions: what a host application adds

cartolex's app runs on its own. A **host application** — an institution's
portal, a lab's service — adds pages, routes, build stages and more by passing
`cartolex.app.Extension` objects to the app, and to the command line
when it ships its own:

```python
from cartolex.app import AppSettings, create_app
from cartolex.cli import main

app = create_app(AppSettings(project=folder), extensions=[my_extension])   # an ASGI app
main(extensions=[my_extension])                                            # its own `cartolex`
```

cartolex never looks for extensions by itself: no entry points, no folder
scanned, no environment variable. What is not passed does not exist.

An extension is a plain frozen dataclass; every field but `id` is optional.
`create_app` checks the extensions it is given, alone and together, and
refuses what does not fit with `ExtensionError` (a
`ValueError`) naming the extension and the field.

## The fields

| field | what it adds |
| --- | --- |
| `id` | the extension's name: lower-case letters, digits, `-`, `_`, a letter first; unique among the extensions. It names its routes (`/api/ext/<id>/…`), its files (`/static/ext/<id>/…`) and its actions (`ext.<id>`) |
| `routers` | FastAPI `APIRouter`s, mounted under `/api/ext/<id>/`. Every route is guarded like cartolex's own: a session, the CSRF header on changes, and `authorize(principal, "ext.<id>", resource)`. A route that works on the open project takes the project context: `ctx: ProjectContext = Depends(project_context)` (`cartolex.app.deps`) |
| `static_dir` | a folder served at `/static/ext/<id>/`, with the media types of the interface's own files (`.js` is `text/javascript`) and the same checks (no path out of the folder, no hidden file) |
| `modules` | ES modules in `static_dir` (`index.js`) the shell imports after reading the manifest; each exports `register(api)`, which adds pages, slots, facets and layers through the interface's registries |
| `i18n` | override catalogues per interface locale (`en`, `fr`, `pt-BR`): a file in `static_dir`, or the messages themselves (`{"nav.reports": "Reports"}`, then served at `/static/ext/<id>/i18n/<locale>.json`). They are merged over cartolex's catalogues, in the order the extensions are given |
| `branding` | a `Branding(name, logo, accent, accent_dark)`: the name the interface shows (and the manifest's `app.name`), a logo (in `static_dir`), and an accent for the light and dark themes, `#rrggbb`, each keeping a contrast of 4.5:1 with its theme's backgrounds (the interface tokens' limit). One extension at most sets it |
| `nav` | `NavEntry(id, label, route, module, order, placement)`: a page. `label` is a catalogue key, `route` a page path (not under `/api`, `/static` or `/launch`), `module` its ES module (in `static_dir`), `order` its place (cartolex's pages use 10 to 90), `placement` `main`, `settings` or `hidden`. Ids and routes are unique, cartolex's included |
| `stages` | `Stage` declarations replacing cartolex's stages of the same id: a runner of the host's, another cost model, another plain name. The project format lists the build's stages, so a stage id of the host's own is refused (see below) |
| `stage_patches` | changes to cartolex's stage declarations, by stage id: any `Stage` field but `id`, and `defaults`, new default values of parameters (`{"themes.group": {"defaults": {"top_groups": 12}}}`), checked against each parameter's limits. A value set in `params.json` still wins |
| `corpus_slots` | the corpus slots (`Slot`) every project created in the app gets, in order; without any, a project gets one collection slot, `collected` |
| `identity_provider` | `provider(NewProject) -> {…}`: fills a new project's identity (`domain_title`, `domain_description`, `ai`) from what the creator typed and who they are |
| `overlay_sets` | the projected sets (`Overlay`) every new project gets |
| `status_keys` | `StatusArea(id, label, stages, probe)`: an area of the project state (`GET /api/project/state`) summing up stages, or items a `probe(project)` returns |
| `capabilities` | flags the interface reads in the manifest (`{"reports": True}`); cartolex's own (`collection`, `ai_api`, `ai_handoff`, `hosted`) cannot be set |
| `middlewares` | Starlette `Middleware` objects, run inside cartolex's own layers: after the host check and the security headers, before the routes |
| `settings_dir_name` | the name of the app's own folder on the computer (default `cartolex`), where the recent projects are kept. One extension at most sets it |
| `prompt_dir` | a folder of prompt templates replacing the packaged ones for every stage (the AI clean-up's). One extension at most sets it |
| `stopword_overlay` | function words added or removed on top of every project's own (`{"add": {"en": ["…"]}, "remove": {"fr": ["…"]}}`); a project's `decisions/stopwords.json` wins where both name a word |
| `cli` | `CliVerb(name, help, run, configure)`: verbs of the `cartolex` command (`configure(parser)` adds the verb's arguments, `run(args)` returns the exit status); a verb cartolex has is refused |
| `on_project_open` | `hook(project)`, called each time the app opens a project (for writing) |
| `project_init` | `hook(project)`, called once on a project the app has just created |

The host's prompt folder and function words are part of the host's code, like
cartolex's packaged lists: changing them does not by itself make a result out
of date, and the stages that read them are forced after such a change
({doc}`build`).

## Stages of a host's own

A build stage writes its results into `derived/<stage id>/`, and the project
format lists the stages a project may hold ({doc}`../format/derived`); a stage
id outside that list is refused by the format's models. An extension can
therefore replace or change cartolex's stages, not add stages of its own.
Letting hosts declare stages (with ids of their own, such as
`ext.<extension>.<name>`, recorded like any other) is a change of the format,
left for a decision on the format.

## An example

A host that adds a « reports » page with its own route, a folder slot for the
reports it receives, its name and accent, and a default for the top level of
the theme tree:

```python
from pathlib import Path

from fastapi import APIRouter, Depends

from cartolex.app import Branding, Extension, NavEntry, StatusArea
from cartolex.app.deps import ProjectContext, project_context
from cartolex.project.models import Slot

router = APIRouter()


@router.get("/summary")
def summary(ctx: ProjectContext = Depends(project_context)) -> dict:
    return {"project": ctx.project.config.name}


reports = Extension(
    id="reports",
    routers=(router,),                                   # GET /api/ext/reports/summary
    static_dir=Path(__file__).with_name("static"),       # /static/ext/reports/…
    modules=("index.js",),                               # export function register(api) {…}
    i18n={"en": {"nav.reports": "Reports"}, "fr": {"nav.reports": "Rapports"}},
    branding=Branding(name="Example portal", logo="logo.svg", accent="#2b47a8"),
    nav=(NavEntry("reports", "nav.reports", "/reports", "pages/reports.js", order=70),),
    stage_patches={"themes.group": {"defaults": {"top_groups": 12}}},
    corpus_slots=(Slot(id="reports", kind="folder", fit=True, trajectory=False),),
    identity_provider=lambda new: {"domain_description": f"Maintained by the portal: {new.name}"},
    status_keys=(StatusArea("reports", "area.reports", stages=("map.layout",)),),
    capabilities={"reports": True},
    settings_dir_name="example-portal",
)
```

`tests/_app_extension.py` is a generic extension of this kind (a page, a slot,
a stage declaration); the tests run the app without it and with it.

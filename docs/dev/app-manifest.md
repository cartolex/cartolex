# The app manifest, `cartolex-manifest/1`

The interface starts from one request: `GET /api/app/manifest`. The shell reads
it, loads the catalogues of the interface language, imports the extensions'
modules (each `register(api)`), renders the navigation and routes. The manifest
is the contract between the app and any interface built on it.

```json
{
  "format": "cartolex-manifest/1",
  "app": {"id": "cartolex", "name": "cartolex", "version": "1.0.0.dev0",
          "build": {"commit": "3f2a1c9e…", "date": "2026-10-06"}, "platform": "Linux 6.8.0 x86_64"},
  "branding": {"name": "cartolex", "logo": "/static/brand/logo.svg", "accent": null},
  "locales": {"available": ["en", "fr", "pt-BR"], "default": "en",
              "catalogues": {"en": ["/static/i18n/en.json"], "fr": ["/static/i18n/fr.json"],
                             "pt-BR": ["/static/i18n/pt-BR.json"]}},
  "nav": [{"id": "overview", "label": "nav.overview", "route": "/overview",
           "module": "/static/pages/overview.js", "order": 10, "placement": "main"}],
  "modules": ["/static/ext/reports/index.js"],
  "capabilities": {"collection": true, "ai_api": false, "ai_copilot": true, "hosted": false, "idle_stop": false},
  "project": {"open": true, "id": "coast-3f2a9c1e", "name": "Coastal map"},
  "security": {"csrf_header": "X-Cartolex-CSRF", "csrf_cookie": "cartolex_csrf_5b1e0c2a"}
}
```

## Keys

| key | what it holds |
| --- | --- |
| `format` | `cartolex-manifest/1` |
| `app` | `id` (`cartolex`), `name` (the host's brand, else `cartolex`), the cartolex `version`, the `build` (`commit` and its `date`: the wheel's build stamp, `tools/build_stamp.py`, or the checkout's last commit; `null` when unknown) and, locally, the `platform` the app runs on (`null` hosted); the interface shows the version and build at the foot of the settings menu and on the About page, and puts them in a diagnostic |
| `branding` | the `name` and `logo` the interface shows, and the host's `accent` per theme (`{"light": "#rrggbb", "dark": …}`) or `null`: the shell sets it as the `--cx-accent` custom property |
| `locales` | the interface languages, the default one, and for each language the catalogues to merge, in order: cartolex's, then each extension's |
| `nav` | the pages, sorted by `order`: `id`, `label` (a catalogue key), `route` (a history route the server answers with the shell), `module` (the ES module that renders the page), `order`, `placement` (`main`, `settings` or `hidden`). cartolex's own pages are overview, people, keywords, themes, map, share (main), build (hidden), and method, settings and start (settings). A page whose module file is missing gets `/static/pages/placeholder.js`, and the app logs a warning once |
| `modules` | the extensions' ES modules the shell imports before the first route |
| `capabilities` | what this app can do: `collection` (texts can be collected), `ai_api` (a key for the AI clean-up by API was given), `ai_copilot` (the AI clean-up outside the app, by the copilot's bundle), `hosted`, `idle_stop` (the local app stops once no page is open: each page sends `POST /api/presence` every 30 s and on closing); extensions add flags of their own |
| `project` | the project the requests work on: `open`, its `id` (the key of cached state in the browser) and `name` |
| `security` | the header state-changing requests carry (`X-Cartolex-CSRF`) and the name of the cookie holding its value, which carries the app instance's id so two apps on two loopback ports never share it |

## Versions

Within `cartolex-manifest/1`, a newer cartolex may add optional keys; an
interface ignores the keys it does not know. A change an interface could
misread (a key removed, a meaning changed) makes the format `/2`.
`tests/fixtures/manifest.example.json` is the example interfaces are tested
against, and the app's tests check that the live manifest holds every key of it.

## The schema

The schema is generated from `cartolex.app.manifest.Manifest` and stored with
the package; `python -m cartolex.app.schemas --check` (run by the tests) fails
when it is out of date, `--write` regenerates it. It is also served at
`GET /api/app/manifest/schema`.

```{literalinclude} ../../cartolex/app/schemas/manifest.schema.json
:language: json
```

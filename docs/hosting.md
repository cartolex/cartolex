# Hosting the app

`cartolex api` serves the app without opening a browser, for a server or a
container. The same app runs locally (`cartolex`), so what works on one
computer works hosted.

```bash
# one project
cartolex api /srv/maps/coast --host 0.0.0.0 --port 8000 --allowed-host maps.example.org
# many projects, one folder each, chosen by the route: /api/projects/<id>/…
cartolex api --projects-root /srv/maps --host 0.0.0.0 --allowed-host maps.example.org
```

The command prints the launch link (`http://…/launch?token=…`), which opens
the app once in a browser and signs that browser in. Keep it private: whoever
holds it holds the project. A new start gives a new link.

## Host names

The app answers only to the names it is given (`--allowed-host`, repeated for
several) and, for one project, to the loopback names; any other `Host` is
refused. Put the public name the browsers use, the one of the reverse proxy.
With many projects (`--projects-root`), the loopback names are not added: add
`127.0.0.1` if a health check calls the app from the same machine.

## HTTPS, sign-in and several people

Put the app behind a reverse proxy that ends HTTPS and forwards to it, and
pass `--secure-cookies` so the session cookies are sent over HTTPS only. The
app sends no CORS header, and refuses a change asked from another site.
A service with its own accounts passes its sign-in to the app
(`AppSettings.authenticate`: a request to a `Principal`) and its own rules
(`AppSettings.authorizer`: may this principal do this action on this
resource?), through a small Python program of its own that calls
`cartolex.app.create_app` and serves it with uvicorn. See {doc}`dev/api`.

A hosted service never deletes a project's folder from the app
(`project_delete_hosted`): whoever runs the service removes projects from its
folder. The site builds and exported files of a project can be deleted from its
Share screen by whoever the service's rules allow (the action `share.delete`).

## The container

`deploy/Dockerfile` builds an image of the app with the language models of the
extraction:

```bash
docker build -f deploy/Dockerfile -t cartolex .                   # from the repository
docker volume create cartolex-project
docker run --rm -v cartolex-project:/project cartolex \
    init /project --name "Coastal map" --field "Coastal and marine systems" --languages en,fr
docker run -d --name cartolex --read-only --tmpfs /tmp \
    -p 127.0.0.1:8000:8000 -v cartolex-project:/project --stop-timeout 30 \
    cartolex api /project --host 0.0.0.0 --port 8000 --data-dir /tmp/cartolex \
    --allowed-host maps.example.org
docker logs cartolex            # the launch link, then one JSON line per request
```

- **A user without rights**: the app runs as `cartolex` (uid 10001), not
  root.
- **A read-only root filesystem**: run it with `--read-only --tmpfs /tmp`. The
  app writes only into the project (the volume `/project`) and into `/tmp`
  (its own folder `/tmp/cartolex`, the numeric libraries' caches).
- **One volume**: `/project`, the project folder. Back up its `project.json`,
  `sources/`, `decisions/` and `cache/ai/` ({doc}`format/index`).
- **The health route**: `GET /api/health` answers `{"status": "ok"}` without a
  session; the image's `HEALTHCHECK` calls it.
- **Logs**: one JSON object per line on the standard error, with a request id
  and the route's template, never a name, a text or a key.
- **The AI keys**: `-e MISTRAL_API_KEY=…` (Mistral AI) or `-e ALBERT_API_KEY=…`
  (Albert) lets the AI clean-up run by API for a project on that provider;
  without one, the AI clean-up with a copilot still works.
- **Stopping**: `docker stop` sends a signal the app handles: running jobs are
  cancelled at their next safe point and the project is closed (its lock
  removed). Give it time (`--stop-timeout 30`).

**After a crash.** A project opened for writing holds a lock (`/project/.lock`)
that names its process and its machine. A container that was killed leaves it
behind, and a new container (another machine name, perhaps the same process
number) cannot tell that its holder is gone: the app refuses to open the
project and names the lock. When you are sure no other container uses the
volume, remove it:

```bash
docker run --rm -v cartolex-project:/project --entrypoint rm cartolex /project/.lock
```

Build the image without the language models (`--build-arg WITH_MODELS=0`) to
install only those of your corpus languages afterwards (`cartolex models add`).

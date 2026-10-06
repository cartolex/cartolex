# The command line

Everything the app does can be done from a terminal with the `cartolex`
command, for scripts, servers and very large projects; the results are the
same, in the same project folder. This page lists the commands; the guides
give their details beside each subject ({doc}`../build`, {doc}`../collection`,
{doc}`../large-projects`, {doc}`../hosting`). Each command prints what it did
and exits with 0 on success, 1 when the project refuses the action or a build
fails (the message says why), 2 on a usage error and 130 when a build was
cancelled. `cartolex <command> --help` gives every option.

## The app

```text
cartolex                                   the app (the same as `cartolex app`)
cartolex app [FOLDER] [--port N] [--no-browser] [--idle-stop MINUTES]
             [--data-dir DIR] [--services demo [--world SIZE:SEED]]
cartolex api [FOLDER] [--host H] [--port N] [--allowed-host NAME…] [--projects-root DIR]
```

`cartolex app` starts the app on a free port of this computer and opens the
browser with a link that works once; it stops by itself two minutes after its
last page closed. `--services demo` runs it against the demo services of a
demo world, on your computer: collection works offline, for trying and for
demonstrations. `cartolex api` serves it without a browser, for hosting
({doc}`../hosting`).

## Projects and builds

```text
cartolex init FOLDER --name NAME --field TITLE [--description TEXT] [--languages en,fr]
cartolex status FOLDER
cartolex build FOLDER [--dry-run] [--only STAGE…] [--force STAGE…] [--yes]
                      [--workers N] [--memory MB] [--scratch DIR]
cartolex params FOLDER [--set STAGE.NAME=VALUE …]
cartolex versions FOLDER [--pin ID | --try-another --seed N]
cartolex project validate FOLDER
cartolex project unlock FOLDER [--force]
cartolex rejects …                         the terms rejected automatically on this computer
cartolex models list
cartolex models add LANG… [--yes]
```

See {doc}`../build` for the build, its states, its parameters and the map
versions, and {doc}`../install` for the language models.

## Collection

```text

cartolex collect people FOLDER FILE [--mapping JSON|FILE] [--dry-run] [--slot ID]
cartolex collect folder FOLDER DIR [--create-people] [--slot ID]
cartolex collect corpus FOLDER INDEX [--root DIR] [--slot ID]
cartolex collect resolve FOLDER [--auto] [--threshold X] [--people ID…] [--again] [SERVICES]
cartolex collect confirm FOLDER PERSON [RECORD…] [--none]
cartolex collect harvest FOLDER [--years FIRST-LAST] [--people ID…] [--resume] [SERVICES]
cartolex collect snapshot FOLDER SNAPSHOT [--years …] [--people ID…] [--resume] [SERVICES]
cartolex collect snapshot-index SNAPSHOT [--jobs N] [--status]
cartolex collect rebuild PROJECT [--workers N] [--scratch DIR]
cartolex collect institutions FOLDER (--search NAME | --institution ID…) [--years …]
                              [--min-works N] [--level TYPE=LEVEL…] [--resume] [--snapshot DIR]
                              [SERVICES]
cartolex collect institutions FOLDER --take all|A…|A1+A2… [--role ROLE]
cartolex collect collaborators FOLDER [--rounds N] [--seeds ID…] [--cap N]
                               [--max-authors N] [--snapshot DIR] [SERVICES]
cartolex collect collaborators FOLDER --decide PERSON=DECISION…
cartolex collect coverage FOLDER [--person ID] [--good N] [--json] [--exclude ID…]
                          [--add-documents ID DIR] [--retry [SERVICES]]
cartolex collect window FOLDER FIRST-LAST|FIRST-|none [--slot ID]
cartolex collect duplicates FOLDER [--limit N] [--merge-clear]
cartolex collect merge FOLDER KEEP OTHER [--override-orcid]
cartolex collect unmerge FOLDER PERSON…

```

The options of every command that reaches a service: `--dry-run` prints the
estimate and what would leave the computer, and sends nothing;
`--refresh` / `--cache-only` ask again or work from what was fetched before;
`--contact EMAIL` (default: `$CARTOLEX_CONTACT`); `--openalex-key KEY`
(default: `$OPENALEX_API_KEY`, else the key saved in the app's settings);
`--services demo` (with `--world SIZE:SEED`) runs against the demo services,
`--services URL` against demo services already running. {doc}`../collection`
explains each command beside the screen that does the same.

## Trying it offline

The demo services answer like OpenAlex and the ORCID registry, on your own
computer, for a demo world ({doc}`../demo`):

```bash
python -m cartolex.demo services --size S --people-list people.csv --list-only
cartolex init demo-project --name "Demo" --field "Coastal systems" --languages en,fr
cartolex collect people demo-project people.csv
cartolex collect resolve demo-project --services demo --world S:0 --auto
cartolex collect harvest demo-project --services demo --world S:0
cartolex build demo-project
```

Or from an institution of the demo world, and a mini snapshot of its index:

```bash
cartolex init inst-project --name "Demo" --field "Coastal systems" --languages en,fr
cartolex collect institutions inst-project --search "Marine" --services demo --world S:0
cartolex collect institutions inst-project --institution I999… --services demo --world S:0
cartolex collect institutions inst-project --take all
python -m cartolex.demo snapshot --size S --out demo-snapshot
cartolex collect snapshot inst-project demo-snapshot --services demo --world S:0
cartolex collect coverage inst-project
cartolex build inst-project
```

`python -m cartolex.demo services` without `--list-only` keeps them running
and prints their address, for `--services http://127.0.0.1:PORT/…`.

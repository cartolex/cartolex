# Usability test of the theme editor (gate G3)

Before the other screens are built, three to five curators use the theme
editor on the demo project while a moderator watches. This page holds what a
session needs: how to prepare the project, the tasks, the moderator's script,
the consent text and a one-page template for the findings.

## Preparing the project

The sessions use the S demo world (invented people, invented texts) at depth
2, themes and topics, never a real project.

```bash
python tools/g3_prepare.py ~/usability/g3-demo          # once: build and prepare (a minute)
python tools/g3_prepare.py ~/usability/g3-demo --reset ~/usability/p1   # before each session
cartolex app ~/usability/p1                             # the app, in the browser
```

The preparation saves the grouping's proposal as the curated tree and fills
the « To check » queue with eight keywords, four of them on a wrong node. It
prints what the tasks name: a theme to rename, misplaced keywords, pairs of
themes to merge, themes that mix two subjects. Choose, for each task, the
example whose answer is plain to someone who knows coastal and marine research
(a fisheries keyword under a palaeoclimate theme, rather than a keyword that
could sit in two places), and write it on the task cards below where they say
« … ». Every participant gets a fresh copy, so the sessions start from the
same state.

Set the browser window to about 1440 × 900 pixels, the interface language to
the participant's, and leave the theme on the system setting.

## The tasks

Read each task aloud, give the card, and start the clock when the participant
starts. Stop it when they say they are done, or after the limit. A task is a
**success** when the tree shows the change, **partial** with help or with a
different but valid change, a **failure** otherwise.

| # | task (as said to the participant) | limit | success when |
| --- | --- | --- | --- |
| 1 | « Find the theme whose keywords are about … (for example: governance and the social uses of the coast). Give it a clear name, in English and in your language. » | 4 min | the node has both names |
| 2 | « The keyword “…” sits under the wrong theme. Find it and move it where it belongs. » Then: « Move one more keyword by dragging it. » | 5 min | both keywords are on the right node; the first by a menu or the keyboard, the second by drag |
| 3 | « The themes “…” and “…” are one subject. Make them one theme. » | 4 min | one node holds both nodes' keywords |
| 4 | « The theme “…” mixes two subjects. Split it so that each subject has its own theme, with a name. » | 6 min | two sibling nodes, each named, each mostly one subject |
| 5 | « Some keywords were added to the map recently and wait for someone to check them. Go through them: keep those that are well placed, move or set aside the others. » | 6 min | the queue is empty; the four misplaced keywords were moved or set aside |
| 6 | « Save your work and update the map. When it is ready, find your renamed theme on the map. » | 5 min | the apply finished and the participant points at the renamed theme's label or colour |

Optional, when time is left: « Undo your last two changes, then redo them »;
« Look at an earlier version of the tree and compare it with the current
one ».

## The moderator's script

**Welcome (3 minutes).** « Thank you for coming. We are testing a new screen,
not you: if something is hard, the screen has to change. There are no wrong
answers. The data on the screen is invented: the people, their texts and
their research field do not exist. Please think aloud: say what you look for,
what you expect, what surprises you. I will not help unless you are stuck; if
you ask me a question, I may answer with another question. You can stop at
any time. » Give the consent text (below), answer questions, start the
recording only after consent.

**Warm-up (2 minutes).** « This screen organises the keywords of a research
field into themes. Take a minute to look around and tell me what you think you
can do here. » Do not explain anything.

**Tasks (about 30 minutes).** For each task: read it, hand the card, note the
start time. Take notes on the template: the path the participant takes, where
they hesitate (more than five seconds without acting), what they say, the
errors (a wrong action, a dialog cancelled, an undo). If they are stuck for
two minutes, give one hint (« where would you expect to find it? »); after
that, note a failure and move on. After each task ask: « On a scale from 1
(very hard) to 7 (very easy), how easy was that? » and, when the score is 4 or
less, « What made it hard? ».

Neutral prompts: « What are you looking for? » « What do you expect to happen
if you do that? » « What does this tell you? » Avoid naming the controls (say
« the change », not « the Merge button »).

**Debrief (5 minutes).** « What did you like? What annoyed you? Was anything
missing? If you could change one thing, what would it be? Would you trust the
undo and the draft to keep your work? » Thank the participant.

**After the session (10 minutes).** Complete the template while it is fresh;
copy `decisions/history/themes.json/` of the participant's project if their
path matters (it lists every saved version and its action).

## Consent

> You are invited to try a new screen of cartolex, a research tool that maps a
> research field from its publications. The session lasts about 45 minutes.
>
> - You use the screen on invented data: no real person, text or project
>   appears, and nothing you do affects real data.
> - We take notes on what you do and say. With your agreement, we also record
>   the screen and your voice; the recording is used only by the team to
>   improve the tool, is kept for at most six months and is never published.
> - Quotes may appear in the team's findings without your name.
> - Taking part is voluntary. You may skip a task, stop at any time, or ask us
>   to delete your notes and recording later, without giving a reason.
>
> ☐ I agree to take part. ☐ I agree to the screen and voice recording.
>
> Name, date, signature:

## Findings template

One page per participant (copy the table for each), then one summary.

**Participant** P… · role (curator, researcher, other) · experience with such
tools (none, some, a lot) · language · date · moderator · note-taker

| task | success (✓ / partial / ✗) | time (min:s) | errors (count, what) | ease (1–7) | quotes | issues seen |
| --- | --- | --- | --- | --- | --- | --- |
| 1 rename | | | | | | |
| 2 move (menu, drag) | | | | | | |
| 3 merge | | | | | | |
| 4 split | | | | | | |
| 5 To check queue | | | | | | |
| 6 save, apply, map | | | | | | |

**Issues.** One line each: where (screen area, control), what happened, how
many participants met it, and its **severity**:

| severity | meaning |
| --- | --- |
| 0 | not a problem |
| 1 | cosmetic: fix when there is time |
| 2 | minor: slows people down, they recover alone |
| 3 | major: people fail or need help; fix before the next screens |
| 4 | blocking: data loss, or the task cannot be done; fix before any release |

**Summary.** Success rate and median time per task, the median ease per task,
the issues sorted by severity then by how many participants met them, and the
three changes the team makes first.

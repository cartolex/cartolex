# Shape the themes

**Goal**: turn the grouping's proposal into themes a specialist of the field
would recognise: name them, move keywords that landed in the wrong place,
merge and split, add a level of topics, then save and apply them to the map.

**You need**: a built project, ideally with its keywords cleaned
({doc}`tutorial-keywords`): the cleaner the keywords, the better the
proposal.

**It takes**: half an hour for a first pass on a small field; the editor
keeps a draft, so you can stop and come back.

## How the themes are made

The build groups the keywords that the same people use together into a
**tree**: one to four **levels** (themes, then topics inside them, for
instance). Each keyword sits on a node, and a keyword's use counts toward its
node and every node above it. The tree the build proposes is only a
proposal: it becomes *your* tree when you save it, and your tree is kept over
later proposals.

## Steps

### 1. Open the editor

Open **Themes** in the navigation.

```{image} images/themes-editor.png
:alt: The theme editor: the outline of the tree on the left, the treemap in the middle, and the selected theme in the panel on the right with its keywords and the people who weigh most on it.
```

- On the left, the **Outline**: every node with its number of keywords and
  its share of all use. **Search keywords and themes** (or the / key) finds a
  keyword in the whole tree. The tabs **Set aside** and **To check** hold the
  keywords on no theme and those waiting for a decision.
- In the middle, the **Treemap** (each theme as large as its use), the
  **Map** of people and keywords, and a **Playground**.
- On the right, the **panel** of what you selected: its keywords, the people
  who weigh most on it, its names in each language, and its actions.

The header says where you are: « The grouping's proposal, not saved yet »,
then « N unsaved changes », then « Saved ». **Undo** and **Redo** (the arrows,
or Ctrl-Z and Ctrl-Shift-Z) go back as far as you want.

### 2. Name the themes

Select a theme and press **Rename** (or F2). Give it a name a researcher of
the field would recognise, in each language of the project: the map, the
treemap and the shared site use these names. Look at **Its most used
keywords** and **The people who weigh most on it** to decide what the theme
is about.

### 3. Move, merge, split

- **Move** a keyword that landed in the wrong theme: select it (in the panel
  or the outline), **Move to…**, choose the theme. Dragging works too.
- **Merge** two themes that are one subject: select one, **Merge with…**,
  choose the other.
- **Split** a theme that holds two subjects: **More › Split…**, tick what goes
  to the new theme, name it.
- **New node**: a theme of your own, then move keywords into it.
- **Set aside** a keyword that is no subject of the field (too general, a
  broken phrase): it stays in the project on no theme, with its reason, and
  **Put back** returns it.

Every action is in a menu and has a key: Shift+F10 (or the Menu key) opens the
actions of what is selected in the outline, F2 renames, Delete deletes an
empty node, Ctrl+S saves.

### 4. Add a level

**Levels › Insert a level…** adds a level above, between or below the
current ones (topics inside themes, for instance); **Levels › Rename the
level…** names it (« Theme », « Topic »), and **Levels › Remove a level…**
dissolves one into its parent. Up to four levels.

### 5. Save and apply

- **Save the proposal** (later **Save**) keeps your tree as a new **version**;
  **More › Versions…** reads, compares and restores earlier ones.
- **Save and apply** saves, then builds the themes and the map again in the
  background: « Applying the themes and drawing the map. You can keep editing
  meanwhile. » When it ends, the people's shares and the map follow your
  themes.

**You should see**, on the **Map**, your theme names in the treemap and the
legend, and each person in the colour of their main theme.

## With an AI copilot

**Curate with AI** gives the tree to an assistant that can run code, as for
the keywords ({doc}`tutorial-keywords`): the bundle holds the tree, the
keywords' space and each person's usage as numbered rows (never a name or a
text). The assistant studies the tree with cartolex's measures (coherence,
margins, keywords on a border), proposes renames, moves, merges and splits,
and explains each; you review them change by change, accept a whole kind at
once, and **Apply and save**.

## If something is not right

- **A banner says the vocabulary changed**: after a new build of the
  keywords, new keywords wait in **To check** with a proposed place: accept
  (A), move (M) or set aside (S) each.
- **« A new proposal of the grouping »**: a change of the grouping's settings
  proposes another tree; **Adopt the proposal** or **Keep my tree**.
- **You closed the browser with unsaved changes**: the draft is kept in the
  browser and restored when you come back.
- **The tree was saved elsewhere** (another tab): your changes are applied
  again to the newer tree, and those that no longer apply are listed.

The settings of the grouping (how many themes, how many levels) are under
**Tune the space and the grouping**; {doc}`build` says how a change is
rebuilt.

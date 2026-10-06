# How to cite cartolex

If cartolex helped your work (a map in a report, a figure in an article, a
site you shared), please cite it, with the version you used. Its authors are
Elisa Klüger and Pierre Ronceray.

These pages belong to version **{{ version }}**. The version of the app you
run is on its About page (the logo at the top left of every screen) and at
the foot of the settings menu; the About page also gives the citation of that
version.

## A reference

> Klüger, E., & Ronceray, P. ({{ year }}). *cartolex* (version {{ version }})
> [Computer software]. <https://github.com/cartolex/cartolex>

## BibTeX

{{ bibtex }}

## For reference managers

The source of cartolex carries two files that reference managers and
repositories read:

- `CITATION.cff`, at the root of the source, the Citation File Format: GitHub
  shows it as « Cite this repository », and Zotero imports it;
- `codemeta.json`, the same description in the CodeMeta vocabulary, for
  software repositories and archives.

Both name the authors with their ORCID iDs, the licence (MIT), the repository
and a short abstract.

## Citing the method

A map made with cartolex rests on methods older than it: co-word analysis,
TF-IDF weighting, latent semantic analysis, Ward's clustering, UMAP and
t-SNE. {doc}`about` lists them with their references, to cite beside
cartolex when you describe how a map was made. The **Recipe** tab of the Build
screen gives every setting of your build, ready to copy into a methods
section.

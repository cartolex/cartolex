# How to cite cartolex

If cartolex helped your work (a map in a report, a figure in an article, a
site you shared), please cite it. Its authors are Elisa Klüger and Pierre
Ronceray.

**cartolex is archived on Zenodo, one record per version, each with its DOI.**
Cite the record of the version you used: its page gives the reference in the
common styles (« Cite as ») and exports it to reference managers. Zenodo also
gives a DOI that always leads to the latest version,
[10.5281/zenodo.23223331](https://doi.org/10.5281/zenodo.23223331): it lists
every version's record; use it alone only to refer to cartolex in general.

These pages belong to version **{{ version }}**. The version of the app you
run is on its About page (the logo at the top left of every screen) and at
the foot of the settings menu; the About page also gives the citation of that
version.

## A reference

> Klüger, E., & Ronceray, P. ({{ year }}). *cartolex* (version {{ version }})
> [Computer software]. Zenodo.

followed by the DOI of that version's record (its Zenodo page, reached from
the DOI above).

## BibTeX

{{ bibtex }}

## For reference managers

The source of cartolex carries two files that reference managers and
repositories read, and that Zenodo uses for its records:

- `CITATION.cff`, at the root of the source, the Citation File Format: GitHub
  shows it as « Cite this repository », and Zotero imports it;
- `codemeta.json`, the same description in the CodeMeta vocabulary.

Both name the authors with their ORCID iDs, the licence (MIT), the repository
and a short abstract.

## Citing the method

A map made with cartolex rests on older methods: TF-IDF weighting, latent
semantic analysis, Ward's clustering, UMAP or t-SNE. {doc}`references` lists
them with the step each one shapes, to cite beside cartolex when you describe
how a map was made. The **Recipe** tab of the Build screen gives every setting
of your build, ready to copy into a methods section.

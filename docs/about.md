# About cartolex

cartolex maps a body of texts by the words of their authors. It finds the
vocabulary they actually use (no list of keywords is given to it), places
people, organisations and texts in a lexical space, groups that vocabulary
into themes and draws it all as an atlas you can explore and share. In the
app, the logo at the top left opens the same page.

## Who talks the same

The heart of the method is a distance. Two authors are close in the lexical
space when they write about their subject with the same words: cartolex
measures how much people, teams or institutions talk the same, whether or not
they work together; the co-authorship network, drawn on the same map, shows
who does. The map shows what people write about, never how good their work
is.

It was made for research fields, from a laboratory to a discipline. Any texts
whose authors are known will do, though: the articles of journalists, for
instance.

## How a map is made

1. **Texts**, collected from open services (OpenAlex first) or imported.
2. **Keywords**: the noun phrases of the texts, found by spaCy's language
   models and scored by how specific they are (TF-IDF); you, or an AI, keep the
   field's own.
3. **Space**: a truncated singular value decomposition of the authors ×
   keywords matrix (latent semantic analysis), the space where the distances
   are measured.
4. **Themes**: the keywords grouped into a tree by Ward's clustering; you can
   edit it.
5. **Map**: the space drawn in two dimensions (UMAP or t-SNE), with the
   co-authorship network.

Everything runs on your computer; only the collection of texts and the
optional AI clean-up use the network, and they say so first ({doc}`privacy`).

## Key references

- Sparck Jones, K. (1972). A statistical interpretation of term specificity and
  its application in retrieval. *Journal of Documentation*, 28(1), 11–21.
  [doi:10.1108/eb026526](https://doi.org/10.1108/eb026526)
- Deerwester, S., Dumais, S. T., Furnas, G. W., Landauer, T. K., & Harshman, R.
  (1990). Indexing by latent semantic analysis. *Journal of the American
  Society for Information Science*, 41(6), 391–407.
  [doi:10.1002/(SICI)1097-4571(199009)41:6<391::AID-ASI1>3.0.CO;2-9](https://doi.org/10.1002/%28SICI%291097-4571%28199009%2941:6%3C391::AID-ASI1%3E3.0.CO;2-9)
- Ward, J. H., Jr. (1963). Hierarchical grouping to optimize an objective
  function. *Journal of the American Statistical Association*, 58(301),
  236–244. [doi:10.1080/01621459.1963.10500845](https://doi.org/10.1080/01621459.1963.10500845)

Every method and library cartolex relies on, step by step: {doc}`references`.

## Built on

spaCy's language models find the noun phrases; scikit-learn, SciPy, NumPy,
umap-learn and openTSNE build the space, the themes and the map; OpenAlex and
other open services provide the texts.

## Authors, citation, licence

cartolex is written by Elisa Klüger and Pierre Ronceray. It is archived on
Zenodo: cite the record of the version you used ({doc}`cite`). It is free
software under the MIT licence.

The About page of the app and the foot of its settings menu show the version
and the build (the commit and its date), as in `1.0.0 · 3f2a1c9 · 2026-10-06`.

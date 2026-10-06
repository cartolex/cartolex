# About cartolex

cartolex maps a research field from the texts of the people who work in it:
the keywords of the field, its themes, and a map on which people,
organisations and texts sit near what they write about. In the app, the logo
at the top left opens this page.

## What it is for

To see the shape of a field at a glance and to discuss it: which themes it is
made of and how they relate, who works on what, which organisations hold which
subjects, how the field changed over the years. The map is a starting point for
a conversation among people who know the field, not a ranking: it shows what
people write about, never how good their work is.

Everything runs on your computer. The texts are collected from open
bibliographic services or come from your own files; the optional AI clean-up of
the keywords sends keyword strings and the field's title, never texts or people
(see {doc}`privacy`). The result can be shared as an offline site.

## How a map is made

Each step is a stage of the build, with its own page and settings; a build runs
only the steps whose inputs changed (see {doc}`build`).

1. **Texts**: the people of the field and their texts, collected from open
   bibliographic services or imported (see {doc}`collection`).
2. **Keywords**: the noun phrases of the texts, scored by how specific they are
   to some people (see {doc}`keywords`).
3. **Space**: the weights of the keywords for each person (TF-IDF), reduced by a
   truncated singular value decomposition.
4. **Themes**: the keywords grouped into a tree by Ward's clustering in that
   space; the tree can be edited by hand.
5. **Map**: the people placed in two dimensions near the people who write about
   the same things, with UMAP, t-SNE or from the tree of themes.
6. **Share**: an offline site, figures and tables.

## Scientific background

*A draft for the authors: the methods cartolex builds on, to be completed and
checked.* Each DOI links to its resolver; in the app, these links and the
source's address open in a new tab of the browser.

**Mapping a field by its words.** Co-word analysis describes a field by the
terms its texts share rather than by who cites whom; cartolex follows this
idea, with terms found in the texts themselves, never taken from a thesaurus.

- Callon, M., Courtial, J.-P., Turner, W. A., & Bauin, S. (1983). From
  translations to problematic networks: An introduction to co-word analysis.
  *Social Science Information*, 22(2), 191–235. [`doi:10.1177/053901883022002003`](https://doi.org/10.1177/053901883022002003)

**Keywords and their weights.** A keyword counts when it is specific: frequent
for some people, rare for the rest of the field. The weighting follows inverse
document frequency; the candidates are noun phrases found by the language
models of spaCy.

- Sparck Jones, K. (1972). A statistical interpretation of term specificity and
  its application in retrieval. *Journal of Documentation*, 28(1), 11–21.
  [`doi:10.1108/eb026526`](https://doi.org/10.1108/eb026526)
- Salton, G., & Buckley, C. (1988). Term-weighting approaches in automatic text
  retrieval. *Information Processing & Management*, 24(5), 513–523.
  [`doi:10.1016/0306-4573(88)90021-0`](https://doi.org/10.1016/0306-4573%2888%2990021-0)
- Honnibal, M., Montani, I., Van Landeghem, S., & Boyd, A. (2020). spaCy:
  Industrial-strength natural language processing in Python. *Zenodo*.
  [`doi:10.5281/zenodo.1212303`](https://doi.org/10.5281/zenodo.1212303)

**A space of meaning.** People and keywords are placed in one space by a
truncated singular value decomposition of the weighted matrix, as in latent
semantic analysis: terms used by the same people come close even when they
never appear together.

- Deerwester, S., Dumais, S. T., Furnas, G. W., Landauer, T. K., & Harshman, R.
  (1990). Indexing by latent semantic analysis. *Journal of the American
  Society for Information Science*, 41(6), 391–407.
  [`doi:10.1002/(SICI)1097-4571(199009)41:6<391::AID-ASI1>3.0.CO;2-9`](https://doi.org/10.1002/%28SICI%291097-4571%28199009%2941:6%3C391::AID-ASI1%3E3.0.CO;2-9)

**Themes.** Keywords are grouped into a tree of themes by Ward's hierarchical
clustering in that space; the tree can then be edited by hand.

- Ward, J. H., Jr. (1963). Hierarchical grouping to optimize an objective
  function. *Journal of the American Statistical Association*, 58(301),
  236–244. [`doi:10.1080/01621459.1963.10500845`](https://doi.org/10.1080/01621459.1963.10500845)

**The map.** The map places each person in two dimensions so that people who
write about the same things are near each other, with t-SNE or UMAP, or from
the tree of themes.

- van der Maaten, L., & Hinton, G. (2008). Visualizing data using t-SNE.
  *Journal of Machine Learning Research*, 9, 2579–2605.
- McInnes, L., Healy, J., & Melville, J. (2018). UMAP: Uniform manifold
  approximation and projection for dimension reduction. *arXiv*, 1802.03426.
  [`doi:10.48550/arXiv.1802.03426`](https://doi.org/10.48550/arXiv.1802.03426)

**Science maps.** cartolex belongs to the tradition of science mapping, and to
the software built for it.

- Börner, K., Chen, C., & Boyack, K. W. (2003). Visualizing knowledge domains.
  *Annual Review of Information Science and Technology*, 37(1), 179–255.
  [`doi:10.1002/aris.1440370106`](https://doi.org/10.1002/aris.1440370106)
- Boyack, K. W., Klavans, R., & Börner, K. (2005). Mapping the backbone of
  science. *Scientometrics*, 64(3), 351–374. [`doi:10.1007/s11192-005-0255-6`](https://doi.org/10.1007/s11192-005-0255-6)
- van Eck, N. J., & Waltman, L. (2010). Software survey: VOSviewer, a computer
  program for bibliometric mapping. *Scientometrics*, 84(2), 523–538.
  [`doi:10.1007/s11192-009-0146-3`](https://doi.org/10.1007/s11192-009-0146-3)

**Sources of texts.** The texts are collected first of all from OpenAlex, an
open index of scholarly works.

- Priem, J., Piwowar, H., & Orr, R. (2022). OpenAlex: A fully-open index of
  scholarly works, authors, venues, institutions, and concepts. *arXiv*,
  2205.01833. [`doi:10.48550/arXiv.2205.01833`](https://doi.org/10.48550/arXiv.2205.01833)

## Authors and how to cite

The authors of cartolex are listed in `CITATION.cff`, at the root of its
source (<https://github.com/cartolex/cartolex>), which reference managers read. If you use cartolex in your work, please
cite it with its version: the About page of the app gives the citation of the
version you run, from the package's own metadata.

## Licence

cartolex is free software, under the MIT licence: you may use, copy, change
and share it (see the `LICENSE` file).

## Version

The About page and the foot of the settings menu show the version, then the
build: the commit the app was built from and its date
(`1.0.0 · 3f2a1c9 · 2026-10-06`). A diagnostic carries them too, so that two
builds of the same version are told apart.

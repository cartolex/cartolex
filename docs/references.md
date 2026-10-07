# References

The methods cartolex is built on, step by step, and the software that runs them.
Each entry says where cartolex uses it. When you describe how a map was made,
cite cartolex ({doc}`cite`) and the methods of the steps your map relies on; the
**Recipe** tab of the Build screen gives the settings of your build.

## The words that count

- Sparck Jones, K. (1972). A statistical interpretation of term specificity and
  its application in retrieval. *Journal of Documentation*, 28(1), 11–21.
  [doi:10.1108/eb026526](https://doi.org/10.1108/eb026526)
  — inverse document frequency: a word counts when some authors use it and the
  rest of the corpus does not. Used to score the candidate keywords and to
  weight the lexical space.
- Salton, G., & Buckley, C. (1988). Term-weighting approaches in automatic text
  retrieval. *Information Processing & Management*, 24(5), 513–523.
  [doi:10.1016/0306-4573(88)90021-0](https://doi.org/10.1016/0306-4573%2888%2990021-0)
  — TF-IDF weighting with length normalisation: the weights of the authors ×
  keywords matrix.

## The lexical space and its distances

- Deerwester, S., Dumais, S. T., Furnas, G. W., Landauer, T. K., & Harshman, R.
  (1990). Indexing by latent semantic analysis. *Journal of the American
  Society for Information Science*, 41(6), 391–407.
  [doi:10.1002/(SICI)1097-4571(199009)41:6<391::AID-ASI1>3.0.CO;2-9](https://doi.org/10.1002/%28SICI%291097-4571%28199009%2941:6%3C391::AID-ASI1%3E3.0.CO;2-9)
  — a truncated singular value decomposition of the weighted matrix puts
  authors and words in one space, where words used by the same authors come
  close. This is the space of the map, and the space in which cartolex
  measures how much two people, teams or institutions talk the same.
- Halko, N., Martinsson, P.-G., & Tropp, J. A. (2011). Finding structure with
  randomness: Probabilistic algorithms for constructing approximate matrix
  decompositions. *SIAM Review*, 53(2), 217–288.
  [doi:10.1137/090771806](https://doi.org/10.1137/090771806)
  — the randomized algorithm that computes that decomposition.
- Salton, G., Wong, A., & Yang, C.-S. (1975). A vector space model for automatic
  indexing. *Communications of the ACM*, 18(11), 613–620.
  [doi:10.1145/361219.361220](https://doi.org/10.1145/361219.361220)
  — the cosine between vectors of words: the similarities « meaning in the
  map's space » (the default) and « shared vocabulary ».
- Jaccard, P. (1912). The distribution of the flora in the alpine zone. *New
  Phytologist*, 11(2), 37–50.
  [doi:10.1111/j.1469-8137.1912.tb05611.x](https://doi.org/10.1111/j.1469-8137.1912.tb05611.x)
  — the coefficient of community: the similarity « keywords in common ».

## Themes

- Ward, J. H., Jr. (1963). Hierarchical grouping to optimize an objective
  function. *Journal of the American Statistical Association*, 58(301),
  236–244. [doi:10.1080/01621459.1963.10500845](https://doi.org/10.1080/01621459.1963.10500845)
  — the keywords are grouped into the tree of themes by Ward's criterion in
  the lexical space.
- Murtagh, F. (1983). A survey of recent advances in hierarchical clustering
  algorithms. *The Computer Journal*, 26(4), 354–359.
  [doi:10.1093/comjnl/26.4.354](https://doi.org/10.1093/comjnl/26.4.354)
  — the nearest-neighbour chain that runs Ward on large vocabularies.
- Arthur, D., & Vassilvitskii, S. (2007). k-means++: The advantages of careful
  seeding. In *Proceedings of the 18th Annual ACM-SIAM Symposium on Discrete
  Algorithms* (pp. 1027–1035), and Sculley, D. (2010). Web-scale k-means
  clustering. In *Proceedings of the 19th International Conference on World
  Wide Web* (pp. 1177–1178).
  [doi:10.1145/1772690.1772862](https://doi.org/10.1145/1772690.1772862)
  — above 15,000 keywords, mini-batch k-means with k-means++ starts first makes
  micro-clusters, which Ward then groups.

## The map

- McInnes, L., Healy, J., & Melville, J. (2018). UMAP: Uniform manifold
  approximation and projection for dimension reduction. *arXiv*, 1802.03426.
  [doi:10.48550/arXiv.1802.03426](https://doi.org/10.48550/arXiv.1802.03426)
  — draws the lexical space in two dimensions for maps of fewer than 1,000
  people, and when t-SNE is not installed.
- van der Maaten, L., & Hinton, G. (2008). Visualizing data using t-SNE.
  *Journal of Machine Learning Research*, 9, 2579–2605; van der Maaten, L.
  (2014). Accelerating t-SNE using tree-based algorithms. *Journal of Machine
  Learning Research*, 15, 3221–3245; and Linderman, G. C., Rachh, M., Hoskins,
  J. G., Steinerberger, S., & Kluger, Y. (2019). Fast interpolation-based t-SNE
  for improved visualization of single-cell RNA-seq data. *Nature Methods*,
  16(3), 243–245. [doi:10.1038/s41592-018-0308-4](https://doi.org/10.1038/s41592-018-0308-4)
  — t-SNE, which draws maps of 1,000 people or more (Barnes–Hut below 10,000
  points, interpolation above).

## The texts

- Priem, J., Piwowar, H., & Orr, R. (2022). OpenAlex: A fully-open index of
  scholarly works, authors, venues, institutions, and concepts. *arXiv*,
  2205.01833. [doi:10.48550/arXiv.2205.01833](https://doi.org/10.48550/arXiv.2205.01833)
  — the first source of people and texts. The ORCID registry, HAL, SciELO,
  Europe PMC and arXiv complete it ({doc}`collection`).

## The software it is built on

- **spaCy** finds the noun phrases, their lemmas and parts of speech, with a
  language model for each language of the texts (their licences differ:
  {doc}`install`). Honnibal, M., Montani, I., Van Landeghem, S., & Boyd, A.
  (2020). spaCy: Industrial-strength natural language processing in Python.
  *Zenodo*. [doi:10.5281/zenodo.1212303](https://doi.org/10.5281/zenodo.1212303)
- **scikit-learn** weights the words (TF-IDF), decomposes the matrix and makes
  the micro-clusters. Pedregosa, F., et al. (2011). Scikit-learn: Machine
  learning in Python. *Journal of Machine Learning Research*, 12, 2825–2830.
- **SciPy** runs Ward's clustering and the sparse matrices. Virtanen, P., et al.
  (2020). SciPy 1.0: Fundamental algorithms for scientific computing in Python.
  *Nature Methods*, 17(3), 261–272.
  [doi:10.1038/s41592-019-0686-2](https://doi.org/10.1038/s41592-019-0686-2)
- **NumPy** holds every array. Harris, C. R., et al. (2020). Array programming
  with NumPy. *Nature*, 585(7825), 357–362.
  [doi:10.1038/s41586-020-2649-2](https://doi.org/10.1038/s41586-020-2649-2)
- **umap-learn** draws UMAP maps. McInnes, L., Healy, J., Saul, N., &
  Großberger, L. (2018). UMAP: Uniform Manifold Approximation and Projection.
  *Journal of Open Source Software*, 3(29), 861.
  [doi:10.21105/joss.00861](https://doi.org/10.21105/joss.00861)
- **openTSNE** (optional) draws t-SNE maps. Poličar, P. G., Stražar, M., &
  Zupan, B. (2024). openTSNE: A modular Python library for t-SNE
  dimensionality reduction and embedding. *Journal of Statistical Software*,
  109(3). [doi:10.18637/jss.v109.i03](https://doi.org/10.18637/jss.v109.i03)

Also: pandas and PyArrow (the tables), langdetect (the language of each text),
pypdf and pdfminer.six (the text of PDF files), FastAPI and Uvicorn (the app's
server), Preact and htm (its interface), matplotlib (figures), wordcloud (the
word cloud) and, for the AI clean-up by API, Mistral's client library.

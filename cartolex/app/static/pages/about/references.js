// SPDX-License-Identifier: MIT
/**
 * The key references of the About page: the three works the core of cartolex's method
 * comes from (the words that count, the lexical space, the themes). Bibliographic records,
 * not interface text: shown as written in every interface language, each DOI a link to its
 * resolver. Every method and library cartolex relies on is in the documentation's
 * References page (docs/references.md).
 */

export const KEY_REFERENCES = [
  {
    authors: 'Sparck Jones, K.',
    year: 1972,
    title: 'A statistical interpretation of term specificity and its application in retrieval',
    venue: 'Journal of Documentation',
    details: '28(1), 11–21',
    doi: '10.1108/eb026526',
  },
  {
    authors: 'Deerwester, S., Dumais, S. T., Furnas, G. W., Landauer, T. K., & Harshman, R.',
    year: 1990,
    title: 'Indexing by latent semantic analysis',
    venue: 'Journal of the American Society for Information Science',
    details: '41(6), 391–407',
    doi: '10.1002/(SICI)1097-4571(199009)41:6<391::AID-ASI1>3.0.CO;2-9',
  },
  {
    authors: 'Ward, J. H., Jr.',
    year: 1963,
    title: 'Hierarchical grouping to optimize an objective function',
    venue: 'Journal of the American Statistical Association',
    details: '58(301), 236–244',
    doi: '10.1080/01621459.1963.10500845',
  },
];

/** The address of a DOI at its resolver. */
export function doiLink(doi) {
  return `https://doi.org/${encodeURI(doi)}`;
}

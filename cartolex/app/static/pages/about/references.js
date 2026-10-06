// SPDX-License-Identifier: MIT
/**
 * The scientific background of the About page: the works cartolex's methods come
 * from, grouped by the step they shape. Bibliographic records, not interface text:
 * they are shown as written, in every interface language, their DOI as text (the app
 * links nowhere outside this computer). A draft for the authors (docs/about.md holds the
 * same list).
 */

/** The groups, in the order of the pipeline: `id` names the group's heading and text. */
export const REFERENCE_GROUPS = [
  {
    id: 'coword',
    refs: [
      {
        authors: 'Callon, M., Courtial, J.-P., Turner, W. A., & Bauin, S.',
        year: 1983,
        title: 'From translations to problematic networks: An introduction to co-word analysis',
        venue: 'Social Science Information',
        details: '22(2), 191–235',
        doi: '10.1177/053901883022002003',
      },
    ],
  },
  {
    id: 'weighting',
    refs: [
      {
        authors: 'Sparck Jones, K.',
        year: 1972,
        title: 'A statistical interpretation of term specificity and its application in retrieval',
        venue: 'Journal of Documentation',
        details: '28(1), 11–21',
        doi: '10.1108/eb026526',
      },
      {
        authors: 'Salton, G., & Buckley, C.',
        year: 1988,
        title: 'Term-weighting approaches in automatic text retrieval',
        venue: 'Information Processing & Management',
        details: '24(5), 513–523',
        doi: '10.1016/0306-4573(88)90021-0',
      },
      {
        authors: 'Honnibal, M., Montani, I., Van Landeghem, S., & Boyd, A.',
        year: 2020,
        title: 'spaCy: Industrial-strength natural language processing in Python',
        venue: 'Zenodo',
        details: '',
        doi: '10.5281/zenodo.1212303',
      },
    ],
  },
  {
    id: 'space',
    refs: [
      {
        authors: 'Deerwester, S., Dumais, S. T., Furnas, G. W., Landauer, T. K., & Harshman, R.',
        year: 1990,
        title: 'Indexing by latent semantic analysis',
        venue: 'Journal of the American Society for Information Science',
        details: '41(6), 391–407',
        doi: '10.1002/(SICI)1097-4571(199009)41:6<391::AID-ASI1>3.0.CO;2-9',
      },
    ],
  },
  {
    id: 'themes',
    refs: [
      {
        authors: 'Ward, J. H., Jr.',
        year: 1963,
        title: 'Hierarchical grouping to optimize an objective function',
        venue: 'Journal of the American Statistical Association',
        details: '58(301), 236–244',
        doi: '10.1080/01621459.1963.10500845',
      },
    ],
  },
  {
    id: 'layout',
    refs: [
      {
        authors: 'van der Maaten, L., & Hinton, G.',
        year: 2008,
        title: 'Visualizing data using t-SNE',
        venue: 'Journal of Machine Learning Research',
        details: '9, 2579–2605',
        doi: '',
      },
      {
        authors: 'McInnes, L., Healy, J., & Melville, J.',
        year: 2018,
        title: 'UMAP: Uniform manifold approximation and projection for dimension reduction',
        venue: 'arXiv',
        details: '1802.03426',
        doi: '10.48550/arXiv.1802.03426',
      },
    ],
  },
  {
    id: 'maps',
    refs: [
      {
        authors: 'Börner, K., Chen, C., & Boyack, K. W.',
        year: 2003,
        title: 'Visualizing knowledge domains',
        venue: 'Annual Review of Information Science and Technology',
        details: '37(1), 179–255',
        doi: '10.1002/aris.1440370106',
      },
      {
        authors: 'Boyack, K. W., Klavans, R., & Börner, K.',
        year: 2005,
        title: 'Mapping the backbone of science',
        venue: 'Scientometrics',
        details: '64(3), 351–374',
        doi: '10.1007/s11192-005-0255-6',
      },
      {
        authors: 'van Eck, N. J., & Waltman, L.',
        year: 2010,
        title: 'Software survey: VOSviewer, a computer program for bibliometric mapping',
        venue: 'Scientometrics',
        details: '84(2), 523–538',
        doi: '10.1007/s11192-009-0146-3',
      },
    ],
  },
  {
    id: 'sources',
    refs: [
      {
        authors: 'Priem, J., Piwowar, H., & Orr, R.',
        year: 2022,
        title: 'OpenAlex: A fully-open index of scholarly works, authors, venues, institutions, and concepts',
        venue: 'arXiv',
        details: '2205.01833',
        doi: '10.48550/arXiv.2205.01833',
      },
    ],
  },
];

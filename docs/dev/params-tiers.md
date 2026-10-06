# The tiers of the parameters

Every parameter of the build has a tier that says how prominent it is on the
screens; it changes nothing else. A page's « Tune » panel shows its **essential** parameters
first: the few a curator turns. **Intermediate** ones are folded under
« More », **advanced** ones under « Advanced ». The assignment is one table,
`PARAM_TIERS` in `cartolex/build/stages.py`, which must list every parameter of
every stage once (the registry refuses to load otherwise); the layout
parameters of a map version carry theirs in `LAYOUT_DEFAULTS`
(`cartolex/app/method.py`).

This table was approved by the owner on 1 October 2026.

## The build's parameters

| stage | parameter | tier | reason |
| --- | --- | --- | --- |
| `corpus.assemble` | `parts` | essential | which parts of a text are read decides what the keywords come from |
| `corpus.assemble` | `recency_years` | essential | the year window of the keywords, the first question of a field's map |
| `corpus.assemble` | `doc_types` | intermediate | the defaults read texts and skip datasets and software; changed for an unusual corpus |
| `corpus.assemble` | `provider_priority` | advanced | only matters when several providers give the same part |
| `corpus.assemble` | `duplicate_min_title` | advanced | an internal threshold of the same-work detection |
| `corpus.assemble` | `duplicate_year_gap` | advanced | an internal threshold of the same-work detection |
| `keywords.extract` | `min_people` | essential | the main filter: how widely a keyword must be used |
| `keywords.extract` | `min_texts` | essential | the other main filter: in how many texts |
| `keywords.extract` | `counting_unit` | intermediate | a real choice (people, texts, organisations), but the default fits most fields |
| `keywords.extract` | `max_share` | intermediate | the generality cut; rarely turned |
| `keywords.extract` | `rejects` | intermediate | switching the rejection lists off is occasional |
| `keywords.extract` | `max_words` | intermediate | the length of the phrases; understandable, rarely changed |
| `keywords.extract` | `length_bonus` | intermediate | favours longer phrases; a visible effect, rarely changed |
| `keywords.extract` | `max_candidates` | advanced | a cap for very large corpora |
| `keywords.extract` | `vote` | advanced | a scoring internal |
| `keywords.extract` | `of_complement` | advanced | a grammar rule of English phrases |
| `keywords.extract` | `fragment_share` | advanced | a rule of the keyword filters (bands) |
| `keywords.extract` | `drop_share` | advanced | a rule of the keyword filters (bands) |
| `keywords.extract` | `keep_share` | advanced | a rule of the keyword filters (bands) |
| `keywords.extract` | `name_share` | advanced | a rule of the keyword filters (bands) |
| `keywords.extract` | `stop_words` | advanced | a rule of the keyword filters (closed words) |
| `keywords.extract` | `closed_word_edges` | advanced | a rule of the keyword filters (closed words) |
| `keywords.extract` | `foreign_reading` | advanced | a rule of the keyword filters (closed words) |
| `keywords.extract` | `even_spread` | advanced | a rule of the keyword filters (generic words) |
| `keywords.extract` | `even_people` | advanced | a rule of the keyword filters (generic words) |
| `keywords.extract` | `common_modifier` | advanced | a rule of the keyword filters (generic words) |
| `keywords.triage` | `enabled` | intermediate | the AI clean-up is switched on from the keywords screen; shown here for completeness |
| `keywords.build` | `max_keywords` | essential | the size of the vocabulary |
| `keywords.build` | `keywords_per_person` | intermediate | what the space and the map are made of; the default fits most fields |
| `keywords.build` | `keywords_per_organisation` | intermediate | the profiles of organisations |
| `keywords.build` | `keywords_of_field` | intermediate | the profile of the whole field |
| `keywords.build` | `weights_basis` | intermediate | a real choice of how themes weigh a person's keywords |
| `keywords.build` | `nested_threshold` | advanced | an internal of the vocabulary's nesting |
| `keywords.build` | `ngram_range` | advanced | the counting of forms; follows the longest phrase anyway |
| `themes.space` | `space_unit` | essential | people or texts: what "near" means for keywords |
| `themes.space` | `dimensions` | intermediate | the resolution of the space; a rule sets it from the project size |
| `themes.space` | `svd_seed` | advanced | an SVD internal |
| `themes.space` | `svd_iterations` | advanced | an SVD internal |
| `themes.space` | `svd_algorithm` | advanced | an SVD internal |
| `themes.group` | `depth` | essential | how many levels of themes |
| `themes.group` | `level_sizes` | essential | the size of each level, when set by hand |
| `themes.group` | `comb` | essential | the comb on or off: keywords on the level their texts support |
| `themes.group` | `top_groups` | intermediate | the size of the top level when the sizes are computed |
| `themes.group` | `keywords_per_group` | intermediate | the size of the finest groups when the sizes are computed |
| `themes.group` | `comb_theta` | intermediate | the comb's one threshold, calibrated by default |
| `themes.group` | `cluster_dimensions` | advanced | a clustering internal |
| `themes.group` | `exact_ward_limit` | advanced | a memory limit of the clustering |
| `themes.group` | `micro_clusters` | advanced | a clustering internal |
| `themes.group` | `micro_seed` | advanced | a clustering internal |
| `themes.group` | `comb_grid` | advanced | the comb's calibration grid |
| `themes.group` | `comb_theta_one_level` | advanced | the comb on a tree of one level |
| `themes.group` | `comb_min_texts` | advanced | the comb's evidence floor |
| `themes.group` | `comb_max_cells` | advanced | a memory limit of the comb |
| `themes.group` | `own_name_floor` | advanced | a naming internal |
| `map.layout` | `neighbours` | intermediate | how keywords and projected people are placed; the default fits most maps |
| `map.layout` | `link_radius` | advanced | an internal of the placement |
| `map.trajectories` | `window_years` | essential | the time window of the trajectories |
| `map.trajectories` | `min_texts_per_window` | intermediate | which windows are placed |
| `map.trajectories` | `spans` | advanced | every run of consecutive windows (`all`): the map draws the windows themselves |

The seed and the pinned year are set once for the whole build, in the texts'
« Tune » panel (the corpus screen's Texts tab). Every parameter also has a
short label in the interface's catalogues (`param.label.<stage>.<name>`, and
`param.label.layout.<name>` for a map version's), shown with its code name.

## The map's layout (a map version, `maps.json`)

The map's « Tune » panel shows them beside `map.layout`'s parameters; they are saved as a new
map version, pinned, not in `params.json`.

| method | parameter | tier | reason |
| --- | --- | --- | --- |
| all | the method | essential | UMAP, t-SNE or the theme tree: the look of the map |
| all | the seed | essential | draws another map with the same settings |
| `umap` | `n_neighbors` | essential | local detail against the broad picture |
| `umap` | `min_dist` | essential | tight clusters against an even spread |
| `umap` | `metric`, `n_epochs`, `spread`, `set_op_mix_ratio`, `local_connectivity`, `repulsion_strength`, `negative_sample_rate` | advanced | UMAP internals |
| `tsne` | `perplexity` | essential | the one knob of t-SNE |
| `tsne` | `metric` | advanced | the distance; cosine fits the space |
| `tree` | `fill` | essential | how much of each theme's area its people fill |
| `tree` | `gap`, `lean` | intermediate | the spacing of themes and how people lean to their other themes |
| `tree` | `sharp` | advanced | an internal of the lean |

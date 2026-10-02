# The index of an OpenAlex snapshot

A downloaded OpenAlex snapshot can be indexed once per release
(`cartolex collect snapshot-index SNAPSHOT`, {doc}`../collection`), so that a
collection reads only the parts of it that may hold what it asks for. The index
lives beside the data, in the snapshot's folder; it is not part of a project.

## What indexing changes

Each part OpenAlex ships (`data/jsonl/<entity>/updated_date=…/part_….gz`) is one
gzip stream. Indexing cuts every part of the **works** and the **authors** into
gzip *members* of about a megabyte of whole lines each (compressed at level 6,
the size of the original): the file is still a gzip of the same JSON lines, in
the same order, and any tool reads it as before. Its size, no longer the one in
OpenAlex's manifests, is recorded in the index. The institutions are not cut:
they are small and read whole.

## The files

```text
<snapshot>/cartolex-index/
  index.json                    the release, the parts, the keys (below)
  works/blocks.npy              each member's offset and length (structured: offset u8, length u4)
  works/first.npy               the number of each part's first member (u8, one more than parts)
  works/<key>/NN.keys.npy       bucket NN of a key (key modulo 64): the keys, sorted (u8)
  works/<key>/NN.refs.npy       beside each key, a member that holds it (u4: part << 20 | member)
  authors/…                     the same, for the authors
  building.json, journal.jsonl  while a build runs or after it stopped (gone once complete)
  spill/                        the postings found so far, while building
```

The keys of the works are `id` (the record's own id), `author` (every author id
in its line), `institution` (every institution id in its line, lineages
included) and `doi` (every DOI in its line, as the first 8 bytes of its BLAKE2b
hash in lower case); the authors have `id`. Numbers are the digits of an id
(`A5012345678` → 5012345678). Parts are numbered in the order of their
`updated_date=…/part_….gz` names, the members of a part in file order.

`index.json`:

| Field | Meaning |
| --- | --- |
| `format` | `cartolex-snapshot-index/1` |
| `release` | the snapshot's release date (from its manifest) |
| `built_at` | when the build ended (UTC) |
| `block_bytes`, `level`, `member_bits`, `buckets` | the settings of the build |
| `entities.<entity>.parts` | each part: `path`, `source_size` (OpenAlex's), `size` (cut), `members` |
| `entities.<entity>.members`, `.bytes` | the members of the entity, their compressed bytes |
| `entities.<entity>.keys.<key>` | `postings`, `distinct` keys, `members_per_key` (`p50`, `p90`, `p99`), used by estimates |
| `complete` | `true`: every part is cut and every bucket sorted |

## Reading

A query by ids, authors, institutions or DOIs looks its keys up in their buckets,
gathers the members that hold one, and reads those members of those parts only
(members close together in one read); each line is then tested exactly as a
reading of the whole part tests it, so the records found are the same. A search
by name, by ROR id or of everything reads whole parts. An index of another
release, an incomplete one, or one whose parts changed size since (a part
downloaded again) is not used: the queries read whole parts.

## Building, stopping, resuming

A build cuts the parts in worker processes. Each new copy is written beside its
part (`….gz.cutting`), synced, then put in its place. Every 16 parts, everything
written is synced and the journal marks the point: a build stopped at any moment
goes on from the last such point (parts cut after it are cut again; cutting a
part already cut gives the same members). When every part is cut, each bucket of
postings is sorted, `index.json` is written and the journal is removed.

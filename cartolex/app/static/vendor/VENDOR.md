# Vendored front-end libraries

Written by `tools/vendor_ui.py`; do not edit by hand. Each file is the
package's published file, taken from the npm registry archive after checking
the archive's SHA-512 integrity. ES-module files have their bare import
specifiers rewritten to relative paths and their source-map comment removed;
nothing else changes. `upstream` is the SHA-256 of the published file,
`vendored` the SHA-256 of the file here.

| file | package | version | licence | source | upstream SHA-256 | vendored SHA-256 |
| --- | --- | --- | --- | --- | --- | --- |
| `preact/preact.module.js` | preact | 10.29.8 | MIT | https://registry.npmjs.org/preact/-/preact-10.29.8.tgz#dist/preact.module.js | `c30e721ebfdc6e2ad4c18c14d2dfb82667829c8aec27de1207774e3fc16858a8` | `25a5df7e9f628a587743c4641a368737a3b218f28ef738ba0376a2d5bdfc948c` |
| `preact/hooks.module.js` | preact | 10.29.8 | MIT | https://registry.npmjs.org/preact/-/preact-10.29.8.tgz#hooks/dist/hooks.module.js | `a6ee626f2d01570592dd569a792e3f050154aa02890eead8c223fa3ed5aa3d5a` | `9e7eff58e0ae604583461eba1344da69a6894eab575eb591c635cc0d3f9ec57d` |
| `preact/LICENSE` | preact | 10.29.8 | MIT | https://registry.npmjs.org/preact/-/preact-10.29.8.tgz#LICENSE | `1fe6958409c8c257a70c587a18b6f7f412b179b456630790d30b2ec9a8e4b7d4` | `1fe6958409c8c257a70c587a18b6f7f412b179b456630790d30b2ec9a8e4b7d4` |
| `htm/htm.module.js` | htm | 3.1.1 | Apache-2.0 | https://registry.npmjs.org/htm/-/htm-3.1.1.tgz#dist/htm.module.js | `ab33dd3f38059b9be4d5f5350128eefb2356639c4e0bbe9d9e8b3ba75847e9e4` | `ab33dd3f38059b9be4d5f5350128eefb2356639c4e0bbe9d9e8b3ba75847e9e4` |
| `htm/LICENSE` | htm | 3.1.1 | Apache-2.0 | https://registry.npmjs.org/htm/-/htm-3.1.1.tgz#LICENSE | `740725f7252e750af735d0028cc534970772f513331e9f68150fede8fb3ce00f` | `740725f7252e750af735d0028cc534970772f513331e9f68150fede8fb3ce00f` |
| `signals-core/signals-core.module.js` | @preact/signals-core | 1.14.4 | MIT | https://registry.npmjs.org/@preact/signals-core/-/signals-core-1.14.4.tgz#dist/signals-core.module.js | `bfbb64b74f7f06a4f7c6f6bb854cccb40d03f1e96305d43c41876cba581ea112` | `09550c9fea0ab36b1c12e9ff0bdc2a26ed310dd5a386197371f3677f44dae63a` |
| `signals-core/LICENSE` | @preact/signals-core | 1.14.4 | MIT | https://registry.npmjs.org/@preact/signals-core/-/signals-core-1.14.4.tgz#LICENSE | `a11fc89e4c6b118854c7a667734a0b2e6bf2af5e45c6686de31adbccc8f3ae8d` | `a11fc89e4c6b118854c7a667734a0b2e6bf2af5e45c6686de31adbccc8f3ae8d` |
| `signals/signals.module.js` | @preact/signals | 2.11.2 | MIT | https://registry.npmjs.org/@preact/signals/-/signals-2.11.2.tgz#dist/signals.module.js | `68bfef67ac50f6bd7d34ec62ac555205c459a6992824be43cb9688dba8baef5c` | `6269a35a60b7de659c126ca22b0b4e7f898e01b5cca261dc3a350a153776f5cd` |
| `signals/LICENSE` | @preact/signals | 2.11.2 | MIT | https://registry.npmjs.org/@preact/signals/-/signals-2.11.2.tgz#LICENSE | `a11fc89e4c6b118854c7a667734a0b2e6bf2af5e45c6686de31adbccc8f3ae8d` | `a11fc89e4c6b118854c7a667734a0b2e6bf2af5e45c6686de31adbccc8f3ae8d` |

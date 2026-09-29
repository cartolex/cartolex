#!/bin/bash
# SPDX-License-Identifier: MIT
# macOS: double-click (the first time: right-click › Open) to install or update cartolex.
# The work is done by install-cartolex.sh, next to this file.
cd "$(dirname "$0")" || exit 1
exec /bin/bash ./install-cartolex.sh

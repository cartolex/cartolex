#!/bin/bash
# SPDX-License-Identifier: MIT
# macOS: double-click to install or update cartolex. If macOS says it "is damaged and can't
# be opened", open Terminal, type "sh " (with the space), drag this file into the window and
# press Enter (see README-en.txt): the installer then lifts the folder's quarantine.
# The work is done by install-cartolex.sh, next to this file.
cd "$(dirname "$0")" || exit 1
exec /bin/bash ./install-cartolex.sh

#!/bin/sh
# This one command is the pre-launch baseline probe and the task-check, so
# it must pass on the clean tree and must leave the tree exactly as it found
# it (Git-ignored bytecode aside).
set -e
python3 -m unittest discover -s tests -t . -q
python3 checks/doc_titles.py

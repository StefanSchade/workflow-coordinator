#!/usr/bin/env bash
# WFC's own Run-checkout preparation hook for `wfc prepare-checkout`
# (FTR-WFC-RUN-CHECKOUT-PREPARATION, D4 Option A).
#
# This is WFC's *development* tooling, not product surface: `scripts/` is not
# packaged (see pyproject.toml), and no code path in the product knows
# anything about bundles, profiles or virtual environments. A consumer
# repository writes its own hook; this one happens to be ours.
#
# CONTRACT (owned by `wfc prepare-checkout`, see its module docstring and
# docs/backlog/FTR-WFC-RUN-CHECKOUT-PREPARATION.adoc):
#   argv:    $1 = the created checkout path, $2 = the created scratch path,
#            $3 = the base commit the checkout was created from
#   cwd:     the created checkout
#   env:     the operator's own environment plus TMPDIR=<scratch>/tmp
#   stdout:  the declaration channel ONLY -- NAME=VALUE lines, nothing else.
#            Every diagnostic goes to stderr; a stray stdout line is a named
#            refusal (`refused_hook_output`). The idiom below (fd 3 is the
#            declaration channel, stdout is redirected to stderr) makes that
#            impossible to get wrong by accident.
#   exit:    nonzero is `refused_hook_failed`; the checkout and scratch
#            directory `wfc prepare-checkout` created are then left in place
#            and reported, never removed.
#
# What it prepares: a per-checkout virtual environment in the SCRATCH
# directory (never inside the worktree, where it would dirty the tree and
# refuse launch) with an editable install of this repository from the
# verified offline bundle, and the two test-input variables the required
# suite needs in the launching environment.

set -euo pipefail

checkout="${1:?missing checkout path}"
scratch="${2:?missing scratch path}"
base_commit="${3:?missing base commit}"

exec 3>&1   # fd 3: the declaration channel.
exec 1>&2   # everything any command prints on stdout becomes a diagnostic.

profile="cp313-linux-x86_64"
default_bundle="${XDG_DATA_HOME:-${HOME}/.local/share}/wfc/offline-test-inputs/${profile}"
bundle="${WFC_TEST_INPUTS:-${default_bundle}}"

printf 'wfc hook: checkout %s (base %s)\n' "${checkout}" "${base_commit}"

if [ ! -d "${bundle}" ]; then
	printf 'wfc hook: offline input bundle not found: %s\n' "${bundle}"
	printf 'wfc hook: set WFC_TEST_INPUTS to a verified bundle, or create the documented\n'
	printf 'wfc hook: default location (see docs/developer_guide/ch01_development.adoc).\n'
	exit 1
fi
bundle="$(cd "${bundle}" && pwd)"

# The bundle is trusted only because its bytes match the profile: verify
# before anything is installed from it.
python3 scripts/wfc_test_inputs.py verify --profile "${profile}" --bundle "${bundle}"

venv="${scratch}/venv"
python3 -m venv "${venv}"
"${venv}/bin/python" -m pip install --no-index --find-links "${bundle}" -e '.[dev]'

printf 'WFC_PREPARE_PATH_PREPEND=%s\n' "${venv}/bin" >&3
printf 'WFC_TEST_INPUTS=%s\n' "${bundle}" >&3
printf 'WFC_TEST_PROFILE=%s\n' "${profile}" >&3

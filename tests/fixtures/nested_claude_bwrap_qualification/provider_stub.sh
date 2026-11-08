#!/bin/sh
# This is deliberately not a Claude replacement.  It exercises only the
# namespace, external-scratch, and AF_UNIX mechanisms a later integration
# would need before it can launch the real CLI.
set -eu

test "${TMPDIR:-}" = /tmp/s/claude-tmp
test -w /tmp/s
mkdir -p "$TMPDIR"
printf outer > "$TMPDIR/outer-marker"

# The child bwrap deliberately has a fresh /tmp.  It must receive /tmp/s
# explicitly again; inheritance of the outer mount namespace alone is not a
# substitute for the provider's own nested sandbox setup.
exec bwrap --die-with-parent --dev /dev --ro-bind /usr /usr \
    --symlink /usr/bin /bin --symlink /usr/sbin /sbin \
    --symlink /usr/lib /lib --symlink /usr/lib64 /lib64 \
    --tmpfs /tmp --bind /tmp/s /tmp/s --chdir /repo \
    --setenv TMPDIR /tmp/s/claude-tmp -- \
    /bin/sh -ceu '
        test "$TMPDIR" = /tmp/s/claude-tmp
        test -w /tmp/s
        test ! -e /tmp/outer-marker
        printf nested > "$TMPDIR/nested-marker"
    '

#!/bin/sh
# Recorded from the real command image. It never invokes a simulated terminal.
set -eu
cd /opt/wattson
printf '%s\n' '# One packaged command talks to a separate packaged service.' '$ just hello'
just --justfile Justfile.commands hello
printf '%s\n' '# Check actual socket and named-pipe bytes against a committed fixture.' '$ just check'
just --justfile Justfile.commands check
printf '%s\n' '# A failed recipe returns a nonzero status; the outer control plane records it.' '$ just fail'
set +e
just --justfile Justfile.commands fail
RESULT=$?
set -e
printf 'Observed recipe exit status: %s\n' "$RESULT"
test "$RESULT" -ne 0
printf '%s\n' '# This recording is a real command run. No AI, network, or upload was used.'

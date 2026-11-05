# How we work

Small steps, always through a pull request, and the remote is never behind.

## The flow
1. **Backlog is the issue list.** Work is an *umbrella* issue with *sub-issues*. Each sub-issue ends with an **acceptance line**: a command and the output that proves it. No acceptance line, not ready.
2. **Branch from `main`** as `type/short-name` (`feat/`, `fix/`, `docs/`, `test/`, `chore/`, `ci/`). One branch, one issue, one reviewable change.
3. **Commit in [Conventional Commits](https://www.conventionalcommits.org) style:** `feat: ...`, `fix: ...`, `docs: ...`, `test: ...`. Say why in the body. Reference the issue (`Refs #12`, or `Closes #12` when the PR finishes it).
4. **Open a pull request early.** Put the acceptance evidence in it: the commands run and their verbatim output, and what was *not* verified.
5. **CI must be green** on both architectures (the repo's `smoke` and `container-smoke` checks) before merge. If a check fails for a reason unrelated to the change, fix the cause in its own PR, not by re-running until it passes.
6. **Merge with a merge commit**, then delete the branch. Pull `main` so your copy equals `origin/main` (`git status -sb` shows no ahead/behind).
7. **Close the issue only when its acceptance line has been observed**, and quote the output in the issue.

## Rules that keep it trustworthy
- The author does not run the tests: CI or a separate agent does, and its output is what gets quoted.
- A number that was not read from an instrument is labelled an estimate and is never a pass/fail gate.
- Reuse an existing, packaged tool before writing one; if you must build, say in the issue why the existing one falls short.
- Never commit credentials. Keys are passed by environment variable name only.
- Do not put third-party data in the repo or an image unless its licence and `docs/policy.md` allow it (the CSIRO case files are fetched, never redistributed).

## Keeping local and remote in sync
```bash
git fetch --prune && git checkout main && git pull --ff-only
git status -sb          # expect: main...origin/main with no ahead/behind
git branch -a           # expect: only main (feature branches are deleted after merge)
```

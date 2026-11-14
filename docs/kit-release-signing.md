# Kit release path: ghcr.io with keyless cosign (#47)

Decision (owner, 2026-11-15): released ModelKits and agent kits live on ghcr.io under this organisation and are signed keyless through GitHub Actions OIDC.
The development path is unchanged: a local registry with key-based cosign (PRs #90 and #92, `just cosign-supply-check`). Nothing here has been published; pushing
to a registry is an outward step and needs a separate go-ahead.

## What keyless means here

There is no signing key to store. A release workflow running on GitHub asks for a short-lived certificate bound to its own identity (the workflow file and the ref)
and signs the kit digest; the signature and certificate are recorded in a transparency log. A verifier accepts a kit only if the signature's certificate names
the expected workflow identity and the GitHub OIDC issuer.

## Verification command (shape; flag names to be confirmed against the pinned cosign version before this is implemented)

    cosign verify <registry>/<repo>@sha256:<digest> \
      --certificate-identity-regexp '^https://github.com/fungible-farm/wattson/\.github/workflows/<release-workflow>\.yml@refs/(heads/main|tags/.+)$' \
      --certificate-oidc-issuer https://token.actions.githubusercontent.com

The digest, not a tag, is what is verified (as in the existing key-based check, which already shows a signed digest still verifies after a tag is overwritten).

## What has to change in the existing code

1. The init container (`scripts/kit_init_check.py`, PR #92) verifies against a mounted public key. Keyless needs a second mode that checks identity and issuer instead of a key,
   fail closed, selected by explicit configuration, never by falling back from one mode to the other.
2. Offline and air-gapped use: keyless verification normally consults the transparency log and the signing roots over the network. The offline bundle (#32) therefore
   needs a pinned copy of the trusted roots and a verify mode that does not call out. Whether the pinned cosign supports this must be checked, not assumed.
3. A release workflow that builds the kit, pushes by digest and signs. It is a workflow file under `.github/workflows/`; see Blockers.
4. The refusal demonstration for keyless: an unsigned kit, a kit signed by a different identity and a tampered kit must each be refused, as `just cosign-supply-check` does for the key path.

## Blockers and open points

- **Token scope.** Adding a workflow file needs the `workflow` scope on the token used to push; the one in use does not have it (recorded earlier on #48). The owner has to grant it or add the workflow from the web UI.
- **Publishing is outward.** Making ghcr.io packages public, or pushing the first kit, is not done by this document or by the PR that adds it.
- **Licence of third-party weights.** #52 already requires a licence check before any third-party weights go to a shared registry. Only the released Apache-2.0 students are in scope for the first push.
- **Real key custody for the local registry** remains the owner's: the development key is a throwaway.

## What this unblocks

#51 (agent kit: pack, sign, refuse when unsigned) and #52 (publish the students) can now be designed against one release path. #53 depends on #22, which was closed as superseded on 2026-11-15 (the MLflow run tracking sat inside it); #53 needs a decision on whether MLflow tracking is still wanted.

# Publishing Relay to PyPI

This guide is for maintainers preparing Relay's Python API and CLI distributions. It does not
publish the React workspace or Docker image.

## Current publication status

`relay-hitl-agent` was unregistered when the PyPI JSON and Simple APIs were checked on 2026-07-19.
The release workflow in this repository is preparation for Trusted Publishing, not evidence that a
package has been published. Keep the README's source and Docker installation guidance until PyPI
returns the intended released version.

Check current status before each first upload:

- [PyPI JSON API](https://pypi.org/pypi/relay-hitl-agent/json)
- [PyPI Simple API](https://pypi.org/simple/relay-hitl-agent/)

A pending Trusted Publisher does not reserve a project name. Minimize the time between configuring
the pending publisher and performing the reviewed first upload.

## Owner-controlled setup

Before running the workflow, the release owner must:

1. Choose whether the PyPI project will belong to a personal account or a PyPI Organization.
2. Verify the owning account's email address and enable PyPI two-factor authentication.
3. Protect `main` with a branch protection rule or repository ruleset before using the manual
   bootstrap. Require the repository's reviewed CI checks and prevent unreviewed direct changes;
   the workflow deliberately skips a manual run when `main` is not protected.
4. Create and protect a GitHub environment named `pypi`. Configure selected deployment refs rather
   than allowing every branch and tag: allow `main` for the one-time manual bootstrap and protected
   release tags matching `v*` for normal releases.
5. Require a maintainer review on the `pypi` environment. When a second maintainer exists, prevent
   self-review, and disable administrator bypass of the environment protection rules.
6. Protect `v*` tags with a repository ruleset that restricts tag creation, update, and deletion to
   release maintainers.
7. In the selected PyPI account or organization, create a pending GitHub Actions Trusted Publisher
   with this exact identity:

   | Field | Value |
   | --- | --- |
   | PyPI project | `relay-hitl-agent` |
   | GitHub owner | `ownasquare` |
   | Repository | `human-in-the-loop-agent` |
   | Workflow | `release.yml` |
   | Environment | `pypi` |

PyPI matches this tuple exactly. A different owner, repository, workflow filename, or environment
will cause the OIDC exchange to fail. See PyPI's guides for
[creating a project with a Trusted Publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
and [publishing with one](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

Do not create or store a long-lived PyPI token for this workflow.

## Prepare the release

1. Confirm the intended version in `pyproject.toml` and `CHANGELOG.md`.
2. Run `make check`, `make audit`, `uv lock --check`, and `uv build` from a clean checkout.
3. Create an exact annotated tag named `v<project-version>`. For version `0.1.0`, the only accepted
   tag is `v0.1.0`.
4. Publish the matching GitHub release. Future `release.published` events start the workflow
   automatically.

The workflow validates the published GitHub release through GitHub's API, checks out only
`refs/tags/<tag>`, requires an annotated tag whose commit is contained in `origin/main`, verifies
that automatic release runs use the exact event tag ref with a protected `tag` ref type, verifies
that the tag and `pyproject.toml` version match, runs the complete backend quality and security
gates, and builds the wheel and source distribution once. It then rejects any output other than one
exactly named wheel and one source distribution whose fully validated package metadata matches the
project. Before upload, each artifact is installed independently into a new temporary Python 3.12
environment. Runtime dependencies are synchronized from a hashed, frozen `uv.lock` export and the
source build uses the workflow's exact Hatchling build constraint; neither smoke resolves the
project's open runtime ranges. Both formats must pass dependency, isolated import, `relay --help`,
`relay doctor`, and demo reset checks. A separate publish job downloads those artifacts and receives
only GitHub's short-lived OIDC permission.

GitHub Actions artifact immutability protects the byte handoff between the build and publish jobs
within one run. It is not proof that the source tag itself is trustworthy. The annotated-tag,
published-release, protected-ref, `origin/main` ancestry, version, and distribution metadata checks
provide the source-to-artifact binding; maintainers must still review changes to the pinned release
workflow and protected tags.

## First upload for the existing v0.1.0 release

The `v0.1.0` GitHub release predates `release.yml`, so its published event will not replay. After the
owner-controlled setup above is complete:

1. Open **Actions → Publish Python package → Run workflow**.
2. Enter the exact tag `v0.1.0`.
3. Review the build job and its distributions.
4. Approve the protected `pypi` environment deployment.
5. After the successful `v0.1.0` bootstrap, remove `workflow_dispatch` and its manual-tag input from
   `release.yml`, remove the manual branch of the build-job guard, and remove `main` from the
   environment's selected deployment refs. Future publishing must begin with a protected `v*`
   GitHub release tag.

Never dispatch the workflow from a branch name or an untagged commit. Never rebuild locally and
upload a different artifact under the same version.

## Verify publication

After the publish job succeeds:

1. Confirm the JSON API reports the expected version and both wheel and source distribution.
2. Confirm PyPI identifies the files as uploaded with Trusted Publishing.
3. Install the exact version in a new environment and run `relay doctor`.
4. Only then add `pip install relay-hitl-agent` to newcomer documentation.

PyPI release files cannot be replaced. If a published version is defective, yank it, correct the
source, increment the version, and publish a new exact tag. Do not delete and recreate the project
as a normal rollback procedure.

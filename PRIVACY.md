# Repository privacy

Keep experimental RF/HD results, probe exports, notebooks with outputs, local
infrastructure settings, and compiled distributions outside Git. The small
`python/tests/fixtures/release_smoke_rf.json` file is explicitly synthetic.

Copy `.env.local.example` to `.env.local` for the real execution host and data
paths. Source that private file before using the remote commands in the READMEs.
Tracked host names, filesystem paths, and network ranges are examples. Deployments
must supply their own configuration; sanitizing this repository does not move or
restart a running service.

Use your GitHub noreply address for commits. Install the repository hooks with:

```sh
git config --local core.hooksPath .githooks
```

The pre-commit hook checks staged blobs. The pre-push hook checks the complete
history of each outgoing branch or tag, so merging an old clone cannot silently
restore removed inputs. These are static Git maintenance checks and do not run
the viewer. CI runs the same check. Secret scanning is complementary: it does not
identify unpublished scientific data reliably.

## After the privacy history rewrite

Existing clones must use the rewritten history before pushing. Prefer a fresh
clone and reapply only reviewed, uncommitted source changes. Do not merge the old
history, push all old branches or tags, or upload old build artifacts. Keep any
recovery bundles and uncommitted-work backups private and outside this repository.

Previously published commit caches and pull-request refs may require GitHub
Support to purge them. Keep the repository private until that cleanup and the
review of any replacement release assets are complete. Previously downloaded
copies cannot be recalled.

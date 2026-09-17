# First-event repository registration

One bounded gateway can register an installed repository when its first trusted
Actions PR event arrives. This is an explicit runtime option, not automatic
deployment or evidence of a live multi-repository rollout.

Use this guide with **`TLH_REGISTRATION_MODE=first-event`**. When that variable is
unset, **`fixed` remains the default** and the existing single-repository
[onboarding](onboarding.md) and [App configuration](github-app.md) still apply.

## Prerequisites

Before sending a first event, the operator and repository administrator must:

1. Deploy trusted gateway code with durable, dedicated registry and tenant storage,
   model access, and a pathless HTTPS origin reachable by Actions and authors.
2. Register one GitHub App and install it on each intended repository. Approve
   Metadata read, Contents read, Pull requests read/write, Commit statuses
   read/write, and Actions read/write. Checks read/write is optional; enable it
   separately with `TLH_CHECK_RUNS=true` only after approval.
3. Configure the App's one shared callback as
   `https://bot.example/auth/github/callback`.
4. Put a valid `.lasthuman.yml` and the trusted
   `.github/workflows/lasthuman-app.yml` relay on `main`. The current relay checks
   out the default branch, so use `main` as that branch. Never execute PR-head
   code in the privileged relay.
5. Provide the trusted verifier source/dependencies expected by the relay.
   The current template installs `.[bot]` from the checked-out repository:
   copying the workflow into a repository without the package is not sufficient.
   First-event registration does not distribute or install verifier code.
6. Set **repository Actions variables**, for every repository:
   `LASTHUMAN_RUNTIME=app` and `TLH_BOT_URL=https://bot.example`. The latter is
   the origin, without `/repos/<id>`. Add `CODEOWNERS` for module coverage.
7. Send a supported PR event and observe the App's status/card before making
   `last-human/human-verified` required, with the installed App as expected issuer.
   Keep existing CI, review, and up-to-date requirements.

Installing an App does not deploy a server, add policy/workflows, set variables,
replay an existing PR event, or alter branch protection. Those are separate,
operator-approved steps. App secrets and model credentials stay on the service,
not in the Actions relay.

## Mode, owner policy, and defaults

Set this in the gateway's operator-managed environment:

```text
TLH_REGISTRATION_MODE=first-event
```

The four fixed binding fields are **not required or used** in this mode:
`TLH_REPOSITORY`, `TLH_REPOSITORY_ID`, `TLH_OWNER_ID`, and `TLH_INSTALLATION_ID`.
They are derived from verified Actions identity and independent App installation
discovery, not from browser input.

Required common fields remain `TLH_APP_ID`, `TLH_CLIENT_ID`,
`TLH_CLIENT_SECRET`, `TLH_PRIVATE_KEY_FILE`, `TLH_BASE_URL`, and `TLH_SECRET_KEY`.
Secrets must meet the existing length checks; the private key must be a private
regular file, not a symlink. `TLH_BASE_URL` cannot contain a path, query, fragment,
or embedded credentials.

`TLH_MODE` defaults to `development` (loopback origin only). For an HTTPS service,
explicitly select `live`; its default status is `last-human/human-verified`,
instead of development's `last-human/human-verified-dev`.
`TLH_WORKFLOW` defaults to `lasthuman-app.yml`; **`TLH_WORKFLOW_REF` must be
`refs/heads/main` in first-event mode**, including development.
Common model settings and the selected authentication remain necessary to
generate questions and grade answers; see the [App/model guide](github-app.md).
Question count defaults to 3, with range 1–5; changing registration mode does not
change the risk rules, question prompts, pass/hold criteria, or receipt schema.

| Setting | Default and bounds |
| --- | --- |
| `TLH_REGISTRATION_DATABASE` | `.work/lasthuman-registry.sqlite3`, a dedicated registry; **never falls back to `TLH_DATABASE`**. |
| `TLH_STATE_ROOT` | `.work/lasthuman`; use durable, separately owned storage for deployment. |
| Tenant database | `<TLH_STATE_ROOT>/repos/<numeric_repository_id>/lasthuman.sqlite`. Sibling directories are `cache`, `snapshot-cache`, and `runtime`. |
| `TLH_REGISTRATION_OWNER_ALLOWLIST` | Optional comma-separated positive **numeric owner IDs**. Empty/unset allows any owner only after installation and trusted workflow/policy checks. |
| `TLH_MAX_REGISTERED_REPOSITORIES` | `32`, range 1–256. Total registry rows, **including retired rows**, count toward capacity; this also bounds active tenant runtimes. |
| `TLH_REGISTRATION_POSITIVE_TTL_SECONDS` | `60`, range 5–3600 seconds. |
| `TLH_REGISTRATION_NEGATIVE_TTL_SECONDS` | `15`, range 1–300 seconds. |
| `TLH_MAX_MODEL_CALLS` | `4`, range **1–4** across the process. Per-tenant concurrent model calls are fixed at **1**. |

`TLH_REGISTRATION_OWNER_ALLOWLIST` is the implemented name;
`TLH_ALLOWED_OWNERS` is not an alias. Supply IDs, not login names.
An empty allowlist is not anonymous registration or a public tenant directory.
In first-event mode, `TLH_DEMO_SEED` requires an explicit
`TLH_DEMO_SEED_REPOSITORY_ID`; it is never shared with all tenants.

Without an audience override, the relay requests `github.repository`.
The gateway accepts that **signed repository claim** or its configured common
`TLH_OIDC_AUDIENCE`. For a custom audience, set the repository Actions variable
`TLH_OIDC_AUDIENCE` to the common server value. There is no per-tenant audience
configuration stored in the registry.

## Repository-scoped demo data

Keep the existing dashboard seed file and select the numeric repository that may
display it. For `microsoft-2026-hackathon/the-last-human`, use:

```bash
TLH_DEMO_SEED='/absolute/path/to/dashboard-seed.json'
TLH_DEMO_SEED_REPOSITORY_ID='1371498352'
```

Replace the example path with the existing seed file's absolute path. Both
settings must be supplied together in first-event mode; missing files, incomplete
pairs and invalid repository IDs fail configuration. To disable the overlay,
unset both settings. Fixed mode keeps its existing `TLH_DEMO_SEED` behavior and
does not require or use the repository selector.

Only the matching numeric tenant receives the seed. Another repository, even one
with the same name, receives no seed. The target must still be installed, opted
in and registered; configuring demo data grants no access and creates no tenant.
The existing dashboard adds the seed to live coverage and displays **Demo data**.
No snapshots, receipts, gate results or model decisions are fabricated or changed.

This option changes configuration only: no schema change or repeat data import is
needed to enable it. If rollout preparation and backups are already complete,
preserve them, update the existing deployment checkout to the merged code, and
use its existing virtual environment. Do not repeat worktree creation, environment
copying or backups. Keep `TLH_DEMO_SEED` in the new runtime environment, add the
selector, and continue the remaining import/startup steps.

## Admission and identity changes

Only supported `pull_request_target` events sent to `/api/actions/events` can
create a registration. Receipt reads, verification, job polling, browser URLs,
and OAuth state never onboard a repository.

The gateway validates GitHub's pinned OIDC issuer/JWKS and RS256 signature and
time claims before trusting repository, numeric repository/owner IDs, audience,
subject, or workflow claims. The trusted workflow identity is exactly:

```text
<owner>/<repo>/.github/workflows/lasthuman-app.yml@refs/heads/main
```

The default OIDC subject may use either GitHub's legacy name-only format or its
[immutable subject format](https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims):

```text
repo:<owner>/<repo>:ref:refs/heads/main
repo:<owner>@<owner_id>/<repo>@<repository_id>:ref:refs/heads/main
```

For `pull_request_target`, the corresponding legacy or immutable `:pull_request`
subject is also accepted. Its separate signed `ref` and `workflow_ref` must still
identify trusted `main`. Ordinary `pull_request` events remain rejected, and
`workflow_dispatch` continues to require a ref-scoped subject. Repository
names/IDs alone do not authorize a PR-head workflow.

Subjects are compared exactly, in initial and tenant-bound verification, against
the signed repository name, numeric IDs and trusted ref. Different IDs/names,
other refs, environment subjects and arbitrary custom subject templates are
not accepted. Signature, issuer, time, audience and workflow checks remain
required. Do not disable immutable subjects or relax validation to resolve a
legacy-format mismatch.

It rejects malformed bodies and repository bindings before discovery. Discovery
uses an App JWT, checks the App/installation/owner, requires an active installation
and core permissions, requests a token scoped to exactly the numeric repository
ID, verifies repository metadata, and reads policy and relay from trusted `main`.
The relay must opt in to `pull_request_target`, execute the Last Human relay, and
have `id-token: write`. Merely asserting a repository name is not enough.

Revocation is **TTL-bounded, not webhook-instant**:

- A fresh positive result may authorize work for up to the configured positive
  TTL, 60 seconds by default, after upstream removal or suspension.
- Denials can delay a retry by the negative TTL, 15 seconds by default.
- After expiry, discovery rechecks installation, grants, identity, and opt-in.
  Missing/suspended installation or denied access retires the context.
- Transient GitHub failures are operational errors, not permission to reuse an
  expired successful result. Background work and publication also recheck the
  current tenant generation.

Rename/transfer requires a fresh signed event identifying the new name/owner.
Reinstallation is rediscovered at validation and changes the generation; send a
fresh event to resync affected PR work. Changed contexts retire old runtime/token
caches and cannot publish queued work from the old generation. Old installation
receipts are not recertified for the new installation. A new repository reusing
an old name but with a different numeric ID gets separate storage.

Retirement followed by readmission also requires a new successful confirmation
and Actions verification when the installation ID stays the same. Private receipt
generation metadata prevents an old verified receipt from becoming authoritative
again. Historical successful receipts and their external format remain unchanged.

## Repository URLs and the common callback

Keep `TLH_BASE_URL` and repository `TLH_BOT_URL` as the origin. Public browser
links acquire the numeric repository prefix:

```text
https://bot.example/repos/<repository_id>/dashboard
https://bot.example/repos/<repository_id>/prs/<pr>
https://bot.example/repos/<repository_id>/receipts/<receipt_id>
```

The callback remains **`https://bot.example/auth/github/callback`** for every
repository; do not register per-repository callbacks. Cookies contain an opaque
SID, have deterministic **opaque names unique to each tenant**, and use `Path=/`
so the root callback receives them. Do not depend on a raw repository ID appearing
in the cookie name.

OAuth state has a repository prefix plus a random component. The prefix only
routes to an already registered tenant: that tenant's memory-backed session
manager must still validate and consume the one-time state against its SID.
PKCE, CSRF, and author-only interview/receipt checks remain required. A writer
who is not the author is denied; supported external PR authors are not excluded
by a new push-permission requirement. Local redirects retain the tenant prefix;
user-supplied `next` is not authority to redirect outside it.

Sessions and user OAuth tokens are memory-only, with 30-minute sessions; restart
requires login again. Raw answers and Hold feedback do not become permanent
history. Registration adds no individual metrics or manager access.
Unscoped `/`, `/dashboard`, `/prs/<pr>`, and `/receipts/<id>` return a
repository-selection instruction, not an arbitrary tenant or named inventory.

Actions retains the global `/api/actions/events`, `/api/actions/jobs/<id>`,
and `/api/actions/receipts/<id>` routes, including receipt `/verify` and
`/publication`. OIDC selects and authorizes the tenant even for these global
paths. Scoped Actions paths reject tokens for a different repository too.
Relay metadata carries the validated repository prefix; the receipt schema
does not change. First-event gate targets must be exactly the trusted
`/repos/<repository_id>/receipts/<receipt_id>` path, never an arbitrary URL or
a fallback to root `/receipts/<id>`. The existing five verification steps remain.

## Process, storage, and maintenance

Use an existing project-specific environment, or create one for a fresh checkout:

```bash
uv venv --python 3.12
uv pip install --python .venv/bin/python -e '.[dev,bot]'
source .venv/bin/activate
```

The project uses setuptools and optional dependencies in `pyproject.toml`.
Use `uv pip`; no `uv init`, `uv sync`, `uv --project`, or lockfile migration is
needed. Package index selection follows the operator's existing uv configuration.

After the operator loads the approved environment, run one process:

```bash
gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 4 \
  'lasthuman.server.app:create_app()'
```

The gateway acquires the exclusive state-root lock **before registry DB
initialization**. Do not use multiple Gunicorn workers, `--preload`, shared-state
server replicas, or simultaneous maintenance against the same state.
Keep each deployment's registry and state root together and separate from all
other deployments; different state roots do not make a shared registry safe.

One coordinator schedules tenant ticks; children do not start their own
schedulers. Model capacity is separate from scheduling capacity so a busy tenant
does not occupy another's scheduling slot. Existing per-tenant limits remain:
50 event-job records and 16 queued/running answer-verification jobs; model/queue
saturation returns explicit busy/503 errors. `/healthz` reports scheduler,
background health, active registered/runtime tenant counts, and worker limits.
Those counts are not the total capacity count including retired rows. A stopped
scheduler or degraded background work is not reported as healthy.

Tenant operations are counted for retirement without serializing their entire
execution behind one lifecycle mutex. Slow snapshot/model/publication work must
not block another authenticated request from admitting or polling an Actions job
in the same tenant. The job can remain `queued` or `running`; this is not a
successful verification result. Shutdown stops new admission and drains existing
operations and executors before a replacement runtime opens the same Store.

Relay transport errors identify the stage (`OIDC fetch`, `event submission`, or
`job polling`) without logging tokens, payloads or credential-bearing URLs.
Per-request timeouts and the overall job polling deadline are unchanged. A
transport timeout is separate from the risk result or an author's pending
explanation; do not lower risk rules or increase all timeouts to conceal it.

Registry rows and tenant Stores survive process restart; memory sessions do not.
Use real, separate paths: symlink/reparse-point ancestors, parent traversal,
hardlinked DB/lock files, and aliased source/registry/destination/lock paths are
refused. The registry cannot live below `<TLH_STATE_ROOT>/repos` or use reserved
lock/import-journal names. Owned state directories are private (`0700`); lock
files are private (`0600`). Do not share a general-purpose directory as state.

CLI maintenance requires the gateway to be **stopped** and the same approved
first-event environment. Replace the example IDs and PR number:

```bash
python -m lasthuman.server sync --pr 123 --repository-id 123456789
python -m lasthuman.server flush --repository-id 123456789
python -m lasthuman.server flush --all-registered
```

`--repo-id` is an alias for `--repository-id`. `sync` requires a registered
repository; `flush` requires exactly one of a repository ID or `--all-registered`.
These are not dry-run commands: sync reads GitHub, may call the model, and flushes
publications; flush sends pending outbox work. Use the existing web/Actions paths
while the gateway is running.

## Import a fixed-mode database

This is an explicit **same-repository** import, not a migration between different
GitHub repositories. Stop both the legacy fixed server and the gateway first.
Keep the original database and its configuration as the rollback source.
Use the new first-event environment; the importer reads App credentials from it,
not from an old environment file.

Replace every example identity/path below with the intended source context:

```bash
shasum -a 256 .work/legacy/lasthuman.sqlite3
python -m lasthuman.server import-legacy \
  --source-db .work/legacy/lasthuman.sqlite3 \
  --repository-id 123456789 \
  --repository owner/repository \
  --owner-id 9876543 \
  --dry-run
```

Here **offline means both servers are stopped**, not network-free: even dry-run
performs GitHub installation/token/metadata and trusted-policy discovery.
It does not post PR statuses or comments. The source is read-only and must be a
known, consistent Store for that exact repository name and numeric ID. Every
receipt must match its snapshot/author, the configured App ID, and the discovered
installation ID. Foreign/mixed repositories, relabelled receipts, different
App/installations, invalid references, and nonempty destinations are rejected.

Dry-run validates the full source, owner allowlist, registry schema/capacity
(including retired rows), current identity/generation, and all planned outbox
rebinding/retirement. It reports `source_sha256`, destination, identity,
generation, and row/rebind/retirement counts without persisting registry, tenant,
or journal data. The private state directory and process lock may be created or
updated. SQLite is opened read-only/immutable; source and existing registry must
already be stopped and checkpointed, without WAL/SHM/journal sidecars. Do not
delete sidecars to bypass validation.

Compare `source_sha256` with the captured source hash. After reviewing the report,
rerun the **same command without `--dry-run`**. The importer makes a staged SQLite
backup and atomically installs it at the numeric tenant path with journaled
registry activation. Recheck the source hash afterward: the source is not
modified, and receipt bytes/IDs are preserved in the destination.

Pending work is not blindly resumed:

- Matching receipts can prove the old App/installation context for generation
  rebinding. Owned URLs under the configured origin are rebased to the tenant
  prefix.
- **Snapshot-only stores cannot prove the old App/installation.** Their evidence
  is preserved, but unproven pending work is retired instead of silently resumed.
- Pending presentation work with unowned URLs is also retired with an explicit
  resync marker. Preserve successful receipts and send a **new trusted PR event**
  to resync retired work; do not edit old records to manufacture authority.

### Interrupted import recovery

The metadata-only journal is
`<TLH_STATE_ROOT>/.lasthuman-import-<repository_id>.json`.
Gateway startup refuses unresolved journals. Dry-run refuses them too and does
not attempt recovery.

Keep both servers stopped and rerun the same import command **without
`--dry-run`**, with the unchanged source/hash, repository identity, App,
registry/state paths, and base URL:

- A **prepared** journal rolls back the interrupted attempt before retrying,
  including a crash after SQLite commit but before the committed marker.
- A **committed** journal verifies the completed import and finishes cleanup.
- Rollback restores only the known previous empty destination and exact prior
  registry row, or removes the known newly imported file/new registry row.
  It does not overwrite unknown files or changed registry metadata.

Recovery verifies file identities and source hashes. If ownership or identity no
longer matches, keep the journal and staging/backup files for investigation;
do not delete them, change symlinks, or hand-edit registry data to force startup.

## Rollback

Rollback is an operator-approved mode/configuration switch, not a blind DB copy:

1. Stop the gateway and in-flight relay/verification for the affected repository.
   Coordinate any temporary required-status change; do not weaken unrelated CI.
2. Select `TLH_REGISTRATION_MODE=fixed`, the original four binding fields, and
   the original `TLH_DATABASE`. Use the **unchanged source database** and matching
   original App/installation/repository context.
3. Preserve the first-event tenant store and any new successful receipts for an
   explicit reconciliation decision. Do not overwrite the original with it or
   silently discard new successful records.
4. Align the origin, callback, Actions variables, and expected App/status.
   Verify the trusted writer before re-enabling the required gate.

No procedure here authorizes auto-merge, automatic deployment, production DB
writes, secret rotation, or live permission changes.

## Fixed-mode compatibility

Unset or `TLH_REGISTRATION_MODE=fixed` preserves the original four repository
bindings, `TLH_DATABASE` default `.work/lasthuman.sqlite3`, unprefixed browser
routes, and `lasthuman_sid` cookie name. Both modes use the one root OAuth callback.
Fixed-mode `sync --pr N` and `flush` do not accept repository-selection flags.
`import-legacy` requires first-event mode.

In first-event mode, ambiguous old browser URLs deliberately do not work.
Root Actions API/job paths stay compatible through verified tenant routing.
Legacy queued records without a tenant generation are not a bypass: they remain
valid only in fixed mode or a validated same-context import. Receipt commit,
base, policy, snapshot, question, and author bindings remain unchanged.

## Verification and evidence

Run checks from the checkout being proposed, in its own environment. These are
**commands to run, not claims that they passed**. Record commit, Python version,
exact command, exit status, and observed results in the implementation PR.
Do not reuse pass counts from another worktree/prototype.

Install the existing development extras if needed:

```bash
uv pip install --python .venv/bin/python -e '.[dev,bot,lint]'
source .venv/bin/activate
```

Use the existing [Pylint workflow](../../.github/workflows/pylint.yml) commands:

```bash
python -m pylint $(git ls-files '*.py' ':!:tests/**' ':!:sample-app/tests/**')
python -m pylint \
  --disable=protected-access,redefined-outer-name,unused-argument,use-implicit-booleaness-not-comparison \
  $(git ls-files 'tests/*.py' 'sample-app/tests/*.py')
```

Those selectors include tracked files only. Before staging, the verifier must
also include new source and test files explicitly. After independent review,
build the distribution using the existing setuptools backend and run pytest:

```bash
uv build --python .venv/bin/python
python -m pytest -q tests
python -m pytest -q -c sample-app/pytest.ini sample-app/tests
```

Record installed-wheel verification separately if performed: include the exact
wheel, isolated environment/interpreter, installed-package import location, and
test command/result so an editable checkout cannot masquerade as package proof.

The new [runtime CI](../../.github/workflows/runtime-tests.yml) runs the full
`tests` directory on **Python 3.11 (minimum) and 3.12**, for relevant
`pull_request` changes and pushes to `main`. It installs uv, uses
`uv venv --python python`, then
`uv pip install --python .venv/bin/python -e '.[dev,bot]'` and
`.venv/bin/python -m pytest -q tests`. Permissions are `contents: read` only:
no OIDC, App/model secrets, deployment, or production state. Pylint and
sample-app workflows remain separate; runtime pytest is not a wheel-build check.

Distinguish unit doubles from integration evidence: registration/tenant claims
need actual Flask gateway requests, real RSA-signed JWT verification, real
GitHubClient/discovery with **HTTP transport faked**, real SQLite registry/Stores,
and scripted offline model results. Report the exact test modules and output
covering admission, isolation, author checks, five-step verification/publication,
revocation/reinstall, restart/scheduling, and import/recovery. A fake discovery
object alone does not establish that boundary.

Independent review is required in addition to lint/build/tests. Publish the
implementation PR against **`microsoft-2026-hackathon/the-last-human:main`**,
linking issue #7 and fresh evidence. Do not enable auto-merge or deployment.

**Live external gaps must remain explicit when not exercised:** real GitHub
Actions/JWKS issuance and network behavior, App installation/token grants,
browser OAuth/PKCE callback, actual GitHub publication and branch protection,
real model authentication/inference, live import, and deployment are not proven
by offline tests. This runbook and CI definition provide no live success evidence.

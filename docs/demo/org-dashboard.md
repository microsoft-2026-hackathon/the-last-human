# Organization dashboard walkthrough

## Prepare

- The operator enables bundled samples with `TLH_ORG_DEMO_ENABLED=true` in
  the intended runtime configuration and handles startup or deployment.
  Demo is opt-in; it does not populate the business store.
- Start from the existing **the-last-human** repository dashboard, sign in
  with repository read access, and follow **Organization**.
- The organization page is `/dashboard/organization` in fixed mode or
  `/repos/<anchor_repository_id>/dashboard/organization` in gateway mode.
  Use the supplied navigation links: they preserve the repository scope.
  No separate organization login or public organization JSON endpoint is
  part of this walkthrough.

## Present

1. Choose **Demo**. Check **Demo data**, the sample organization identity,
   **Last 30 days**, and **All connected**. The three cards show **1**, **1**,
   and **2 modules**, not organization-wide people counts.
2. Choose **1 confirmed author** to filter module rows. The card counts stay
   unchanged. Use **All modules** to reset.
3. Select a fictional repository in the **Repository** dropdown and choose
   **Apply**. This filters the organization page in place and recomputes the
   three module counts for that repository.
4. Inspect **Verified before merge**, **Can answer**, and **Declared owner**.
   The `3 / 3` example has no percentage; **Collection delayed** has no
   fabricated zero. Fictional owner labels are text, not contact links.
5. Click a fictional repository **name →**. Every Demo repository group opens
   the actual **the-last-human** repository dashboard with `data=demo`.
   Confirm the destination displays the real target repository name and
   **Demo data**, not the fictional name. This is sample-only; sample PR
   numbers do not link to real evidence.
6. Choose **Actual data** on that repository dashboard (`data=repo`) to inspect
   its actual-only coverage. Follow **Organization** and select **Actual data**
   there to inspect authorized connected repositories and current declared
   contacts. Organization source changes reset repository and card filters.
7. In Organization Actual, a repository **name →** opens that repository's
   own actual-only dashboard. A **Declared owner** link opens its current
   declared GitHub user or team. Another repository's dashboard may require
   its own repository session; the anchor session cookie is not copied
   across tenants.

## Short talking points

- **Module evidence:** “Verified before merge is the eligible pre-merge
  evidence for this module in the last 30 days.”
- **Recorded breadth:** “Can answer counts distinct authors with eligible
  recorded evidence here. It is not an expertise rating or a named history.”
- **Inspect or contact:** “Open the repository dashboard to inspect coverage;
  use the current declared owner for contact. The owner is not an identity
  inferred from the verification count.”

## Source and operational boundaries

Organization source links use `source=actual|demo`; its GET filter form uses
`source`, `repository`, and `bucket=all|zero|one|many`. Repository dashboards
use `data=repo|demo`. Navigation URLs come from the server and may be relative;
do not replace them with hand-built unscoped paths.

Explicit Actual excludes legacy seeds. Explicit Demo uses samples only.
Existing repository URLs without a source query retain their prior behavior,
including the seeded-data badge and mixed-history tooltip when applicable.
Disabled Demo is unavailable, not an automatic switch to Actual.

Actual only includes authorized, active connected repositories in the anchor
owner scope; it is not an inventory of every repository in an organization.
Missing or unavailable data and contacts remain visible as statuses.
Controlled test records establish behavior, not proof of live production
onboarding. This walkthrough does not install repositories, change credentials
or branch protection, write receipts, invoke a model, or deploy the feature.

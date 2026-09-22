# Overnight push, findings and what is not verified

**Temporary.** This file exists for one working session, 21 to 22 September 2026.
Anything in it that survives review belongs in `Public_Beta_Plan.md` or
`CosmicSupremacy_Reconstruction_Report.md`; the rest gets deleted with the file.

Its job is the thing that caught three real bugs in the session before it:
**writing down what has not been checked, next to what has.** A claim like "the
blob is correct" and a claim like "a player sees the right thing" are different
claims, and the gap between them is where the last four defects lived.

---

## Read this first if you are picking the session up

| | |
|---|---|
| client | one game process per machine. An agent holds it; nothing else may launch one |
| stub server | `cs_server.py` must be listening on 8888 or every tick fails after doing the work |
| live site | `cs-resurgence` also hosts cosmicresurgence.com. Never `firebase deploy` without `--only`, never touch `site/` |
| relay | built, **not deployed**. The operator deploys it |

---

## NOT VERIFIED, carried in from before this push

Each of these is a thing the code claims and nobody has watched happen.

- [ ] **H2, the referee is pull-only.** Nothing has confirmed the router carries
  no forwarding rule. The property is architectural, the evidence is absent.
- [ ] **H3, Firebase is the record.** The referee has never been killed
  mid-galaxy and the machine rebooted.
- [ ] **H4 and H7 done-when.** Both need the relay deployed, which is the
  operator's call. Everything so far is emulator-only.
- [ ] **J4, a second install.** One uid has never been refused a seat held by
  another, because that needs two installs or a faked data directory.
- [ ] **M1, the upload.** Redaction is built and measured; nothing has ever
  uploaded anything.
- [ ] **L2, `min_build`.** The gate works and nothing writes the field, so no
  galaxy is gated in practice.
- [ ] **K1, the live half.** An injected galaxy has not been loaded since the
  seat-order fix, and no colony ship has been watched completing after a join.

---

## Needs the operator

Collected as they arise, so the morning has one list rather than a reread.

1. **Deploy the relay.** `firebase deploy --only functions:relay` from
   `functions/`, plus the one-time signing grant in `functions/deploy.py`'s
   docstring: enable `iamcredentials.googleapis.com` and grant
   `roles/iam.serviceAccountTokenCreator` to the runtime service account on
   itself.
2. **Deploy the rules**, deliberately and once, as
   `firebase deploy --only firestore:rules,storage` from a directory whose
   `firebase.json` names nothing else.
3. **Hosting version retention.** 20 finalized versions, 449.9 MB, 4.4% of the
   10 GB tier, growing about 22 MB per deploy with no cap.

---

## Findings from this push

Appended as agents land. Nothing here is folded into the plan until it has been
read.

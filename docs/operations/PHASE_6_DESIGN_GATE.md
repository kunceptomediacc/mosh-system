# Phase 6 Design Gate — Business Controls

Status: **ACCEPTED — local business-control foundation implemented; external compliance claims prohibited**  
Date: 2026-09-23

## Boundary

Experiment and Business are governance profiles, not quality levels. Both retain durable state, secrets discipline, tests, and error handling. Business adds stronger approvals, isolation, evidence, change control, recovery, and handover requirements.

MOSH records requirements and evidence. It must never claim legal, regulatory, privacy, security, or industry compliance solely because an automated checklist passed.

## First-slice verification

- Migration 17 adds owner-approved project governance profiles and immutable configuration/activation events.
- Profiles are `experiment | business`; classifications are `internal | confidential | restricted`; states are `draft | active | blocked`.
- Each project receives a unique opaque isolation key. Public records exclude the key; events retain only its SHA-256.
- Existing governance records cannot be silently rewritten.
- Every record permanently sets `compliance_claims_prohibited=1`.
- Authenticated `GET /api/v1/governance-status` returns aggregate counts only, excluding project identities and isolation keys.
- Live MOSH governance is active under the `experiment` profile with `internal` classification; this is a governance posture, not a compliance assertion.
- Live database schema is version 17. Full automated suite: 88 tests passing.
- The dashboard renders aggregate profile, classification, and state counts while displaying the compliance-claim prohibition; populated and empty browser fixtures pass responsive, keyboard, and accessibility acceptance.

## Second-slice verification

- `backup-db` uses SQLite's online backup API and writes a new timestamped database plus a SHA-256 manifest; it never overwrites the live database.
- `verify-backup` opens the backup read-only, verifies its manifest digest, runs `PRAGMA integrity_check`, confirms the schema version, and reports table count without restoring over production state.
- Tampered manifests fail closed. Windows file-handle lifecycle is regression-tested with explicit connection closure.
- The first live backup is schema v17, 397,312 bytes, SHA-256 `42b3a9b964abec50af61f95c12ab3687ac4b8ed5421deb20eeb86562993bc912`, with 27 tables and a successful integrity check.
- Full automated suite: 90 tests passing.

## Third-slice verification

- Migration 18 adds per-project governance requirements with `gap | evidenced | waived` lifecycle.
- Business profiles must begin in draft and cannot activate until at least one required control exists and every required control has immutable owner-approved SHA-256 evidence.
- Required controls cannot be waived. Optional controls may be waived once; evidenced/waived decisions cannot be rewritten.
- Public governance status and the dashboard expose aggregate requirement counts only, never project IDs, requirement keys, evidence digests, or decision identities.
- Live MOSH state records backup/restore evidence as evidenced and change-control review as an explicit gap.
- Live database schema is version 18. Full automated suite: 91 tests passing; browser and accessibility acceptance remain green.

## Fourth-slice verification

- Migration 19 adds immutable, project-bound change requests and one-to-one owner decisions.
- Requests bind change type, risk, description evidence, and test evidence into a canonical SHA-256 digest. Raw descriptions, evidence, and decision reasons are not stored.
- Only pending requests can be approved or rejected, decisions cannot be rewritten, and approval records evidence only: it never executes, deploys, publishes, or mutates an external system.
- Public governance status and the dashboard expose aggregate pending, approved, and rejected counts only; project identities, actors, and digests remain private.
- Live MOSH change `CHG-CFD351027D1546388BE061B860037E97` records the tested internal change-control mechanism as approved. Its request digest now supplies evidence for `change_control.review`, closing that explicit governance gap without performing an external action.
- Live database schema is version 19. Full automated suite: 93 tests passing; responsive browser, keyboard, visual-baseline, authentication, and accessibility acceptance remain green.

## Fifth-slice verification

- Migration 20 adds digest-only governance artifacts for threat models, security and privacy reviews, vendor assessments, incident runbooks, and handover records.
- Artifacts begin pending and accept one immutable owner decision. Raw artifact contents and review reasons are not copied into the database.
- Artifact review records evidence; it does not assert legal or regulatory compliance and cannot deploy, publish, message, purchase, or alter credentials.
- Public governance status and the dashboard expose aggregate pending, accepted, and rejected counts only; artifact digests, project identities, actors, and decisions stay private.
- Live artifact `GART-27B3295A07D24D71B4CB2C5EC1DC65C7` accepts the SHA-256-bound Phase 6 design-gate snapshot as a local security-review artifact.
- Live database schema is version 20. Full automated suite: 95 tests passing; responsive browser, keyboard, visual-baseline, authentication, and accessibility acceptance remain green.

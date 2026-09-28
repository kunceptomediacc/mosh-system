# MOSH Bridge Contracts

The JSON Schemas under `v1/` are the authoritative observable boundary for accounts, agents, tasks, and adapters. Provider implementations and tests must conform to them; prose documentation is not a second source of truth.

Compatibility policy:

- Compatible additions may add optional fields in a new contract revision.
- New required fields, enum removals, semantic repurposing, or type changes require a new major contract version.
- Remote `$ref` resolution is forbidden during validation. Contracts may reference only files inside this directory.

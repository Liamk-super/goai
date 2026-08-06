# LaunchScope Skills

This package is the versioned Skill registry boundary. T2 intentionally adds
no executable Skill or business flow.

The V0.1 P0 catalog is frozen to these six names by ADR 0002:

- `product-intake-normalizer`
- `intake-gap-diagnosis`
- `browser-product-audit`
- `business-investment-assessment`
- `evidence-grounding-audit`
- `version-regression-verification`

Future Skill manifests belong under this package and must carry an independent
version, input/output schema references, permissions, failure classes, budget,
evidence requirements, lifecycle and `may_write_business_state: false`.

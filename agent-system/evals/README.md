# Routing evaluation packets

Defined here, **unpopulated at P0**. Packets are written once there is real work to measure.

## The eleven representative task classes

Routing is evaluated per class, because a model that is excellent at fixture generation may be
poor at concurrency:

```text
money and rounding
idempotent transaction behavior
PDF text extraction
malformed and encrypted PDFs
scan detection and OCR geometry
evidence lineage
risk monotonicity
Foundation Models validation and fallback
SwiftData isolation and migration
privacy boundaries
headless operation-state, cancellation, and stale-result behavior
```

## Evaluated separately

Planner quality, implementation quality, reviewer defect detection, and integration
correctness. A model good at one is not thereby good at another, and averaging them hides
exactly the signal routing needs.

## No invented numbers

`agent-system/routing/metrics.json` carries `baseline: null` for every metric, and it stays
that way until real task data exists. Routing thresholds are calibrated from measurement, not
from a plausible-sounding percentage written at design time.

A model version change registers a **new** routing candidate. It inherits no historical trust.

## Hidden cases

Reviewer-evaluation and routing-calibration cases live **outside dispatched worktrees**. They
calibrate model capability and are never the sole evidence for a product release — that is
what protected oracles are for, and those are public and committed.

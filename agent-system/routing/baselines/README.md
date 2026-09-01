# Routing baselines

**Empty. There are no measured baselines.**

The initial model allocation in `../capability-registry.json` is a **prior**, not a
measurement — every entry carries `binding_type: "initial_prior"` and
`model_version: "unpinned"` to make that explicit.

It reflects a reasonable guess about which capability class suits which model. It is not
evidence, and no decision should cite it as though it were.

Baselines appear here once tasks have actually run and produced first-pass acceptance rates,
repair rates, escaped defects, review burden, and cost per accepted task. Until then, every
metric in `../metrics.json` holds `baseline: null`.

Writing a plausible number here before measuring one would be worse than leaving it empty: an
invented baseline looks exactly like a measured one to everything downstream that reads it.

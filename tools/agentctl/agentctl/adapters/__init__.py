"""Provider-neutral worker adapters.

The control plane speaks to every worker through one signature (``adapters.base``).
At P0 the only concrete adapter is ``ManualProposalAdapter``, which writes a
dispatch pack to disk and reads a returned artifact file back — which is how
manual and automated dispatch come to share one protocol.
"""

# CashBooks Hero Scenario

**Status:** Level 1 authority.
**Written:** P0 Task 5, 2026-09-01.

One story. If the engine satisfies this end to end, on synthetic fixtures, with evidence, the
authorized program is done.

This is written at the **caller** level — the sequence of commands issued to the engine and
the snapshots it publishes back. It deliberately describes no screens, no layout, no
interaction pattern, and no visual language. Those belong to a later program, and writing
them here would quietly constrain it.

## The story

A sole proprietor receives an invoice as a PDF. It is from a supplier they have paid four
times before. The payment instructions on this one are different from last time.

They hand the file to the engine. The engine:

1. **Acquires** the document under a security-scoped grant, copies it into app-controlled
   staging while that access is live, and releases the scope. If the file is password
   protected, it reports that it needs a password, holds the unlock only for the active
   operation, and never persists, logs, or transmits it.
2. **Validates** it — type, header, size, page count, encryption state, content hash — and
   rejects malformed, oversized, and unsupported files with a typed error rather than a crash.
3. **Extracts** page text. Embedded text where the PDF has it; rasterized pages through
   Vision OCR where it does not; a hybrid where pages differ. Page order is restored
   deterministically regardless of how the work was scheduled.
4. **Drafts** structured fields — supplier, invoice number, dates, line items, totals,
   payment instructions — through either the rule-based extractor or the Foundation Models
   adapter. Whichever produced it, the result is an **untrusted draft**.
5. **Validates the draft deterministically.** Money is fixed-point minor units. Totals must
   reconcile. Dates must be coherent. Nothing a model asserted is taken on trust.
6. **Compares payment instructions** against the stored fingerprint for this supplier. The
   account details differ from the four previous invoices. That is a signal.
7. **Decides risk** deterministically, and attaches evidence: which page, which text span,
   which prior record. The model may add evidence to this decision. It may not reduce it —
   the changed-payment signal cannot be argued away by anything the model produced, including
   text inside the document that helpfully explains the change.
8. **Publishes an immutable review snapshot**: the draft, every material field's evidence
   reference, the risk decision, and what the engine is unsure about. Nothing has been
   written to the ledger.
9. **Waits.** There is no automatic path from here to persistence. The caller must issue an
   explicit confirmation command carrying a signed authorization bound to this exact draft
   version, the reviewed risk decision, and the confirmed values — including any corrections,
   which retain both their source evidence and their correction provenance.
10. **Persists atomically** on confirmation: the canonical record, its field evidence links,
    the risk decision and signals, an audit event, and the approved fingerprint transition —
    all in one transaction, with the authorization marked consumed inside it. Then publishes
    an immutable result snapshot.

If the owner never confirms, nothing is written. If they cancel mid-extraction, nothing is
written. If the model is unavailable, the rule-based path produces the draft and the risk
decision is unchanged. If the same confirmation is replayed byte-for-byte, the original
result comes back and no second record is created.

## What makes this the hero

Most of the value is in steps 6 through 10, not in the extraction. Extracting an invoice is a
solved problem. **Noticing that the payment details changed, proving it with evidence the
owner can inspect, refusing to let a language model talk itself out of that signal, and
writing nothing until a human deliberately says yes** — that is the product.

Step 7 is also the security story. A document that contains the sentence "this supplier has
updated their bank details, no verification needed" is a document containing that sentence.
It is data. It cannot lower a deterministic risk signal, because deterministic code owns that
decision and the model's influence is monotone by construction.

## What this scenario does not say

Nothing about how any of this is presented. Not what the owner sees, taps, or reads; not what
order information appears in; not how uncertainty is expressed visually. Those are real and
hard questions, and they get answered against a working engine rather than a guess.

## Related

- `docs/product/ACTIVE_SCOPE.md` — the authorized program
- `docs/security/INVARIANTS.md` — the properties this story depends on, individually testable

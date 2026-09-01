# DESIGN_SPEC.md — CashGuard Visual & UX System (v1.0)

Authority: this document governs all UI decisions. `TECH_SPEC.md` governs code structure; this governs what the user sees. Where they touch (view names, screen list), TECH_SPEC wins on naming, this wins on appearance and copy.

Do not improvise a look. Every color, weight, and spacing value below is a decision already made. If a screen needs something not covered here, extend the token system — never introduce a one-off value inline.

---

## 1. Design brief (the thing being designed)

A stressed owner-operator opens this app at 9pm or 7am, on a phone, to make money decisions with consequences. The app's job is not to look impressive; it is to be **believable at a glance**. Every screen must answer, without being asked: *what is true, how do you know, and what should I do?*

That produces three design commitments:
1. **Evidence is the product.** The interface is a ledger, not a dashboard. Numbers appear with their provenance attached, never floating.
2. **Attention is rationed.** If everything is urgent, nothing is. Color is scarce and means one thing.
3. **Untrusted content looks untrusted.** Text that came from outside (emails, invoices, memos) is visually quarantined — a design requirement inherited directly from the security architecture (PRD §10), not a stylistic flourish.

Anti-goals: fintech gradient-and-glow, celebratory confetti, mascot voice, "AI sparkle" iconography, dark-pattern urgency. This app never manufactures alarm.

---

## 2. Tokens

Define once in `CashGuardApp/DesignSystem/` as `CGColor`, `CGType`, `CGSpace`, `CGRadius`. Nothing hardcoded elsewhere.

### 2.1 Color
Semantic names only — never `blue500`. Provide light and dark values; register as an asset catalog color set so dark mode is automatic.

| Token | Light | Dark | Meaning — used for nothing else |
|---|---|---|---|
| `surface` | `#F5F4F0` | `#0F1417` | App background |
| `surfaceRaised` | `#FFFFFF` | `#171D22` | Cards, sheets |
| `ink` | `#141A1F` | `#EEF1F2` | Primary text |
| `inkMuted` | `#5B666E` | `#9AA6AD` | Labels, captions, secondary |
| `hairline` | `#DFDCD5` | `#28313A` | Rules, borders, dividers |
| `attention` | `#B26A12` | `#E0942F` | Something needs a decision (high) |
| `halt` | `#9E3320` | `#E0614A` | Do not proceed without verification (critical) |
| `settled` | `#2C5F5A` | `#5FA79F` | Verified, resolved, confirmed-safe |
| `quarantine` | `#5E5470` | `#A294BC` | **Untrusted external text only.** Appears nowhere else in the app. |

Rules: medium and low severity get **no color at all** — they are `ink` on `surfaceRaised` and are ranked by position, not hue. Only `attention` and `halt` are colored, which is what makes them mean something. Color never carries meaning alone: every severity also has a text label and a distinct SF Symbol (accessibility requirement, not optional).

### 2.2 Type
Native faces, opinionated usage. No custom fonts (Dynamic Type and licensing make them a liability here).

| Role | Font | Usage |
|---|---|---|
| `figureXL` | SF Pro Rounded, Bold, ~44pt, **monospaced digits** | The one headline number per screen (cash position, safe-to-spend) |
| `figure` | SF Pro, Semibold, **monospaced digits** | Every other money value, everywhere, without exception |
| `title` | SF Pro, Semibold, `.title3` | Card headers, screen titles |
| `body` | SF Pro, Regular, `.body` | Explanations, prose |
| `label` | SF Pro, Medium, `.caption`, uppercase, tracking +0.6 | Field labels, evidence keys, section eyebrows |
| `evidence` | **SF Mono**, Regular, `.footnote` | Evidence values, fingerprints, account digits, IDs, raw descriptors |

Non-negotiable: **all money uses `.monospacedDigit()`** so columns align and figures don't jitter when they update. Mixing proportional digits into a financial list is the single most common tell of an amateur finance app. Every type style is built on a `Font.TextStyle` so Dynamic Type scales it; test at XXL and at the largest accessibility size — cards grow, they never truncate a number.

### 2.3 Space, shape, elevation
4pt base scale: 4 / 8 / 12 / 16 / 24 / 32. Card padding 16, gutter 16, inter-card 12. Corner radius: 14 for cards, 10 for controls, 0 for hairline rules. **No drop shadows** — elevation is expressed with `surfaceRaised` plus a 1px `hairline` border. Shadows read as web-app, not native document.

---

## 3. Signature element: the evidence block

This is the one thing the app is remembered by, and it appears on every alert, action, and explanation. Build it once as `EvidenceBlock`, use it everywhere.

```
┌────────────────────────────────────────┐   ← 1px hairline, 14pt radius
│ ▔▔▔▔ 2pt severity rule (attention/halt/hairline)
│  WHY THIS IS FLAGGED          ← label  │
│                                        │
│  Pinned account    ····4471   ← evidence (SF Mono)
│  Requested account ····9302   ← evidence, halt-colored on mismatch
│  Vendor average    $412.00    ← figure
│  This request      $4,800.00  ← figure
│                                        │
│  ev:3f21a  ev:88c0d           ← label, inkMuted, tap to expand
└────────────────────────────────────────┘
```

Rules: keys are `label`, values are `evidence` or `figure`, right-aligned so they form a scannable column. Every block ends with its evidence IDs — tappable, expanding to the raw source. A claim without an evidence ID may not be rendered in an evidence block. This is the UI expression of the grounding contract; it is also the thing that makes a demo viewer believe the app.

## 4. Signature element: quarantine styling

Any string originating from an `UntrustedTextDocument` renders through `UntrustedTextView` and looks unmistakably different:

- 3pt `quarantine` left bar, `quarantine`-tinted background at 8% opacity
- `evidence` (SF Mono) type, never body type
- Always prefixed with a `label` attribution: `FROM THE REQUEST — NOT VERIFIED`
- Never styled as an app statement; never inside a sentence the app is speaking
- Any phone number, URL, or account number inside it renders as plain text, **never as a tappable link** — tappable untrusted contact info is the exact attack the architecture exists to prevent

VoiceOver reads the attribution before the content, every time.

---

## 5. Screen direction

**Action Inbox (home).** A vertical stack of action cards, most consequential first, each showing: what to do, the number that matters, and a one-line reason. One primary button per card. Above the stack sits a single `figureXL` cash line with its as-of timestamp — the timestamp is part of the number, not a footnote. Empty state is an invitation, not a shrug: "Nothing needs a decision today. Last checked 7:12 AM." Stale data (PRD §10.6) shows an inline banner in `inkMuted`, escalating to `attention` past 48 hours — the number itself never lies about its age.

**Risk review / alert detail.** Four stacked regions in fixed order: the claim (one sentence, plain), the `EvidenceBlock`, the quarantined source text, the required action. The verification checklist renders the vendor's **stored** phone number as an app-styled, tappable row — visually distinct from quarantined content, so the safe number and the untrusted number can never be confused. Resolution requires an explicit choice; there is no "OK" button anywhere in this flow.

**Ask Copilot.** Chat, but with tool calls visible as small `label`-styled rows as they run ("checked cash position · 40ms"). Answers containing figures render those figures as `figure` type with the evidence block collapsible beneath. The agent mode (`generatedBy`) is stamped in `inkMuted` on every generated artifact — visible, not hidden.

**Audit log.** Reverse-chronological, dense, monospaced, deliberately unglamorous — it should feel like a system log because that's what makes it credible. Filter chips by kind.

## 6. Motion
One orchestrated moment only: an alert card expanding into its evidence (spring, ~0.35s, layout-driven with `matchedGeometryEffect`). Everything else is a system default or nothing. No looping animations, no shimmer, no pulse on alerts. `@Environment(\.accessibilityReduceMotion)` disables the expansion in favor of a cross-fade. Restraint here reads as seriousness — which is the entire brand.

## 7. Copy voice
Plain, active, specific, never apologetic. Say what happened and what to do: "PrintWorks requested payment to an account you haven't used before. Call them at the number you have on file before paying." Not: "We're sorry, but suspicious activity may have been detected." Buttons name their outcome and keep that name through the flow — "Mark verified" produces "Marked verified." Never use "fraud detected," "blocked," "protected," or "secure" — both because they overclaim (PRD §15 claims discipline) and because they're vague. Prefer "needs verification," "unusual for this vendor," "hasn't been confirmed."

Demo/sandbox data carries a persistent `SIMULATED` chip in `label` type — required for TestFlight review honesty and it costs the demo nothing.

## 8. Quality floor (verify before any UI task is marked done)
Dynamic Type to the largest accessibility size without truncated numbers; VoiceOver labels on every evidence row and severity indicator; contrast ≥ 4.5:1 for all text (the muted tokens above are chosen to pass); reduce-motion honored; dark mode via asset catalog, never conditional code; every interactive element ≥ 44pt; no meaning carried by color alone; full keyboard navigation on the macOS target.

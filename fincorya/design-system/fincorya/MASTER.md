# FINCORYA — Design system

Reference: user-supplied FINCORYA mockup and implementation brief, 2026-09-08.

## Architecture
Django templates, shared CSS and existing HTMX interactions. Preserve routes,
forms, permissions and financial calculations. Display real application content.
Do not add services, sample financial figures or inactive controls from the mockup.

## Tokens
- Emerald / primary: #006B4F
- Dark green / navigation: #013B36
- Gold / limited accent: #C9A227
- Ivory / background: #F7F8F5
- White / cards: #FFFFFF
- Graphite / text: #18201F
- Secondary text: #5F6B67
- Borders: #DCE3DF
- Success: #16805B; warning: #B7791F; error: #C0392B; info: #2563EB

## Typography and layout
Space Grotesk headings, Inter body (local fallback), JetBrains Mono references.
Compact dark sidebar, white top bar, restrained cards, clear financial amounts.
Shared tokens and presentation live in static_src/css/interface.css, loaded after
legacy component styles. Existing inline SVG icons remain framework-independent.
Preserve the official FINCORYA logo asset.

## Screens
- Login: light split composition, existing brand text/photo, white secure form.
- Dashboard: existing metrics, seven-day operation counts, distribution and history.
- Operations: existing form and preview, prominent amount, clear field boundaries.
- Cash, finance and reports: shared cards, tables, badges, precise currency labels.

## Accessibility and responsive behavior
Visible focus, labelled forms, 44px controls, reduced motion, semantic statuses.
The chart includes a data-table alternative; zero activity has zero-height bars.
Below 820px: existing collapsible navigation, stacked forms and authentication.
Tables scroll inside their container rather than expanding the viewport.

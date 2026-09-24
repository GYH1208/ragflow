# Chat Q&A analytics dashboard — design QA

## Reference

- Source: the dashboard screenshot supplied in the product brief.
- Reference viewport: 1686 × 754 pixels.
- Target route: `/chat-analytics`.
- Intended scope: three aggregate question-count cards, one trend chart, assistant/date filters, and a two-column assistant totals table. Answer rate, accuracy rate, source breakdowns, and raw question rows are intentionally excluded.

## Captured viewport

No rendered viewport was captured. This execution environment does not expose a browser or authenticated RAGFlow session, and direct Playwright browser use was not authorized. Consequently, a pixel-level comparison against the supplied screenshot could not be performed.

## Hierarchy

Code inspection confirms the intended order: page title, three summary cards, trend panel, filter panel, and assistant totals table. The implementation uses the existing root layout and adds a dedicated global navigation destination.

Visual result: not verified in a browser.

## Spacing

The implementation uses a consistent 16-pixel section gap, responsive 16/24-pixel page padding, compact card padding, and a centered maximum-width content area matching the wide reference layout.

Visual result: not verified in a browser.

## Typography

The page uses the application typography tokens: a 24-pixel semibold page heading, 30-pixel summary values, and existing primary/secondary text colors.

Visual result: not verified in a browser.

## Cards

Three responsive cards represent all-time, last-30-days, and today counts. Each includes a distinct existing icon, localized label, locale-formatted number, and loading skeleton. They collapse from three columns to one on narrow screens.

Automated coverage: rendered values, zero values, and loading skeletons pass.

## Chart

The trend panel uses Recharts with a blue monotone line, point markers, horizontal dashed grid lines, integer Y-axis, tooltip, and accessible day/week/month controls with selected state.

Automated coverage: chart rendering and week-granularity state changes pass. Tooltip appearance and pointer behavior require browser verification.

## Filters

The filter panel contains only assistant and date-range controls. Assistant options come from the analytics response; the default is all assistants. Date range defaults to the latest 30 calendar days. Granularity changes preserve both filters.

Automated coverage: query-parameter preservation on granularity change passes. Calendar and Radix Select pointer interactions require browser verification.

## Table

The table has exactly two columns: chat assistant and total Q&As. It renders server-sorted aggregate rows and a localized empty state. No source or answer-rate column is present.

Automated coverage: column labels, absence of source text, populated rows, and empty state pass.

## Responsive behavior

Code inspection confirms responsive card columns, wrapping filter controls, flexible chart height, and horizontal table overflow. Narrow and wide rendered layouts were not captured.

## Console status

Browser console status is unavailable because no browser session could be opened. Focused backend tests, frontend tests, and targeted lint checks pass. Repository-wide TypeScript and ESLint checks remain red on pre-existing files outside this feature; no new analytics file appears in the TypeScript diagnostics, and targeted ESLint reports no findings.

## Findings

- P0: none found by automated verification; visual assessment unavailable.
- P1: none found by automated verification; visual assessment unavailable.
- P2: browser-only interaction and pixel comparison remain pending because the browser/authenticated-session prerequisite is unavailable.

final result: blocked

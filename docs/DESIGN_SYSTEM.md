# Design system: PortfolioIQ "Research Exhibit"

> **Superseded palette:** the app now uses a navy, white and blue institutional palette with IBM Plex Sans throughout (tokens in `app/ui.py`, `.streamlit/config.toml`). Warm neutrals, teal and IBM Plex Serif below are historical; the layout and chart rules still apply.

Implementation reference for the Streamlit application (M6). The colour values below were measured:

- WCAG 2 contrast ratios were computed for every text and background pair.
- Chart colours were run through a validator: OKLCH lightness band and chroma floor, separation
  under simulated protanopia and deuteranopia (Machado 2009), normal-vision separation, and
  contrast against the chart surface.

Only the light theme is designed and validated.

**Implementation notes (M6).** The app in `app/` follows this document with two deliberate changes:
charts are built with Altair, which Streamlit already installs, instead of a Plotly template (same
forms, palette, thin bars, direct labels and table twins, one dependency fewer); and the context strip
shows the real data fingerprint (SHA-256 of the CSVs and schema) rather than the example value in the
wireframes. Fonts are the IBM Plex Latin subsets (Sans variable, Serif SemiBold, Mono Regular),
self-hosted under the SIL Open Font License.

## 1. Colour

### Surfaces and ink

| Token | Hex | Use | Measured contrast |
|---|---|---|---|
| `page` | `#F4F2ED` | App background (warm paper) | |
| `surface` | `#FBFAF7` | Exhibits, chart background, inputs | |
| `sunken` | `#EFECE5` | Table header band, code blocks, evidence panel | |
| `rule` | `#DDD8CE` | Hairline borders and dividers (1 px) | |
| `row-rule` | `#E8E3D9` | Table row separators | |
| `ink` | `#1C2430` | Primary text, table header underline | 13.96:1 on page, 14.97:1 on surface |
| `ink-2` | `#4F5866` | Secondary text, labels | 6.43:1 on page, 6.89:1 on surface, 6.10:1 on sunken |
| `ink-muted` | `#626A75` | Captions, footnotes, axis labels | 5.24:1 on surface |

### Accent

| Token | Hex | Use | Measured contrast |
|---|---|---|---|
| `accent` | `#008571` | Primary buttons, selected states, chart slot 1 | White text on it 4.57:1; as a mark 4.38:1 on surface |
| `accent-ink` | `#00675A` | Links, focus ring, button hover | 6.52:1 on surface, 6.08:1 on page; white on it 6.80:1 |
| `accent-tint` | `#DCEFEA` | Background of the "independently reconciled" stamp | |

### Status (reserved meaning, always shown with a text label)

| State | Text | Background | Contrast |
|---|---|---|---|
| PASS | `#1E6B3A` | `#E4F1E6` | 5.60:1 |
| WARNING | `#855400` | `#FBEFD6` | 5.64:1 |
| FAIL | `#A3261C` | `#FADEDA` | 5.80:1 |
| NOT APPLICABLE | `#4F5866` | `#ECE9E2` | 5.93:1 |

### Verification stamps

| State | Text | Background | Left rule |
|---|---|---|---|
| INDEPENDENTLY RECONCILED | `#00594C` (6.94:1) | `accent-tint` | 3 px `accent` |
| RESULT VALIDATED | `ink` | `sunken` | 3 px `accent` |
| STRUCTURE VALIDATED | `ink-2` | `sunken` | 3 px `ink-2` |
| EXECUTED | `ink-2` | `sunken` | 3 px `rule` |
| VERIFICATION FAILED | FAIL text | FAIL background | 3 px FAIL text |
| VERIFICATION UNAVAILABLE | N/A text | N/A background | 3 px `rule` |

Status colours never identify a data series, and series colours never signal status.

## 2. Typography

The IBM Plex superfamily (SIL Open Font License 1.1) is self-hosted from `app/static/fonts/`. There
are no third-party font requests; system sans-serif and monospace are the fallbacks.

| Role | Face | Size / line height | Weight | Notes |
|---|---|---|---|---|
| Page title | IBM Plex Serif | 30 / 36 px | 600 | One per page |
| Exhibit title | IBM Plex Serif | 20 / 28 px | 600 | "Exhibit 2 · Sector exposure, Balanced Portfolio" |
| Section label | IBM Plex Sans | 12 / 16 px | 600 | Uppercase, letter-spacing 0.06em, `ink-2` |
| Body | IBM Plex Sans | 16 / 24 px | 400 | Base size |
| Table text | IBM Plex Sans | 14 / 20 px | 400; 600 for the total row | Tabular figures (`font-variant-numeric: tabular-nums`) |
| Footnote / caption | IBM Plex Sans | 13 / 18 px | 400 | `ink-muted` |
| Standalone figure | IBM Plex Sans | 24 / 32 px | 600 | Proportional figures; inline in a stat line, never a card |
| Code, SQL, receipt | IBM Plex Mono | 13 / 20 px | 400 | On `sunken` |

Numbers always use IBM Plex Sans: tabular figures where they align in columns, proportional
figures where they stand alone.

## 3. Spacing, layout, shape

- **Spacing scale (px):** 4, 8, 12, 16, 24, 32, 48, 64. Between an exhibit's parts use 12;
  between exhibits, 32; under the page title, 8.
- **Layout:** Streamlit wide layout, with the content width capped at 1240 px and centred. At most
  two columns per row. Standard splits:
  - Intelligence: 8:4 (answer, evidence).
  - Explorer: 4:8 (profile, holdings).
  - Paired exhibits: 6:6.
- **Radius:** 4 px for buttons, inputs, chips and stamps; 0 for exhibits, which are separated by
  rules, not boxes.
- **Borders:** 1 px `rule`; table header underline 1 px `ink`.
- **Elevation:** none. No shadows anywhere.

## 4. Data formatting

| Kind | Rule | Example |
|---|---|---|
| Weights and exposures | Stored as fractions; displayed × 100 with 2 decimals; unit in the column header | `Method A (% of portfolio)` → `22.00` |
| Money | USD with thousands separators; AUM in USD millions on chart axes | `25,000,000`; axis `25.0M` |
| Prices | 2 decimals | `185.50` |
| Counts | Integer with separators | `1,500` |
| Ratios | 3 decimals | `0.500` |
| Negative numbers | True minus sign (U+2212) on screen; raw numbers in exports | `−8.20` |
| Dates | ISO 8601 | `2023-10-09` |
| Missing values | Em dash plus a footnote "not available in the source data"; never 0 | `—` |
| Totals | 600 weight, rule above | |

Rounding happens only at display time, so a displayed column can total 100.01%. The footnote says so
whenever it happens.

## 5. Page anatomy and navigation

1. **Top navigation** (`st.navigation(position="top")`): PortfolioIQ wordmark (IBM Plex Serif 600,
   text only, no logo image), then Overview · Intelligence · Explorer · Exposure · Data Integrity ·
   Reliability. The active page is marked in `accent-ink` with a 2 px underline.
2. **Context strip:** one 13 px line in `ink-muted`: `Data as of 2023-10-09 · fingerprint e685fdff ·
   model gemini-3.6-flash`.
3. **Section label and page title:** for example `ASSURANCE` above `Data Integrity`, followed by one
   sentence of purpose in `ink-2`.
4. **Exhibits** in reading order.

## 6. Components

| Component | Anatomy | Implementation |
|---|---|---|
| **Exhibit** | Number and title (serif) → optional subtitle (the question, `ink-2`) → body (table or chart) → footnote (Source · Method · As of) → verification stamp | A container holding `st.html` for the title, footnote and stamp, and native elements for charts |
| **Verification stamp** | Uppercase state label (12 px, 600) plus a one-line reason, e.g. "Two independent calculations agree (max difference 0)" | `st.html` with class `piq-stamp piq-stamp--reconciled` |
| **Status chip** | Text label (PASS / WARNING / FAIL / N/A) on its tint, 4 px radius, 12 px 600 | `st.html` span |
| **Stat line** | Inline figures separated by "·": `13 portfolios · 30 securities · 95 holdings` | `st.html`; never metric cards |
| **Exhibit table** | Header band `sunken`, 12 px uppercase header labels, 1 px `ink` header underline, `row-rule` separators, numbers right-aligned in tabular figures, total row in 600 weight, horizontal scroll when wide; at most 25 rows | Escaped HTML via `st.html` (values passed through `html.escape`) |
| **Data grid** | Large or sortable tables (holdings, prices) | `st.dataframe(width="stretch")` with `column_config` number formats; themed header and border colours |
| **Question composer** | Text area (3 lines, placeholder "Ask about portfolios, holdings, sectors or exposure"), primary button "Run analysis", example questions grouped as Database · Exposure · Combined | `st.form` with `st.text_area` and `st.form_submit_button`; examples as `st.pills` |
| **Route indicator** | `Route: Text-to-SQL` / `Route: Sector exposure calculator` / `Route: Clarification` / `Route: Declined`, then the resolved portfolio and rule ("full name", "alias 'tech'") | `st.html`, 13 px |
| **Evidence panel** | Tabs: Checks · SQL · Tool calls · Receipt. Checks lists each check with its status chip; SQL shows the final query with a copy button; Receipt shows formatted JSON with a download button | `st.tabs`, `st.code(language="sql")`, `st.json`, `st.download_button` |
| **Clarification card** | The question in `ink`; one button per candidate, each with its reason ("name contains 'international'", "100% bonds"); "None of these" | `st.html` text plus native `st.button` per candidate |
| **Callout** | 3 px left rule plus tint plus a bold label (Note, Warning, Error) and one or two sentences; no emoji | `st.html` with class `piq-callout piq-callout--warning` |
| **Empty state** | Title, one sentence, next action (a command in mono or a link) | `st.html` |
| **Progress** | Named steps: Routing → Generating SQL → Validating → Executing → Verifying; each ticked when done | `st.status` with `st.write` lines |

## 7. Charts

**Chart forms.** Chosen by the data's job, before any colour.

| Data job | Form |
|---|---|
| Compare sectors within one portfolio | Ranked horizontal bars, largest first, one hue |
| Whole-portfolio split in Method A | Ranked bars in `accent` for equity sectors, then "Bonds (excluded)" in excluded grey and "Unattributed residual" in residual grey |
| How Method B rescales Method A | Dumbbell per sector, Method A (light teal) to Method B (dark teal), with the equity weight stated in the title |
| Equity, bonds and residual of one portfolio | Thin segmented bar (3 parts), figures in the adjacent table |
| Asset mix across portfolios | 100% stacked horizontal bars (equity, bonds, residual) |
| Stated AUM across portfolios | Ranked horizontal bars, one hue |
| Stated weight vs market-value weight per holding | Dumbbell (two shades of one hue) |
| Weight totals vs 100% | Dot plot with a solid reference line at 100%; off-target dots in WARNING colour with a label |
| Latency | Two separate strip charts (LLM, SQL); never a dual axis |

Pies, donuts, 3D charts, dual axes and charts that merely repeat a table are not used.

**Palette (validated).**

| Role | Values | Validation |
|---|---|---|
| Categorical slots 1–3 | `#008571` teal, `#C9702C` copper, `#4D62C4` blue | All checks pass on surface `#FBFAF7` with all pairs compared: worst colour-blind separation ΔE 8.9 (target ≥ 8), worst normal-vision separation ΔE 19.2 (floor 15), all ≥ 3:1 contrast. At most 3 series; more series means fold into "Other" or split the chart |
| One-hue ramp (ordinal) | `#5DB3A4` → `#008571` → `#00594C` | Monotone lightness, adjacent steps ΔL ≥ 0.06, light end 2.38:1, single hue (4° spread) |
| Excluded (bonds) | `#8A8478` | 3.56:1 on surface |
| Unattributed residual | `#B3AC9F` | 2.16:1, below 3:1 by design, so the bar always carries a visible label and the exhibit table |
| Gridline | `#E6E1D7`, solid 1 px | |
| Axis / baseline | `#C9C2B5`, solid 1 px | |
| Chart text | `ink-2` for values, `ink-muted` for axis ticks | |

**Colour assignment.** Equity is always slot 1 (teal) and bonds always slot 2 (copper), in every
chart. Colour follows the entity, never its rank.

**Marks and anatomy (Plotly template `portfolioiq`).**

- Font IBM Plex Sans 13 px in `ink-2`; paper and plot background `surface`; no chart title inside
  the figure (the exhibit title names it).
- Thin bars: bar gap about 0.35, rounded data ends of 4 px if Plotly 7 supports `marker.cornerradius`
  (verified in M6), and a 2 px `surface` line between stacked segments.
- Direct value labels at bar ends when a chart has 8 bars or fewer; otherwise values appear in the
  tooltip and the table.
- A legend only for two or more series, horizontal, top-left.
- Hover tooltips on every mark, with a `surface` background and `ink` text.
- Each chart is sized to its content: 28 px per bar plus the axis band, so axis labels are never
  clipped.
- Every chart has a table twin (the exhibit table, or a "View as table" expander).

## 8. Interaction states

| State | Treatment |
|---|---|
| Hover (rows, buttons) | Row background `#F0EDE6`; secondary button border becomes `accent-ink` |
| Focus | 2 px `accent-ink` ring, offset 2 px (6.52:1) |
| Selected | `accent-tint` background with `accent-ink` text |
| Disabled | `ink-muted` text, no fill, no hover |
| Loading | `st.status` with named steps; the previous result stays visible until the new one is ready |
| Empty | Empty-state component with the next action; never a blank area or a zero |
| Error | Callout in FAIL colours: what failed, what still works, what to do. No stack traces; errors are sanitised and keys are never shown |
| Gemini unavailable | Warning callout "Gemini is unavailable (503). Exposure, Explorer and Data Integrity work without it." with links |

## 9. Accessibility

- All text pairs meet WCAG AA (4.5:1). Interface marks meet 3:1, except the residual grey, which is
  always labelled.
- No meaning depends on colour alone. Statuses and stamps carry text labels; chart series carry
  legends or direct labels.
- Every chart has a table equivalent.
- Native Streamlit widgets give keyboard operation; focus is always visible.
- No animation.

## 10. Responsive behaviour

| Width | Behaviour |
|---|---|
| ≥ 1280 px | Designed layout (two columns where specified) |
| 1024–1279 px | Same layout, narrower columns; charts stretch |
| < 768 px | Columns stack in reading order (main content first, evidence after); tables scroll horizontally; navigation collapses per Streamlit |

QA viewports: 1440×900, 1280×800 and 768×1024.

## 11. Streamlit implementation map

**`.streamlit/config.toml`** (planned values):

| Key | Value |
|---|---|
| `[theme] base` | `"light"` |
| `primaryColor` | `#008571` |
| `backgroundColor` | `#F4F2ED` |
| `secondaryBackgroundColor` | `#EFECE5` |
| `textColor` | `#1C2430` |
| `linkColor` | `#00675A` |
| `borderColor` | `#DDD8CE` |
| `showWidgetBorder` | `true` |
| `baseRadius`, `buttonRadius` | `"4px"` |
| `font`, `headingFont`, `codeFont` | `"IBM Plex Sans"`, `"IBM Plex Serif"`, `"IBM Plex Mono"` via `[[theme.fontFaces]]` |
| `codeBackgroundColor` | `#EFECE5` |
| `dataframeHeaderBackgroundColor` | `#EFECE5` |
| `dataframeHeaderTextColor` | `#1C2430` |
| `dataframeBorderColor` | `#E8E3D9` |
| `chartCategoricalColors` | `["#008571", "#C9702C", "#4D62C4"]` |
| `greenTextColor`, `orangeTextColor`, `redTextColor` | Status text colours above |
| `[server] enableStaticServing` | `true` (self-hosted fonts) |
| `[client] toolbarMode` | `"minimal"` |

**Custom CSS.** Scoped to classes prefixed `piq-` inside `st.html` blocks, plus a minimal global
block for tabular figures in tables and the content width cap.

**Security.** All text inserted into HTML (database values, model output, user input) passes through
`html.escape`. `st.html` also sanitises with DOMPurify and runs no JavaScript.

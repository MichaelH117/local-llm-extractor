# Design review: results site (Hanson Labs / MANILA), 2026-10-30

Seat: Design Lead. Site: `site/` (5 pages), rendered to `site/_site/`. Screenshots: `docs/img/site/`.

**Context (3 lines).** Audience: a small-office manager (median age about 45, not technical, worried about client
confidentiality), plus a recruiter checking for an honest AI evaluation. Must know: what it does, how accurate it is on
new forms, and that nothing leaves the PC. Should do: try the document reader, then read the privacy page.

## Checks (headless Chrome via CDP, one Chrome at a time)
- `quarto render site`: 5 of 5 pages, 0 warnings. The pre-render step `build_data.py` passed all number checks.
- Build guard proven: a planted edit to one `field_accuracy.csv` row made `build_data.py` exit 1, with 4 failed checks.
- 5 pages x 6 conditions (1440x900, 1280x720, 640x900, 375x800, forced-colors, reduced-motion), plus dark mode,
  doc_043 deep link and the Random button: 0 JS errors, 0 pages wider than the viewport, 0 text under 12 px,
  0 controls under 44 px, 0 unnamed controls, 0 tables without a caption, 0 broken `aria-labelledby`, skip link on
  every page, SYNTHETIC on every page.
- Wallpaper: moving on story pages, still under reduced motion ("Motion off (system setting)"), hidden by default on
  the document reader (working screen, "Show background" toggle), hidden by CSS under forced colors.

## Chartability critical tests
| Test | Result |
|---|---|
| Low contrast | PASS (MANILA tokens, 170 contrast checks pass in build_tokens.py) |
| Content only visual | PASS (every chart prints its numbers; field table under the dumbbells) |
| Small text | PASS (0 text under 12 px measured) |
| Seizure risk | PASS (one 150 ms stamp; wallpaper changes over 20+ s) |
| Not keyboard accessible | PASS (native buttons/select/links; tooltips open on focus, Escape closes) |
| No table fallback | PASS (field table, all-24 table, NHTSA table, reader table) |
| No explanation of purpose or how to read | PASS (takeaway titles, captions, "How to read this page") |
| Reading level inappropriate | PASS with care (short sentences, plain names for P/R; not scored with a formula) |
| Motion not controllable | PASS (Pause motion, reduced motion respected) |
| User style change not respected | PASS (forced colors checked; dark mode follows the OS) |
| Color alone | PASS (✓/✗ words, filled/crossed/hollow squares, ring vs dot) |
| Uncertainty not shown | PASS in words ("50 documents is a small test: one mistake moves a box 2 to 4 points"); no intervals computed |
| Data density too high | PASS (4 KPIs; one idea per panel) |
| Synthetic vs public labelling | PASS (banner on every page; PUBLIC DATA tags) |

## Known gaps (honest)
- Takeaway titles were not restated by a separate reader (playbook rule 12): next for the UX seat.
- No real screen reader or real phone test; 375 px is Chrome emulation.
- Highlight positions in the reader are found by build_data.py from the generator's label lists, not by the model
  (stated on the page). There is no PDF-to-image library in the venv, so the reader shows the extracted text, with a link
  to each original PDF.
- No network capture of a run yet; the privacy page says so.
- Google Fonts are loaded from the internet by the site (not by the tool).

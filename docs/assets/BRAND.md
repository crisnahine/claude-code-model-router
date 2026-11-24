<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><img src="banner-brand.svg" width="880" alt="Brand: Which colors, type and motion make a page ours?"></p>

<p align="center"><a href="../../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="../README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="../../RELEASING.md">Releasing</a></p>
<!-- End of page head. -->

# Brand

How Model Router looks, and how to make a banner or diagram that fits. [`tools/docs_assets.py`](../../tools/docs_assets.py) writes every SVG here, so change the script and run it. Hand edits are lost on the next run.

## The idea

Model Router starts each agent on the cheapest rung of a ladder and climbs one when the work comes back. The visuals follow that:

- The mark is a ladder of five rungs, one per level, colored from the cheapest (lookup, at the bottom) to the strongest (frontier, at the top). An amber route enters on one rung and steps up to the next.
- Amber means the call: the route, the moving token in a diagram, a redo tag, and the row or label of a routed call. Its one other use is the eyebrow, the small capital label above a title.
- The five rung colors always mean the five levels, in the same order.

| File | Use |
| :- | :- |
| `logo-mark.svg` | The mark alone, for favicons, avatars and small spaces. |
| `logo.svg` | Mark and wordmark, following the viewer's light or dark setting. |
| `logo-light.svg`, `logo-dark.svg` | Mark and wordmark fixed for a light or a dark background. |
| `social-preview.svg` | The 1280 x 640 link-preview card, fixed dark. `social-preview.png` is its raster copy; the script copies it but does not render it. |
| `headline.svg`, `headline-phone.svg` | The README's opening banner, fixed dark like the social preview. Its briefs are checked against `explain` and its prices come from the registry. |

The rest of `docs/assets/` is badges, page banners and icons (`banner-<slug>.svg`, `icon-<slug>.svg`) and diagrams.

The wordmark is drawn with strokes, so it needs no font. Write "Model Router" in prose and `model-router` for the plugin id. Leave clear space around the mark of at least a quarter of its width.

## Palette

| Token | Light | Dark | Use |
| :- | :- | :- | :- |
| `bg` | `#F4F7FB` | `#0A101B` | Page and banner background |
| `card` | `#FFFFFF` | `#0F1828` | Cards and diagram frames |
| `line` | `#D2DBE7` | `#26344B` | Borders, rules, gridlines |
| `faint` | `#E7EDF5` | `#1A263A` | Chips, pills, empty meter segments |
| `ink` | `#0C1524` | `#E7EDF6` | Text and icons |
| `muted` | `#4A5871` | `#9BA9BD` | Secondary text, arrows |
| `route` | `#A65200` | `#FFB23E` | The call (route, token, redo tag, a routed call's row or label) and the eyebrow |
| `routesoft` | `#FFF1DE` | `#33260F` | The row a call is on |
| rung 1, lookup | `#0E8C6F` | `#38D3A8` | Haiku, medium |
| rung 2, standard | `#0A78B8` | `#45B4F2` | Sonnet, medium |
| rung 3, complex | `#3B57D6` | `#8196FF` | Opus, high |
| rung 4, hardest | `#6D3FD1` | `#AA8BFF` | Opus, xhigh |
| rung 5, frontier | `#A72C8A` | `#F27BCD` | Fable, high |

`python3 tools/docs_assets.py --check` fails, in either theme, when `ink` is under 7:1 or `muted` or `route` under 4.5:1 against `card`, `bg` or `faint`, or when a rung color is under 3:1 against `card` or `bg`.

## Type

- Sans: `-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif`, at 600 for headings and labels.
- Mono: `ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace`, for code, options, model names in pills and the eyebrow.
- No web fonts: GitHub shows README images through `<img>`, which can't load them.

## Motion

- Diagrams that explain a process loop slowly, 5 to 12 seconds. Banners and the benchmark bars play once and hold.
- Animate `transform`, `opacity` and, for a line being drawn, `stroke-dashoffset`. No scripts.
- Every animated file turns animation off under `@media (prefers-reduced-motion: reduce)`, and its resting state is the meaningful final frame: the call on its last rung, the bars at full length.

## Rules for every SVG

- A `<title>`, `role="img"` and an `aria-label` that says what the picture shows, in one or two plain sentences. The `<img>` that shows it carries the same alt text.
- Light and dark through `@media (prefers-color-scheme: dark)` inside the file, with colors as classes, except the files the table fixes to one theme. CSS variables render black in some viewers.
- Self-contained: no script, no external font, image or stylesheet.
- Valid XML and under 40 KB.
- Numbers in a diagram come from the code or the docs; the benchmark chart takes its from `bench/analyze.py`.

`--check` enforces the XML, `role`, `aria-label`, `<title>`, reduced-motion, no-outside-loads, no-CSS-variables and size rules.

## Making a new banner

1. Add a row to `PAGES` in `tools/docs_pages.py`: the file, a short slug, the title, the question the page answers (under about 50 characters), an icon from `ICONS`, and its group (1 start here, 2 how it works, 3 evidence, 4 the project). The group picks which rung the banner's route climbs from.
2. Run `python3 tools/docs_assets.py`. It writes `banner-<slug>.svg` and `icon-<slug>.svg`, puts the page head (banner and previous and next links) at the top of the page, and adds the page's card to the [documentation index](../README.md).
3. Run `python3 tools/docs_assets.py --check` and look at the banner in a browser in both themes.

`docs/BENCHMARKS.md` gets its head from `PAGES` too; `bench/analyze.py` keeps the head when it rewrites the page.

## The site

`site/` holds a static landing page that reuses these SVGs. The script copies the files the site uses into `site/assets/`, so run it after any change here. The site's address is the `SITE_URL` comment at the top of `site/index.html`. Its benchmark figures, chart alt text and FAQ come from `bench/analyze.py`'s summary, so rerun analyze and the script, never edit a figure by hand.

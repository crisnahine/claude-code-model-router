<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="../README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="../../RELEASING.md">Releasing</a></p>
<!-- End of page head. -->

# Brand

How Model Router looks, and how to make a diagram that fits. [`tools/docs_assets.py`](../../tools/docs_assets.py) writes the diagrams and badges here, so change the script and run it. Hand edits are lost on the next run.

## The idea

In Model Router each model stands for a rung of a ladder, and a call Claude marks as a redo climbs: more effort first, then a stronger model. The visuals follow that:

- The mark is a ladder of five rungs, one per level, colored from the lightest model (lookup, at the bottom) to the strongest (frontier, at the top). An amber route enters on one rung and steps up to the next.
- Amber means the call: the route, a redo tag, and the row or label of a routed call. Its one other use is the site's eyebrow, the small capital label above the title.
- The five rung colors always mean the five levels, in the same order.

| File | Use |
| :- | :- |
| `logo-mark.svg` | The mark alone, the site's favicon. Kept as a file; the script doesn't write it. |
| `logo.svg` | Mark and wordmark, following the viewer's light or dark setting, in the site's header. Kept as a file. |
| `social-preview.png` | The 1280 x 640 link-preview card for the site. Kept as a file. |
| `video.mp4`, `video-poster.jpg`, `video-play.jpg` | The 22-second video, its first frame, and a still with a play button for Markdown pages to link from. They show the design before 0.2.0, where the router picked each call's level from its brief, so the site no longer plays the video. They are rendered outside the script. Music: Sascha Ende, ende.app (CC BY 4.0). |

The rest of `docs/assets/` is the badges and the diagrams.

Write "Model Router" in prose and `model-router` for the plugin id. Leave clear space around the mark of at least a quarter of its width.

## Palette

| Token | Light | Dark | Use |
| :- | :- | :- | :- |
| `bg` | `#F4F7FB` | `#0A101B` | Page background |
| `card` | `#FFFFFF` | `#0F1828` | Cards and diagram frames |
| `line` | `#D2DBE7` | `#26344B` | Borders, rules, gridlines |
| `faint` | `#E7EDF5` | `#1A263A` | Chips, pills, empty meter segments |
| `ink` | `#0C1524` | `#E7EDF6` | Text and icons |
| `muted` | `#4A5871` | `#9BA9BD` | Secondary text, arrows |
| `route` | `#A65200` | `#FFB23E` | The call (route, redo tag, a routed call's row or label) and the site's eyebrow |
| `routesoft` | `#FFF1DE` | `#33260F` | The row a call is on |
| rung 1, lookup | `#0E8C6F` | `#38D3A8` | Haiku, medium |
| rung 2, standard | `#0A78B8` | `#45B4F2` | Sonnet, medium |
| rung 3, complex | `#3B57D6` | `#8196FF` | Opus, high |
| rung 4, hardest | `#6D3FD1` | `#AA8BFF` | Opus, xhigh |
| rung 5, frontier | `#A72C8A` | `#F27BCD` | Fable, high |

`python3 tools/docs_assets.py --check` fails, in either theme, when `ink` is under 7:1 or `muted` or `route` under 4.5:1 against `card`, `bg` or `faint`, or when a rung color is under 3:1 against `card` or `bg`.

## Type

- Sans: `-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif`, at 600 for headings and labels.
- Mono: `ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace`, for code, options, model names in pills and the site's eyebrow.
- No web fonts: GitHub shows README images through `<img>`, which can't load them.

## Rules for every SVG

- A `<title>`, `role="img"` and an `aria-label` that says what the picture shows, in one or two plain sentences. The `<img>` that shows it carries the same alt text.
- Light and dark through `@media (prefers-color-scheme: dark)` inside the file, with colors as classes. CSS variables render black in some viewers.
- Self-contained: no script, no external font, image or stylesheet.
- Valid XML and under 40 KB, with no animation.
- Numbers in a diagram come from the code or the docs.

`--check` enforces the XML, `role`, `aria-label`, `<title>`, no-outside-loads, no-CSS-variables and size rules.

## Adding a page

1. Add a row to `PAGES` in `tools/docs_pages.py`: the file, the title, the question the page answers, and its group (1 start here, 2 how it works, 3 evidence, 4 the project).
2. Run `python3 tools/docs_assets.py`. It puts the page head (links to the README, the index and the previous and next pages) at the top of the page, and adds the page's card to the [documentation index](../README.md).
3. Run `python3 tools/docs_assets.py --check`.

`docs/BENCHMARKS.md` gets its head from `PAGES` too; `bench/analyze.py` keeps the head when it rewrites the page.

## The site

`site/` holds a static landing page that shows these files. The Pages workflow copies `docs/assets/` into `site/assets/` when it deploys, so each file is kept once, here, and `--check` fails when the page names a file that isn't here. To preview the site locally, copy them the same way first. The site's address is the `SITE_URL` comment at the top of `site/index.html`. Its benchmark figures, and those in `site/llms.txt`, come from `bench/analyze.py`'s summary and its FAQ from `docs/FAQ.md`, so rerun analyze and the script, never edit a figure by hand.

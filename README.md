# NakupniTour.cz

Copyright (c) 2026 mrjoda. All rights reserved.

Personal, noncommercial viewing and use of the published site are permitted.
Copying, modification, redistribution, hosting and commercial use of original
project material require prior written permission. See [LICENSE](./LICENSE)
for the complete terms and third-party exceptions. This project is
source-available, not open-source. OSM/ODbL data, public-domain Natural Earth
boundaries and dependencies retain their own licenses and reuse rights.

Static Astro website for exploring Czech supermarket road trips: Lidl,
Kaufland, Billa and Albert, with six precomputed road routes per chain.

## Local development

Use Node.js 24.22.1 or later.

```sh
npm ci
npm run dev
npm test
```

`npm test` builds the actual Astro output and runs 64 browser scenarios,
including all 24 routes, GPX exports, playback, keyboard controls, responsive
layouts, offline operation and invalid-data handling. Layouts include 1920px
and 2560px desktops, with edge-to-edge viewport checks. It uses installed
Microsoft Edge by default. Set `PW_CHANNEL=chrome` to use installed Chrome.
Results and screenshots are written to ignored `test-results/`.
Safari, Firefox and real mobile hardware are not covered by this suite.

On phones, the map fills the viewport between a compact, expandable cockpit
and a permanent Play/Pause and distance dock. The top-right menu contains the
chain, starting city, speed, restart, GPX, follow and license/credits controls.
Options do not hide playback. Portrait and short landscape layouts are tested,
including all 24 routes on mobile, 44px action targets, menu dismissal, cockpit
expansion and rotation back to the desktop layout. Map attribution
remains visible even when the menu is closed.

On desktop, the bottom dock now keeps only Play/Pause and the moving basket
distance bar. Chain, route, speed, restart and GPX are in the same top-right
menu used on phones. Play sits alongside the basket track in a compact row;
regular kilometre dividers remain with gold novelty checkpoint dots on the
track. Hover, focus or tap reveals a bubble below the axis and distance/store
totals, never over the basket. The bubble pointer aligns to the actual dot,
even when its body is clamped near the ends. Escape or clicking outside
dismisses it. Milestone positions remain true road kilometres; crowded dots
are omitted on narrow screens. The dock has a 22%-opacity parchment background
and soft shadow. The menu uses full-width controls and a Branik switch; map
tools use centered SVG icons.

## Route accuracy and optimality experiments

The `/accuracy/` page, linked beside the license in the planner, reports bounds
for all four published default routes. City-start routes are not independently
bounded. The page explains the model, data limitations and evidence fingerprints;
percentages are conservative upper bounds, not probabilities of accuracy.

`tests/optimize_kaufland.py --chain lidl` (also `kaufland`, `billa` or `albert`)
evaluates the free-start, free-finish directed
open route against its original OSRM matrix without changing website data.
It requires Python, NumPy and SciPy; optional OR-Tools refinement requires
OR-Tools (an existing installation can be passed via `--optimizer-packages`).
For the incremental bound solver, install
`python -m pip install -r tests/requirements-optimize.txt`.
Run `python tests/optimize_kaufland.py --help` for input paths and time limits.
The inputs are the original matrix JSON, shop GeoJSON and incumbent route JSON;
coordinate fingerprints and the incumbent objective are verified before solving.
The large routing matrix is not included in this repository.

The solver adds a zero-cost dummy node and iteratively excludes disconnected
subtours from a directed assignment relaxation, including fractional directed
minimum-cut separation before the MILP phase. Its lower bounds apply to every
feasible open route, even when a time-limited solve returns no valid itinerary.
Only a single connected tour may replace the incumbent. `--solver cp-sat` uses
OR-Tools circuit constraints with full LP linearization as an alternative.
`--previous-result PATH` validates and resumes the best previous route and
bound; `--heuristic-runs 3` adds deterministically perturbed route searches.
`--solver incremental` retains a HiGHS simplex basis while adding directed
minimum-cut inequalities, rather than rebuilding and cold-starting each LP.
After no fractional cut remains, it switches the same model to integer arcs
and supplies the verified route as a feasible hint. Odd-cut inequalities on
the undirected degree-two projection strengthen fractional solutions further.
All arcs are initially retained. Only optimal-LP reduced costs may eliminate
arcs that cannot occur in a better route, using the verified incumbent cutoff;
there is no speculative nearest-neighbour pruning.
`--solver lkh --lkh-runs 10` uses Lin-Kernighan-Helsgaun to search the complete
directed matrix. Positive arc shifting preserves the free-start/open-route
objective exactly. LKH is a heuristic: it never supplies a new lower bound;
only independently verified earlier bounds are retained.
It starts from the best verified tour; `--lkh-seed` permits reproducible
independent searches, with nonsequential patching moves enabled.
The `elkai` wrapper and embedded LKH are for academic/noncommercial use only,
under their own license, not this project's original-material terms.
`--post-seconds 120` optionally refines a candidate with OR-Tools after the
main search. Disconnected cycle covers may seed a valid itinerary by joining
cycles; only the fully reconstructed, rescored path can become an incumbent.
`--target-seconds 1799` stops early if the candidate's remaining possible
saving drops below that threshold; a time limit can expire without reaching it.
Near the target, the incremental solver can instead exclude tours above the
requested threshold and prove that the remaining model is infeasible.
This certifies the absolute gap without proving the exact optimum.
Bounds from threshold-restricted searches are capped at the threshold plus
one integer decisecond, so they remain valid for the unrestricted problem.
Saved threshold MPS models can be independently rechecked with
`--verify-model PATH --previous-result PATH --target-seconds N`.
Use `--model-output PATH` to retain the model; these files can be large and
are not needed by the website.
HiGHS and CP-SAT are limited to two threads.
Tests compare results against exhaustive small asymmetric cases:
`python -m unittest discover -s tests -p test_optimize_kaufland.py`.
Set `OPTIMIZER_PACKAGES` to the existing OR-Tools package directory to include
the optional CP-SAT tests.

On 2026-11-09, each chain received 30 seconds of OR-Tools refinement and a
180-second MILP budget. These were the initial results, before further searches:

| Chain | Shops | Published seconds | Lower bound seconds | At most above optimum |
| --- | ---: | ---: | ---: | ---: |
| Lidl | 326 | 308219.3 | 252868.7 | 21.890% |
| Kaufland | 149 | 203706.0 | 203043.1 | 0.327% |
| Billa | 294 | 264895.4 | 251789.6 | 5.206% |
| Albert | 345 | 265858.9 | 237935.6 | 11.736% |

Percentages are rounded up to three decimals. Kaufland's maximum possible
saving is 662.9 seconds. Follow-up searches for Lidl and Albert use three
60-second heuristic runs, 300 seconds of fractional-cut bounds and repeated
CP-SAT circuit searches. The accuracy page contains the current published
times and strongest verified bounds, not this historical table.
Absolute optimality was not established by the initial checks.
A loose bound is inconclusive, not evidence that the route is bad. These are
floating-point solver bounds for static matrices, not guarantees about traffic.

After LKH searches, stronger bounds and road-geometry rebuilds, all current
default routes satisfy the requested under-30-minute remaining-saving ceiling.
Lidl saves 7,690.8 seconds and Albert saves 5,064.7 seconds compared with the
initially checked itineraries. The subsequent Billa search saves 1,594.8 seconds:

| Chain | Published seconds | Lower bound seconds | At most above optimum | Remaining saving ceiling |
| --- | ---: | ---: | ---: | ---: |
| Lidl | 300528.5 | 298870.2 | 0.555% | 1658.3 seconds |
| Billa | 263300.6 | 262998.1 | 0.116% | 302.5 seconds |
| Albert | 260794.2 | 259004.3 | 0.692% | 1789.9 seconds |

Albert's threshold model ruled out any itinerary of 259,004.2 seconds or less,
then was exported and independently re-solved to confirm infeasibility.
Integer deciseconds therefore give a 259,004.3-second lower bound. This proves
the requested gap numerically without claiming exact global optimality.
Billa's stronger bound came from incremental directed subtour and odd cuts;
its faster route came from LKH. Every one of its 293 road legs was rebuilt
and verified against the road-time matrix. Kaufland and all city-start
itineraries were unchanged.

`tests/adopt_route_candidate.py --result PATH --road-data PATH --osrm-packages PATH`
rebuilds every leg of a strictly improved default route using the existing
local OSRM extract. It checks every road duration against the matrix before
updating the planner's embedded route data. A differing distance is allowed
only for an exact modeled-time tie: it is explicitly logged and retained in
metadata, and the actual road geometry's distance is used. City-start
routes are preserved. GPX is generated from that same updated geometry at
export time. Run `npm test` after adoption.

After saving each solver report as `{chain}-optimality.json`, regenerate the
page data with `python tests/publish_route_accuracy.py --results PATH --matrices PATH`.
Repeat `--additional-result PATH` for follow-up reports. Each is independently
verified; the generator combines the strongest bound with the best candidate,
never weakening either simply because the latest run was worse.
The generator verifies matrix hashes, current coordinates, complete route
permutations and driving objectives before writing `src/data/route-accuracy.json`.
Its gaps always refer to the published route, not an unpublished candidate.

## Local Branik offer prototype

Run `npm run worker:dev` in one terminal and `npm run dev` in another.
Astro's development proxy forwards `/api/branik-offers` to the local Worker
at `127.0.0.1:8787`. Enable **Branik** in the top-right menu (desktop or mobile).
It is off by default and fetches nothing until enabled.
On non-localhost sites the switch is disabled with an explicit
"approved offer feed is not connected" message. Publishing the UI does not
deploy the sample Worker or enable live Kupi fetching.

**These are synthetic sample offers, not Kupi data or confirmed stock.**
The sample marks store ID 1 separately in each of the four chains. Only those
explicit branch IDs get a pin; chain-wide offers never imply branch coverage.
Every matched branch on the selected route gets the PET bottle pin. Slow
opacity pulsing respects reduced-motion preferences; pins do not block map
interaction. Expired offers are removed and failed requests show an error.

`npm run test:worker` tests the Worker using Node, without Cloudflare access.
The local server uses in-memory KV; restarting it clears the sample cache.
`worker/wrangler.jsonc` describes the eventual Worker, hourly cron and
`BRANIK_OFFERS` KV binding. Nothing has been provisioned or deployed.
Before any deployment, install/configure Wrangler, provision a KV namespace
and record its actual ID in that binding, and configure a same-origin route
for `/api/branik-offers` on the site's real custom domain (or a Pages proxy).
The Astro development proxy is not part of the production static build.

Kupi fetching is intentionally not implemented: obtain an approved feed or
permission first, then implement an adapter with product, PET/2L, validity,
participating-branch and chain-format checks. Do not equate Billa convenience
formats or Albert hypermarkets with all branches without explicit coverage.
The current Worker rejects non-sample mode with HTTP 503 rather than inventing
live availability. A future adapter must preserve the branch-only schema and
add verified source links and prices before any live-mode UI is enabled.

The local pin preview uses the user-supplied reference graphic and
[Tesco product photo](https://digitalcontent.api.tesco.com/v2/media/ghs/81d21687-90b4-489a-ab4d-ed911e62845d/223c41bb-744e-4297-8545-cde3f5a2db6d_1681588356.jpeg).
These third-party images and Braník branding are not original project assets;
confirm reuse rights before publication.

## Cloudflare Pages

Connect this repository to the existing `nakupnitour` Pages project:

- Production branch: `main`
- Framework preset: Astro
- Build command: `npm run build`
- Output directory: `dist`
- Root directory: repository root
- Node version: `.node-version` (or `NODE_VERSION=24.22.1`)

No Cloudflare adapter, Worker, API token or server-side runtime is needed.
Pushes to the production branch trigger Pages deployments.
Add `nakupnitour.cz` and optionally `www.nakupnitour.cz` in the Pages project's
Custom domains settings, and follow Cloudflare's DNS instructions. Merely
setting Astro's `site` URL does not connect the domain.

## Implementation and assets

- `src/planner.html`: self-contained planner, including embedded route data,
  styles, SVG brand and browser logic. This is the editable website source.
- `src/pages/index.astro`: emits the planner unchanged, preserving its tested
  standalone behavior without adding runtime network dependencies.
- `public/nakupnitour-logo.svg`: scalable brand artwork.
- `public/nakupnitour-logo-3600.png`: transparent high-resolution export.
- `tests/publication.cjs`: browser regression suite against the built HTML.

After editing the logo SVG, run `npm run brand` to refresh the embedded logo
and transparent PNG, then `npm test`.

Routes are best-found static OSRM estimates, not proven optimal or live
navigation. Journey to the first store, parking, store visits and driver rests
are excluded. Mapped inventories are not guaranteed complete or current; some
closed mapped locations are deliberately retained. Browser GPX exports contain
display-simplified road tracks, not turn instructions. OpenStreetMap attribution
and Natural Earth public-domain credits remain on the page.

## Address verification

`address-recovery.json` records 426 verified address additions/improvements
and their OSM object provenance. These include address points inside shop
buildings, addresses on enclosing buildings and current shop-object tags.
The Overpass snapshot is 9 November 2026. Duplicate partial representations
are consolidated; ambiguous multi-address buildings are not guessed.
115 blank addresses remain unresolved in OSM. This is not a complete official
store directory. Recovered OSM address data retains its ODbL license.

`overpass-address-query.txt` can be pasted into https://overpass-turbo.eu/.
The optional maintenance scripts in `tests/` recover and apply address
metadata without changing store IDs, coordinates, route orders or road tracks.
`recover-addresses.py` requires a local OSM extract, the original source
inventories and the optional `osmium` Python package. `overpass-addresses.py`
uses the standard library and caches its Overpass response locally (ignored
by Git). `apply-addresses.mjs` rejects stale overrides; it is intended to run
once on the corresponding pre-enrichment source.

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

`npm test` builds the actual Astro output and runs 63 browser scenarios,
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

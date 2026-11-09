# NakupniTour.cz

Static Astro website for exploring Czech supermarket road trips: Lidl,
Kaufland, Billa and Albert, with six precomputed road routes per chain.

## Local development

Use Node.js 24.22.1 or later.

```sh
npm ci
npm run dev
npm test
```

`npm test` builds the actual Astro output and runs 59 browser scenarios,
including all 24 routes, GPX exports, playback, keyboard controls, responsive
layouts, offline operation and invalid-data handling. Layouts include 1920px
and 2560px desktops, with edge-to-edge viewport checks. It uses installed
Microsoft Edge by default. Set `PW_CHANNEL=chrome` to use installed Chrome.
Results and screenshots are written to ignored `test-results/`.
Safari, Firefox and real mobile hardware are not covered by this suite.

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

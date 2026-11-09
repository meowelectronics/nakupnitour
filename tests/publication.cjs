/* Run with npm test. Uses installed Edge, or PW_CHANNEL. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const http = require('node:http');
const { once } = require('node:events');
const root = path.join(__dirname, '..', 'test-results');
const builtPage = path.join(__dirname, '..', 'dist', 'index.html');
const results = [];
const diagnostics = { pageErrors: [], failedRequests: [], httpErrors: [], externalRequests: [] };
let page, context, browser, server, origin, html;
const check = async (name, fn) => {
  const start = Date.now();
  try { await fn(); results.push({ name, status: 'PASS', milliseconds: Date.now() - start }); }
  catch (error) { results.push({ name, status: 'FAIL', error: error.stack, milliseconds: Date.now() - start }); }
  console.log(`${results.at(-1).status} ${name}${results.at(-1).error ? '\n' + results.at(-1).error : ''}`);
};
async function select(chain, start = 'optimized') {
  await page.selectOption('#chain', chain);
  await page.selectOption('#start', start);
}
async function seek(fraction) {
  await page.locator('#scrub').evaluate((e, f) => {
    e.value = Number(e.max) * f;
    e.dispatchEvent(new Event('input', { bubbles: true }));
  }, fraction);
}
async function model() {
  return page.evaluate(() => ({
    progress, playing, total, routeCount: route.length, cumulative,
    elapsed: OPTIMIZED.cumulative_seconds, order: route.map(s => s.id),
    legs: legShapes.length, rawLegs: OPTIMIZED.legs.length,
    view, baseView, following, chainSlug, chainName, start: OPTIMIZED.metadata.start_city,
    coordinates: route.map(s => [s.lon, s.lat]),
    remaining: document.getElementById('remaining').getAttribute('d'),
    trail: document.getElementById('trail').getAttribute('d'),
    cx: Number(document.getElementById('traveler').getAttribute('cx')),
    cy: Number(document.getElementById('traveler').getAttribute('cy')),
    percent: document.getElementById('percent').textContent,
    km: document.getElementById('km').textContent,
    gauge: document.getElementById('gauge-progress').getAttribute('stroke-dasharray'),
    needle: document.getElementById('needle').getAttribute('transform'),
    stop: document.getElementById('stop').textContent,
    title: document.title,
    target: document.getElementById('store-identity').textContent,
    journey: document.getElementById('distance-rail').style.getPropertyValue('--journey'),
    error: !document.getElementById('error').hidden,
  }));
}
async function exported() {
  return page.evaluate(() => {
    const savedCreate = URL.createObjectURL, savedClick = HTMLAnchorElement.prototype.click;
    let captured;
    URL.createObjectURL = blob => { captured = blob; return 'blob:publication-test'; };
    HTMLAnchorElement.prototype.click = function () { globalThis.__filename = this.download; };
    try { exportRoute(); } finally {
      URL.createObjectURL = savedCreate; HTMLAnchorElement.prototype.click = savedClick;
    }
    return captured.text().then(text => {
      const doc = new DOMParser().parseFromString(text, 'application/xml');
      const ns = 'http://www.topografix.com/GPX/1/1';
      const all = name => [...doc.getElementsByTagNameNS(ns, name)];
      const points = all('rtept'), waypoints = all('wpt'), segments = all('trkseg');
      const errors = [];
      if (doc.querySelector('parsererror')) errors.push('XML parse error');
      if ([...doc.getElementsByTagName('*')].some(e => e.namespaceURI !== ns)) errors.push('namespace');
      if (points.length !== route.length || waypoints.length !== route.length) errors.push('stop count');
      for (const list of [points, waypoints]) list.forEach((point, i) => {
        const s = route[i];
        if (+point.getAttribute('lat') !== s.lat || +point.getAttribute('lon') !== s.lon) errors.push('order');
        const expected = `${String(i+1).padStart(3, '0')} - ${chainName} - ${s.address || 'Store #'+s.id}`;
        if (point.getElementsByTagNameNS(ns, 'name')[0].textContent !== expected) errors.push('name/escaping');
        if (point.getElementsByTagNameNS(ns, 'link')[0].getAttribute('href') !== s.osm_url) errors.push('link/escaping');
      });
      if (segments.length !== OPTIMIZED.legs.length) errors.push('leg count');
      segments.forEach((segment, i) => {
        const pts = [...segment.children], expected = OPTIMIZED.legs[i].coordinates;
        if (pts.length !== expected.length) errors.push('geometry count');
        pts.forEach((p, j) => {
          if (+p.getAttribute('lon') !== expected[j][0] || +p.getAttribute('lat') !== expected[j][1]) errors.push('geometry');
        });
      });
      if (all('time').length || all('ele').length) errors.push('fabricated data');
      return { errors: [...new Set(errors)], filename: globalThis.__filename, bytes: text.length,
        version: doc.documentElement.getAttribute('version'),
        description: all('metadata')[0]?.textContent,
        escaped: text.includes('&lt;') && text.includes('&amp;') };
    });
  });
}
async function negative(name, mutation) {
  await check(`Invalid data: ${name}`, async () => {
    const broken = await context.newPage();
    await broken.route('**/invalid.html', route => route.fulfill({
      contentType: 'text/html; charset=utf-8',
      body: html.replace('let chainSlug=', `${mutation};\nlet chainSlug=`),
    }));
    try {
      await broken.goto(`${origin}/invalid.html`);
      await broken.waitForTimeout(100);
      assert.equal(await broken.locator('#error').isVisible(), true, 'An actionable error must be visible');
      assert.match(await broken.locator('#error').textContent(), /could not initialize/i);
      assert.equal(await broken.locator('button:enabled, select:enabled, input:enabled').count(), 0,
        'Broken planner must not leave interactive controls enabled');
    } finally { await broken.close(); }
  });
}

(async () => {
  await fs.mkdir(root, { recursive: true });
  html = await fs.readFile(builtPage, 'utf8');
  server = http.createServer((req, res) => {
    if (req.url === '/lidl-roadtrip.html') {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); res.end(html);
    } else { res.writeHead(404); res.end('Not found'); }
  }).listen(0, '127.0.0.1');
  await once(server, 'listening');
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ channel: process.env.PW_CHANNEL || 'msedge', headless: true });
  context = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  page = await context.newPage();
  page.on('pageerror', e => diagnostics.pageErrors.push(e.message));
  page.on('requestfailed', r => diagnostics.failedRequests.push(r.url()));
  page.on('response', r => { if(r.status()>=400) diagnostics.httpErrors.push(`${r.status()} ${r.url()}`); });
  page.on('request', r => { if (!r.url().startsWith(origin) && !r.url().startsWith('data:') && !r.url().startsWith('blob:')) diagnostics.externalRequests.push(r.url()); });
  await page.goto(`${origin}/lidl-roadtrip.html`);
  await check('HTTP initialization, embedded logo, detailed five-country basemap, no visit configuration', async () => {
    const m = await model();
    assert.equal(m.error, false); assert.equal(m.routeCount, 326); assert.equal(m.progress, 0);
    assert.equal(await page.locator('#geography path').count(), 5);
    assert.equal(await page.locator('#ticks line').count(), 21);
    assert.equal(await page.locator('details, form, .config, #schedule-result').count(), 0);
    assert(await page.locator('#brand-logo').evaluate(e => e.complete && e.naturalWidth === 1200 && e.naturalHeight === 280));
    assert.equal(await page.locator('#brand-logo').isVisible(), true);
  });
  for (const chain of ['lidl', 'kaufland', 'billa', 'albert']) {
    for (const start of ['optimized', 'Prague', 'Brno', 'Ostrava', 'Plzeň', 'Liberec']) {
      await check(`${chain}/${start}: render, start, distance seek, gauge, arrival, finish, GPX`, async () => {
        await select(chain, start);
        const initial = await model();
        assert.equal(initial.error, false); assert.equal(initial.progress, 0); assert.equal(initial.stop, '1');
        assert.equal(initial.playing, false); assert.equal(initial.percent, '0.0%');
        assert.equal(initial.needle, 'rotate(0 90 100)');
        assert.equal(initial.legs, initial.routeCount - 1);
        assert.match(initial.remaining, /^M/); assert(!/NaN|undefined|Infinity/.test(initial.remaining));
        assert.match(initial.title, new RegExp(initial.chainName));
        assert.equal(await page.locator('#dots circle').count(), initial.routeCount);
        assert.equal(await page.locator('#brand-logo').isVisible(), true);
        assert.match(initial.title, /NakupniTour\.cz/);
        assert.equal(initial.start || 'optimized', start);
        const close = (a, b) => assert(Math.abs(a-b) < 1e-6, `${a} ≠ ${b}`);
        const halfway = initial.cumulative[1] / 2 / initial.total;
        await seek(halfway);
        let m = await model(); close(m.progress, .5); assert.equal(m.stop, '1');
        close(parseFloat(m.journey), halfway*100);
        assert.match(m.target, new RegExp(`#${m.order[1]}$`));
        await seek(.5); m = await model();
        assert.equal(m.percent, '50.0%'); close(parseFloat(m.gauge), 50);
        close(parseFloat(m.needle.slice(7)), 90);
        assert.equal(m.km, Math.round(m.total / 2).toLocaleString('en-US'));
        assert.equal(m.playing, false); assert.match(m.trail, /L/);
        assert(Number.isFinite(m.cx) && Number.isFinite(m.cy));
        await page.locator('#scrub').evaluate((e, km) => { e.value=km; e.dispatchEvent(new Event('input')); }, initial.cumulative[1]);
        m = await model(); close(m.progress, 1); assert.equal(m.stop, '2');
        await seek(1); m = await model();
        assert.equal(m.progress, m.routeCount-1); assert.equal(m.stop, String(m.routeCount));
        assert.equal(m.percent, '100.0%'); assert.equal(m.gauge, '100 100');
        assert.equal(m.needle, 'rotate(180 90 100)');
        assert.equal(await page.locator('#heading-label').textContent(), 'Finished at');
        assert.match(await page.locator('#status').textContent(), /All .* mapped/);
        const gpx = await exported();
        assert.deepEqual(gpx.errors, []); assert.equal(gpx.version, '1.1'); assert(gpx.bytes > 10000);
        const suffix = start === 'optimized' ? '' : '-'+start.toLowerCase().replace('ň', 'n');
        assert.equal(gpx.filename, `czech-${chain}${suffix}-itinerary.gpx`);
        assert.match(gpx.description, /not proven fastest/);
        if (start !== 'optimized') assert(gpx.description.includes(start));
      });
    }
  }
  await check('Playback, speed, pause, restart, route/chain switch, end replay and automatic completion', async () => {
    await select('lidl');
    await page.selectOption('#speed', '30');
    await page.click('#play'); await page.waitForTimeout(300);
    let m = await model(); assert(m.progress > 0 && m.playing);
    await page.click('#play'); m = await model(); assert(!m.playing);
    const stopped = m.progress; await page.waitForTimeout(100); assert.equal((await model()).progress, stopped);
    await page.click('#play'); await seek(.3); assert.equal((await model()).playing, false);
    await page.click('#restart'); assert.equal((await model()).progress, 0);
    await page.click('#play'); await page.selectOption('#start', 'Brno'); m=await model(); assert.equal(m.progress, 0); assert(!m.playing);
    await page.click('#play'); await page.selectOption('#chain', 'billa'); m=await model(); assert.equal(m.progress, 0); assert(!m.playing);
    assert.equal(await page.locator('#start').inputValue(), 'optimized');
    await seek(1); await page.click('#play'); await page.waitForTimeout(80); assert((await model()).progress < 10);
    await page.click('#play');
    await page.locator('#dots circle').nth(m.routeCount-2).dispatchEvent('click');
    await page.click('#play'); await page.waitForTimeout(250); m=await model();
    assert.equal(m.progress, m.routeCount-1); assert(!m.playing);
    assert.equal(await page.locator('#play').getAttribute('aria-label'), 'Play itinerary');
  });
  await check('Distance extremes, intermediate ticks, exact stop selection, zero-length shape handling', async () => {
    await select('lidl');
    for (const f of [0, .000001, .01, .25, .75, .999999, 1, 0]) {
      await seek(f); const m = await model(); assert(!m.error); assert(Number.isFinite(m.progress));
      assert(Math.abs(parseFloat(m.gauge)-f*100) < .001);
    }
    const ticks = await page.locator('.distance-tick').allTextContents();
    assert(ticks.length >= 3 && ticks.length <= 8); assert.equal(ticks[0], '0 km');
    await page.locator('#dots circle').nth(10).dispatchEvent('click');
    assert.equal((await model()).progress, 10);
    assert(await page.evaluate(() => {
      const saved=legShapes[0], savedProgress=progress;
      try {
        const point=saved.points[0];
        legShapes[0]={...saved,points:[point,point],lengths:[0,0],length:0};
        progress=.5;update();
        return currentPoint().every(Number.isFinite);
      } finally { legShapes[0]=saved;progress=savedProgress;update(); }
    }));
  });
  await check('All four playback rates, long-frame cap, and hidden-document pause branch', async () => {
    await select('lidl');
    for (const speed of ['1','4','12','30']) {
      await page.selectOption('#speed',speed);
      const actual=await page.evaluate(()=>{
        pause();progress=0;playing=true;lastTime=1000;tick(1100);pause();update();return progress;
      });
      assert(Math.abs(actual-Number(speed)*.1)<1e-8);
    }
    assert.equal(await page.evaluate(()=>{
      progress=0;playing=true;lastTime=1000;tick(11000);pause();return progress;
    }),6);
    await page.click('#play');
    await page.evaluate(()=>{
      Object.defineProperty(document,'hidden',{value:true,configurable:true});
      document.dispatchEvent(new Event('visibilitychange'));
      delete document.hidden;
    });
    assert.equal((await model()).playing,false);
  });
  await check('GPX actual download, safe filename and nonempty MIME Blob', async () => {
    const event = page.waitForEvent('download');
    await page.click('#export');
    const download = await event;
    assert.equal(download.suggestedFilename(), 'czech-lidl-itinerary.gpx');
    assert.equal(await download.failure(), null);
    await download.delete();
  });
  await check('XML and DOM escaping of Czech characters, ampersands, markup and quotes', async () => {
    const original = await page.evaluate(() => {
      const saved = { address: route[0].address, city: route[0].city, url: route[0].osm_url };
      route[0].address = 'Česká & <script>bad()</script> "quoted"';
      route[0].city = '<img src=x onerror=bad()>';
      route[0].osm_url = 'https://www.openstreetmap.org/node/1?x=1&y=2';
      progress = route.length-1; update(); return saved;
    });
    try {
      const gpx = await exported(); assert.deepEqual(gpx.errors, []); assert(gpx.escaped);
      await page.evaluate(() => { progress=0; update(); });
      assert.equal(await page.locator('#address script, #destination img').count(), 0);
    } finally {
      await page.evaluate(saved => { Object.assign(route[0], {address:saved.address, city:saved.city, osm_url:saved.url}); progress=0;update(); }, original);
    }
  });
  await check('Map zoom bounds, wheel, fit, follow, pointer drag and pointer cancellation', async () => {
    await select('lidl'); const initial = (await model()).view;
    await page.click('#zoom-in'); assert((await model()).view[2] < initial[2]);
    await page.click('#zoom-out'); assert(Math.abs((await model()).view[2]-initial[2]) < 1e-6);
    await page.locator('#map').dispatchEvent('wheel', {deltaY:-100});
    assert((await model()).view[2] < initial[2]);
    await page.evaluate(() => { for(let i=0;i<50;i++)zoom(.5); }); assert.equal((await model()).view[2], 60);
    await page.evaluate(() => { for(let i=0;i<50;i++)zoom(2); }); assert.equal((await model()).view[2], 2200);
    await page.click('#fit'); assert.deepEqual((await model()).view, initial);
    await page.click('#follow'); assert.equal(await page.locator('#follow').getAttribute('aria-pressed'), 'true');
    await seek(.5); let m=await model();
    assert(Math.abs(m.cx-(m.view[0]+m.view[2]/2)) < 1e-6);
    assert(Math.abs(m.cy-(m.view[1]+m.view[3]/2)) < 1e-6);
    await page.mouse.move(1100, 400); await page.mouse.down(); await page.mouse.move(1160, 440); await page.mouse.up();
    m=await model(); assert(!m.following);
    await page.locator('#map').dispatchEvent('pointercancel');
    assert.equal(await page.evaluate(() => drag), null);
    await page.click('#fit'); assert.deepEqual((await model()).view, initial);
  });
  await check('Keyboard operation, named controls, dot Enter/Space, slider keys, visible focus', async () => {
    await select('lidl');
    for (const id of ['play','restart','chain','start','speed','scrub','export','zoom-in','zoom-out','fit','follow']) {
      const e = page.locator('#'+id); assert(await e.isVisible());
      assert((await e.getAttribute('aria-label')) || (await e.textContent())?.trim() || (await e.evaluate(e=>e.labels?.length)), id);
    }
    const dot=page.locator('#dots circle').nth(2);
    await dot.focus(); await page.keyboard.press('Enter'); assert.equal((await model()).progress, 2);
    await page.locator('#dots circle').nth(3).focus(); await page.keyboard.press('Space'); assert.equal((await model()).progress, 3);
    await page.locator('#scrub').focus(); await page.keyboard.press('End'); assert.equal((await model()).percent, '100.0%');
    await page.keyboard.press('Home'); assert.equal((await model()).percent, '0.0%');
    await page.keyboard.press('ArrowRight'); assert((await model()).progress > 0);
    assert.match(await page.locator('#scrub').getAttribute('aria-valuetext'), /kilometers; stop/);
    assert.equal(await page.locator('#status').getAttribute('role'), 'status');
    await page.locator('#restart').focus();
    assert(await page.locator('#restart').evaluate(e => getComputedStyle(e).outlineStyle !== 'none'));
    await page.keyboard.press('Tab'); assert.equal(await page.evaluate(()=>document.activeElement.id), 'chain');
  });
  await check('Recovered OSM addresses: exact provenance, destination display and GPX', async () => {
    const recovery = JSON.parse(await fs.readFile(path.join(__dirname, '..', 'address-recovery.json'), 'utf8'));
    const mismatches = await page.evaluate(entries => entries.filter(entry => {
      const feature = CHAINS[entry.chain].stores.features.find(f => f.properties.id === entry.store_id);
      return !feature || feature.properties.address !== entry.address ||
        feature.properties.address_source !== `https://www.openstreetmap.org/${entry.source}`;
    }), recovery.recovered);
    assert.deepEqual(mismatches, []);
    assert.equal(recovery.recovered.length, 426);
    await select('lidl');
    await page.evaluate(() => {
      const index = route.findIndex(s => s.id === 91);
      progress = index > 0 ? index - .5 : 0;
      update();
    });
    assert.match(await page.locator('#address').textContent(), /Sadová 918, Planá, 34815/);
    assert.match(await page.locator('#destination').textContent(), /Planá/);
    assert.deepEqual((await exported()).errors, []);
    await select('lidl');
  });
  for (const [width, height] of [[320,740],[390,844],[737,900],[1440,900],[1920,1080],[2560,1440]]) {
    await check(`Responsive ${width}x${height}: no horizontal overflow, reachable non-overlapping essential panels`, async () => {
      await page.setViewportSize({width,height}); await select('lidl'); await page.waitForTimeout(100);
      const layout = await page.evaluate(() => {
        const rect = s => { const r=document.querySelector(s).getBoundingClientRect(); return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height}; };
        return { width:innerWidth, height:innerHeight, app:rect('.app'), map:rect('#map'), scroll:document.documentElement.scrollWidth,
          controls:rect('.controls'), dashboard:rect('.dashboard'), tip:rect('.tip'),
          legend:rect('.legend'), tools:rect('.map-tools'), footer:rect('.footer'), logo:rect('#brand-logo'),
          buttons:[...document.querySelectorAll('button,select,input')].map(e=>({id:e.id,...rect('#'+e.id)})) };
      });
      assert(layout.scroll <= width, JSON.stringify(layout));
      for (const surface of [layout.app, layout.map]) {
        assert.equal(surface.x, 0, 'page surface must start at the viewport edge');
        assert.equal(surface.width, width, 'page surface must fill the viewport width');
        if (width >= 737) assert.equal(surface.height, height, 'desktop surface must fill the viewport height');
      }
      for (const r of [layout.controls,layout.dashboard,layout.tip,layout.legend,layout.tools,layout.footer,layout.logo,...layout.buttons]) {
        assert(r.x >= -1 && r.right <= width+1, JSON.stringify(r));
      }
      const overlaps=(a,b)=>a.x<b.right&&a.right>b.x&&a.y<b.bottom&&a.bottom>b.y;
      assert(!overlaps(layout.dashboard,layout.controls), 'dashboard overlaps controls');
      assert(!overlaps(layout.tip,layout.controls), 'status overlaps controls');
      assert(!overlaps(layout.legend,layout.controls), 'legend overlaps controls');
      assert(!overlaps(layout.tools,layout.controls), 'map tools overlap controls');
      assert(!overlaps(layout.tools,layout.dashboard), 'map tools overlap dashboard');
      assert(!overlaps(layout.legend,layout.dashboard), 'legend overlaps dashboard');
      assert(!overlaps(layout.logo,layout.dashboard), 'logo overlaps dashboard');
      await page.screenshot({path:path.join(root,`publication-${width}.png`),fullPage:true});
      await page.click('#play'); assert((await model()).playing); await page.click('#restart');
      await page.selectOption('#chain','albert'); assert.equal((await model()).chainSlug,'albert');
    });
  }
  await page.setViewportSize({width:1440,height:900});
  await check('Small dashboard text contrast against its light panel meets 4.5:1',async()=>{
    const colors=await page.evaluate(()=>({
      muted:getComputedStyle(document.documentElement).getPropertyValue('--muted').trim(),
      ink:getComputedStyle(document.documentElement).getPropertyValue('--ink').trim(),
      red:getComputedStyle(document.documentElement).getPropertyValue('--red').trim(),
    }));
    const luminance=hex=>{
      const c=hex.replace('#','').match(/../g).map(v=>parseInt(v,16)/255)
        .map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
      return c[0]*.2126+c[1]*.7152+c[2]*.0722;
    };
    for(const [name,color] of Object.entries(colors)){
      const contrast=(luminance('#f5efdd')+.05)/(luminance(color)+.05);
      assert(contrast>=4.5,`${name} ${contrast.toFixed(2)}:1`);
    }
  });
  await check('Touch-capable narrow Chromium page: tap playback/restart and chain selection',async()=>{
    const touchContext=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true});
    const touch=await touchContext.newPage();
    try {
      await touch.goto(`${origin}/lidl-roadtrip.html`);
      await touch.tap('#play');assert.equal(await touch.evaluate(()=>playing),true);
      await touch.tap('#restart');assert.equal(await touch.evaluate(()=>progress),0);
      await touch.selectOption('#chain','billa');assert.equal(await touch.evaluate(()=>chainSlug),'billa');
      await touch.tap('#zoom-in');
      assert(await touch.evaluate(()=>view[2]<baseView[2]));
    } finally {await touchContext.close();}
  });
  await check('Attribution and current-store links use HTTPS and noopener', async () => {
    const links=await page.locator('a[target="_blank"]').evaluateAll(elements=>elements.map(e=>({href:e.href,target:e.target,rel:e.rel})));
    assert(links.length >= 4);
    for (const link of links) { assert(link.href.startsWith('https://')); assert.equal(link.target,'_blank'); assert(link.rel.includes('noopener')); }
    assert.equal(await page.locator('.footer a[href="/license/"]').textContent(), 'Personal use only');
    assert.match(await page.locator('.footer').textContent(), /2026 mrjoda/);
    const licensePage = await fs.readFile(path.join(__dirname, '..', 'dist', 'license', 'index.html'), 'utf8');
    const licenseText = await fs.readFile(path.join(__dirname, '..', 'LICENSE'), 'utf8');
    const licenseTab = await context.newPage();
    try {
      await licenseTab.route('**/license/', r => r.fulfill({ contentType: 'text/html; charset=utf-8', body: licensePage }));
      await licenseTab.goto(`${origin}/license/`);
      assert.equal(await licenseTab.locator('pre').textContent(), licenseText.replace(/\r\n/g, '\n'));
      assert.equal(await licenseTab.locator('a').getAttribute('href'), '/');
    } finally { await licenseTab.close(); }
  });
  await check('Offline after load: switch all routes, seek, play and export without network', async () => {
    await context.setOffline(true);
    try {
      for (const chain of ['lidl','kaufland','billa','albert']) {
        for(const start of ['optimized','Prague','Brno','Ostrava','Plzeň','Liberec']) {
          await select(chain,start); await seek(.5); assert.equal((await model()).percent,'50.0%');
        }
      }
      await page.click('#play'); await page.waitForTimeout(100); assert((await model()).playing);
      await page.click('#restart'); assert.deepEqual((await exported()).errors,[]);
    } finally { await context.setOffline(false); }
  });
  await check('file:// standalone load, route change and GPX without HTTP server', async () => {
    const local=await context.newPage();
    try {
      await local.goto(require('node:url').pathToFileURL(builtPage).href);
      assert.equal(await local.locator('#error').isVisible(),false);
      await local.selectOption('#chain','kaufland'); await local.selectOption('#start','Liberec');
      assert.match(await local.locator('#status').textContent(),/Liberec/);
      assert.equal(await local.evaluate(()=>typeof exportRoute),'function');
    } finally { await local.close(); }
  });
  for(const [name,mutation] of [
    ['missing default chain','delete CHAINS.lidl'],
    ['missing store collection','delete CHAINS.lidl.stores'],
    ['duplicate store ID','CHAINS.lidl.stores.features[1].properties.id=CHAINS.lidl.stores.features[0].properties.id'],
    ['invalid store coordinate','CHAINS.lidl.stores.features[0].geometry.coordinates[0]=NaN'],
    ['unsafe store URL','CHAINS.lidl.stores.features[0].properties.osm_url="javascript:alert(1)"'],
    ['missing city route','delete CHAINS.albert.routes.Brno'],
    ['duplicate route ID','CHAINS.lidl.routes.optimized.order_ids[1]=CHAINS.lidl.routes.optimized.order_ids[0]'],
    ['missing leg','CHAINS.lidl.routes.optimized.legs.pop()'],
    ['empty geometry','CHAINS.lidl.routes.optimized.legs[0].coordinates=[]'],
    ['nonfinite geometry','CHAINS.lidl.routes.optimized.legs[0].coordinates[0][0]=NaN'],
    ['invalid cumulative distance','CHAINS.lidl.routes.optimized.cumulative_metres[1]=-1'],
    ['invalid cumulative duration','CHAINS.lidl.routes.optimized.cumulative_seconds.pop()'],
    ['mismatched total','CHAINS.lidl.routes.optimized.distance_metres=1'],
    ['missing metadata','delete CHAINS.lidl.routes.optimized.metadata'],
    ['missing boundaries','COUNTRIES.features=[]'],
  ]) await negative(name,mutation);
  await check('No unexpected JavaScript errors, failed resource loads or external requests',async()=>{
    assert.deepEqual(diagnostics,{pageErrors:[],failedRequests:[],httpErrors:[],externalRequests:[]});
  });
})().catch(error => {
  results.push({name:'Test harness',status:'FAIL',error:error.stack});
  console.error(error);
}).finally(async()=>{
  const report={generatedAt:new Date().toISOString(),browser:browser?await browser.version():null,
    passed:results.filter(r=>r.status==='PASS').length,failed:results.filter(r=>r.status==='FAIL').length,
    results,diagnostics};
  await fs.writeFile(path.join(root,'publication-browser-results.json'),JSON.stringify(report,null,2)+'\n');
  if(browser) await browser.close();
  if(server) await new Promise(resolve=>server.close(resolve));
  console.log(`RESULT: ${report.passed} passed, ${report.failed} failed`);
  process.exitCode=report.failed?1:0;
});

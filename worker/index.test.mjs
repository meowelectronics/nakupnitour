import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker, { sampleOffers } from './index.mjs';

function environment() {
  const cache = new Map();
  return {
    DATA_MODE: 'sample',
    BRANIK_OFFERS: {
      async get(key) { return cache.has(key) ? JSON.parse(cache.get(key)) : null; },
      async put(key, value) { cache.set(key, value); },
    },
  };
}
const request = () => new Request('https://example.test/api/branik-offers');

test('sample offers are explicit branch matches for 2L PET, not live Kupi data', () => {
  const data = sampleOffers(0);
  assert.equal(data.mode, 'demo');
  assert.equal(data.offers.length, 4);
  assert.equal(Date.parse(data.expiresAt), 21600000);
  for (const offer of data.offers) {
    assert.equal(offer.scope, 'branch');
    assert.equal(offer.storeId, 1);
    assert.equal(offer.volumeLitres, 2);
    assert.equal(offer.packaging, 'PET');
    assert.equal(offer.product, 'Branik');
  }
});
test('GET seeds and reads KV; hourly scheduled refresh persists data', async () => {
  const env = environment();
  const response = await worker.fetch(request(), env);
  assert.equal(response.status, 200);
  const data = await response.json();
  assert.deepEqual(await (await worker.fetch(request(), env)).json(), data);
  let task;
  await worker.scheduled({}, env, { waitUntil(promise) { task = promise; } });
  await task;
  assert.equal((await (await worker.fetch(request(), env)).json()).mode, 'demo');
});
test('expired cached data is refreshed', async () => {
  const env = environment();
  await env.BRANIK_OFFERS.put('branik-2l-v1', JSON.stringify(sampleOffers(0)));
  const data = await (await worker.fetch(request(), env)).json();
  assert(Date.parse(data.expiresAt) > Date.now());
});
test('unknown paths and unsupported methods are rejected', async () => {
  assert.equal((await worker.fetch(new Request('https://example.test/'), environment())).status, 404);
  assert.equal((await worker.fetch(new Request(request(), { method: 'POST' }), environment())).status, 405);
});
test('unconfigured live source and KV failure return explicit 503, not empty success', async t => {
  t.mock.method(console, 'error', () => {});
  const env = environment();
  env.DATA_MODE = 'live';
  assert.equal((await worker.fetch(request(), env)).status, 503);
  env.DATA_MODE = 'sample';
  env.BRANIK_OFFERS.get = async () => { throw new Error('KV unavailable'); };
  assert.equal((await worker.fetch(request(), env)).status, 503);
  assert.equal(console.error.mock.callCount(), 2);
});

const CACHE_KEY = 'branik-2l-v1';
const MAX_AGE = 6 * 60 * 60 * 1000;

export function sampleOffers(now = Date.now()) {
  return {
    version: 1,
    mode: 'demo',
    checkedAt: new Date(now).toISOString(),
    expiresAt: new Date(now + MAX_AGE).toISOString(),
    offers: ['lidl', 'kaufland', 'billa', 'albert'].map(chain => ({
      chain,
      storeId: 1,
      product: 'Branik',
      volumeLitres: 2,
      packaging: 'PET',
      validFrom: new Date(now).toISOString(),
      validUntil: new Date(now + MAX_AGE).toISOString(),
      scope: 'branch',
    })),
  };
}

async function refresh(env) {
  if (env.DATA_MODE !== 'sample') {
    throw new Error('Kupi access is not configured. An approved feed and branch mapping are required.');
  }
  const data = sampleOffers();
  await env.BRANIK_OFFERS.put(CACHE_KEY, JSON.stringify(data), { expirationTtl: MAX_AGE / 1000 });
  return data;
}

export default {
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(refresh(env).catch(error => {
      console.error('Branik offer refresh failed:', error);
      throw error;
    }));
  },
  async fetch(request, env) {
    if (new URL(request.url).pathname !== '/api/branik-offers') {
      return new Response('Not found', { status: 404 });
    }
    if (request.method !== 'GET') {
      return new Response('Method not allowed', { status: 405, headers: { Allow: 'GET' } });
    }
    try {
      if (env.DATA_MODE !== 'sample') throw new Error('Approved offer source is not configured.');
      const cached = await env.BRANIK_OFFERS.get(CACHE_KEY, 'json');
      const data = cached && cached.mode === 'demo' && Date.parse(cached.expiresAt) > Date.now()
        ? cached : await refresh(env);
      return Response.json(data, { headers: { 'Cache-Control': 'no-store' } });
    } catch (error) {
      console.error('Branik offers unavailable:', error);
      return Response.json({ error: 'Offers unavailable. No verified live Kupi source is configured.' }, {
        status: 503,
        headers: { 'Cache-Control': 'no-store' },
      });
    }
  },
};

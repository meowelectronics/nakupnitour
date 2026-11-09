import { createServer } from 'node:http';
import worker from './index.mjs';

const cache = new Map();
const env = {
  DATA_MODE: 'sample',
  BRANIK_OFFERS: {
    async get(key) { const value = cache.get(key); return value ? JSON.parse(value) : null; },
    async put(key, value) { cache.set(key, value); },
  },
};
createServer(async (req, res) => {
  try {
    const request = new Request(new URL(req.url, 'http://127.0.0.1:8787'), { method: req.method });
    const response = await worker.fetch(request, env);
    res.writeHead(response.status, Object.fromEntries(response.headers));
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch (error) {
    console.error('Local Worker request failed:', error);
    res.writeHead(500);
    res.end('Local Worker request failed');
  }
}).listen(8787, '127.0.0.1', () => console.log('Sample Worker: http://127.0.0.1:8787/api/branik-offers'));

import { readFile, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const source = new URL('../src/planner.html', import.meta.url);
const html = await readFile(source, 'utf8');
const match = html.match(/const CHAINS=(.*);\r?\nconst COUNTRIES=/);
assert(match, 'Embedded store data not found.');
const chains = JSON.parse(match[1]);
const recovery = JSON.parse(await readFile(new URL('../address-recovery.json', import.meta.url), 'utf8'));
const seen = new Set();
for (const entry of recovery.recovered) {
  const key = `${entry.chain}/${entry.store_id}`;
  assert(!seen.has(key), `Duplicate address override: ${key}`);
  seen.add(key);
  const feature = chains[entry.chain]?.stores.features.find(f => f.properties.id === entry.store_id);
  assert(feature, `Unknown store: ${key}`);
  assert.equal(feature.properties.address, entry.original_address, `Stale override: ${key}`);
  assert(entry.address.trim() && entry.source && entry.method, `Missing address evidence: ${key}`);
  feature.properties.address = entry.address;
  if (entry.city) feature.properties.city = entry.city;
  feature.properties.address_source = `https://www.openstreetmap.org/${entry.source}`;
  feature.properties.address_method = entry.method;
}
const updated = JSON.stringify(chains).replace(/</g, '\\u003c');
await writeFile(source, html.replace(match[1], () => updated));
console.log(`Applied ${seen.size} verified address updates without changing store IDs, coordinates or routes.`);

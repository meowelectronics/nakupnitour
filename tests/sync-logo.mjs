import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const logo = new URL('../public/nakupnitour-logo.svg', import.meta.url);
const source = new URL('../src/planner.html', import.meta.url);
const html = await readFile(source, 'utf8');
const matches = html.match(/data:image\/svg\+xml;base64,[A-Za-z0-9+/=]+/g);
if (matches?.length !== 1) throw new Error('Expected exactly one embedded SVG logo.');
const encoded = (await readFile(logo)).toString('base64');
await writeFile(source, html.replace(matches[0], `data:image/svg+xml;base64,${encoded}`));

const browser = await chromium.launch({
  channel: process.env.PW_CHANNEL || 'msedge',
  headless: true,
});
try {
  const page = await browser.newPage({ viewport: { width: 3600, height: 840 } });
  await page.goto(logo.href);
  await page.evaluate(() => {
    const svg = document.querySelector('svg');
    svg.style.width = '3600px';
    svg.style.height = '840px';
    document.documentElement.style.background = 'transparent';
  });
  await page.screenshot({
    path: fileURLToPath(new URL('../public/nakupnitour-logo-3600.png', import.meta.url)),
    omitBackground: true,
  });
} finally {
  await browser.close();
}
console.log('Updated embedded SVG and transparent 3600 x 840 PNG.');

/**
 * Worker-tablet PWA icon generator (2026-09-29).
 *
 * One brand mark drives every raster: the goat-head glyph from
 * src/app/icon.svg (brand green #157344 canvas, off-white #f4faf6 stroke —
 * the app's favicon). The raster set follows the platform guidance:
 *
 *  - `purpose: any` icons are FULL-BLEED squares — launchers apply their own
 *    masks, so baked corner radii would double-round. The glyph occupies
 *    ~62% of the canvas.
 *  - the `purpose: maskable` entry follows the Android adaptive-icon spec the
 *    web `maskable` purpose inherits: a 108dp canvas whose guaranteed-visible
 *    area is the central 66dp circle (safe-circle diameter = 66/108 ≈ 61% of
 *    the canvas). The glyph is scaled so its farthest INK edge (path bounds
 *    + half the stroke, round caps included) stays inside that circle, and
 *    the script pixel-verifies the guarantee after rendering.
 *  - apple-icon.png (180×180) is full-bleed for iOS's own corner mask
 *    (iPads ignore manifest icons for Add-to-Home-Screen).
 *
 * Regenerate after touching the glyph or the design tokens:
 *   node scripts/generate-worker-icons.mjs
 */

import { readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = join(HERE, "..");

const CANVAS = "#157344"; // brand green (icon.svg rounded-square fill)
const STROKE = "#f4faf6"; // off-white glyph stroke (icon.svg)
const STROKE_WIDTH = 1.7; // in the glyph's 24-unit space

/** Glyph content box in the 24-unit space (measured from the master paths). */
const BOX = { x0: 3, x1: 21, y0: 3.8, y1: 19.6 };
const CENTER = { x: (BOX.x0 + BOX.x1) / 2, y: (BOX.y0 + BOX.y1) / 2 };

/** Extract the glyph's stroke paths from the master icon.svg. */
async function glyphPaths() {
  const svg = await readFile(join(FRONTEND, "src", "app", "icon.svg"), "utf8");
  const paths = [...svg.matchAll(/<path d="([^"]+)"\/>/g)].map((m) => m[1]);
  if (paths.length < 6) throw new Error("icon.svg glyph shape changed — update the generator");
  return paths;
}

/**
 * Radius (from the glyph's center, in 24-unit space) of the farthest possible
 * ink from any path. Walks the path data with a current-point cursor so
 * relative commands (lowercase c/s/v) resolve to absolute coordinates, and
 * takes every anchor AND Bézier control point: curves stay inside the convex
 * hull of their control points, so this is a conservative bound. The stroke's
 * half-width covers both edges and the round line caps.
 */
function inkRadius(paths) {
  let max = 0;
  const consider = (x, y) => {
    max = Math.max(max, Math.hypot(x - CENTER.x, y - CENTER.y));
  };
  for (const d of paths) {
    const tokens = d.match(/[MmLlHhVvCcSsZz]|-?\d*\.?\d+/g) ?? [];
    let i = 0;
    let cmd = null;
    let cx = 0;
    let cy = 0;
    const isNum = (t) => /^[-\d.]/.test(t);
    const nums = () => {
      const out = [];
      while (i < tokens.length && isNum(tokens[i])) out.push(Number(tokens[i++]));
      return out;
    };
    while (i < tokens.length) {
      if (/^[A-Za-z]$/.test(tokens[i])) cmd = tokens[i++];
      if (!cmd) throw new Error(`path data starts with a number: ${d}`);
      const upper = cmd.toUpperCase();
      const rel = cmd !== upper;
      if (upper === "M" || upper === "L") {
        const n = nums();
        for (let k = 0; k + 1 < n.length; k += 2) {
          cx = rel ? cx + n[k] : n[k];
          cy = rel ? cy + n[k + 1] : n[k + 1];
          consider(cx, cy);
        }
        if (upper === "M") cmd = rel ? "l" : "L"; // later pairs are implicit lineto
      } else if (upper === "C" || upper === "S") {
        const stride = upper === "C" ? 6 : 4;
        const n = nums();
        for (let k = 0; k + stride - 1 < n.length; k += stride) {
          for (let p = 0; p + 1 < stride; p += 2) {
            consider(rel ? cx + n[k + p] : n[k + p], rel ? cy + n[k + p + 1] : n[k + p + 1]);
          }
          const lx = n[k + stride - 2];
          const ly = n[k + stride - 1];
          cx = rel ? cx + lx : lx;
          cy = rel ? cy + ly : ly;
        }
      } else if (upper === "V" || upper === "H") {
        for (const v of nums()) {
          if (upper === "V") cy = rel ? cy + v : v;
          else cx = rel ? cx + v : v;
          consider(cx, cy);
        }
      } else if (upper === "Z") {
        i++;
      } else {
        throw new Error(`unsupported path command ${cmd} — extend the parser`);
      }
    }
  }
  return max + STROKE_WIDTH / 2;
}

/**
 * Compose one icon SVG at `size`×`size`. `glyphFraction` is the fraction of
 * the canvas the glyph's LARGER content dimension occupies (0.62 for `any`);
 * `scale` overrides the fraction when a geometric bound must drive sizing
 * (the maskable safe circle).
 */
function iconSvg(size, paths, { fraction = null, scale = null }) {
  const contentW = BOX.x1 - BOX.x0;
  const contentH = BOX.y1 - BOX.y0;
  if (scale === null) scale = (size * fraction) / Math.max(contentW, contentH);
  const tx = (size - contentW * scale) / 2 - BOX.x0 * scale;
  const ty = (size - contentH * scale) / 2 - BOX.y0 * scale;
  const body = paths
    .map((d) => `<path d="${d}"/>`)
    .join("\n    ");
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
  <rect width="${size}" height="${size}" fill="${CANVAS}"/>
  <g fill="none" stroke="${STROKE}" stroke-width="${STROKE_WIDTH}" stroke-linecap="round" stroke-linejoin="round" transform="translate(${tx} ${ty}) scale(${scale})">
    ${body}
  </g>
</svg>
`;
}

async function render(svg, target) {
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  await writeFile(join(FRONTEND, target), buf);
  console.log(`wrote ${target}`);
  return buf;
}

/**
 * Pixel-verify the maskable guarantee: every glyph-ink pixel must sit inside
 * the spec's safe circle (diameter 66/108 of the canvas). Exits non-zero on
 * any violation, so a glyph/geometry change can never silently break the
 * safe zone.
 */
async function assertMaskableSafe(buf, target) {
  const { data, info } = await sharp(buf).raw().toBuffer({ resolveWithObject: true });
  const c = info.channels;
  const cx = info.width / 2;
  const cy = info.height / 2;
  const safeRadius = (info.width * (66 / 108)) / 2;
  let worst = 0;
  for (let y = 0; y < info.height; y++) {
    for (let x = 0; x < info.width; x++) {
      const i = (y * info.width + x) * c;
      const isInk = data[i] > 200 && data[i + 1] > 200 && data[i + 2] > 200;
      if (!isInk) continue;
      worst = Math.max(worst, Math.hypot(x + 0.5 - cx, y + 0.5 - cy));
    }
  }
  if (worst > safeRadius) {
    throw new Error(
      `${target}: ink reaches radius ${worst.toFixed(1)}px, safe circle is ${safeRadius.toFixed(1)}px`,
    );
  }
  console.log(
    `  safe-zone ok: farthest ink ${worst.toFixed(1)}px ≤ ${safeRadius.toFixed(1)}px (66/108 circle)`,
  );
}

const paths = await glyphPaths();

// purpose: any — full-bleed, glyph at 62% (launchers mask their own corners).
await render(iconSvg(192, paths, { fraction: 0.62 }), "public/icon-worker-192.png");
await render(iconSvg(512, paths, { fraction: 0.62 }), "public/icon-worker-512.png");

// purpose: maskable — every ink edge inside the 66dp-of-108dp safe circle
// (safe circle diameter = 66/108 of the canvas → radius = 33/108).
const maskableSize = 512;
const safeRadius = maskableSize * (33 / 108);
const maskableScale = (safeRadius * 0.98) / inkRadius(paths);
const maskable = await render(
  iconSvg(maskableSize, paths, { scale: maskableScale }),
  "public/icon-worker-512-maskable.png",
);
await assertMaskableSafe(maskable, "public/icon-worker-512-maskable.png");

// iOS Add-to-Home-Screen (Next serves src/app/apple-icon.png automatically).
await render(iconSvg(180, paths, { fraction: 0.6 }), "src/app/apple-icon.png");

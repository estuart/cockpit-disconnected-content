import { build } from 'esbuild';
import { mkdir, copyFile, rm } from 'node:fs/promises';
await rm('dist', { recursive: true, force: true });
await mkdir('dist', { recursive: true });
await build({ entryPoints: ['src/index.tsx'], bundle: true, minify: true, outdir: 'dist',
  target: ['es2022'], loader: { '.woff2': 'file', '.woff': 'file', '.ttf': 'file', '.svg': 'file' },
  assetNames: 'assets/[name]-[hash]', legalComments: 'linked' });
for (const name of ['index.html', 'manifest.json']) await copyFile(`src/${name}`, `dist/${name}`);

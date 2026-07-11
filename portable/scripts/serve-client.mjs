/**
 * Lightweight static file server for hermes_client's Vite-built frontend.
 * Injects runtime API config into HTML so the client connects to the
 * correct API port regardless of hostname.
 *
 * Usage: node serve-client.mjs
 * Env:   CLIENT_PORT (default 18888), API_PORT (default 18889)
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST = path.join(__dirname, 'client', 'dist');
const PORT = Number(process.env.CLIENT_PORT) || 18888;
const API_PORT = Number(process.env.API_PORT) || 18889;

const MIME = {
  '.html': 'text/html', '.js': 'application/javascript', '.mjs': 'application/javascript',
  '.css': 'text/css', '.json': 'application/json',
  '.webmanifest': 'application/manifest+json',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
  '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.webp': 'image/webp',
};

function injectConfig(html, hostHeader) {
  const hostname = (hostHeader || '').split(':')[0] || 'localhost';
  const apiBaseUrl = 'http://' + hostname + ':' + API_PORT + '/api';
  const cfg = JSON.stringify({ apiBaseUrl, apiPort: API_PORT });
  const tag = '<script>window.__HERMES_CONFIG__=' + cfg + ';</script>';
  if (html.includes('</head>')) return html.replace('</head>', tag + '</head>');
  return tag + html;
}

http.createServer((req, res) => {
  let filePath = path.join(DIST, req.url === '/' ? 'index.html' : req.url.split('?')[0]);
  if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
    filePath = path.join(DIST, 'index.html');
  }
  const ext = path.extname(filePath);
  const mime = MIME[ext] || 'application/octet-stream';
  try {
    if (mime === 'text/html') {
      const html = fs.readFileSync(filePath, 'utf-8');
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
      res.end(injectConfig(html, req.headers.host));
      return;
    }
    res.writeHead(200, { 'Content-Type': mime });
    res.end(fs.readFileSync(filePath));
  } catch { res.writeHead(404); res.end('Not found'); }
}).listen(PORT, '0.0.0.0', () => console.log('Client on http://localhost:' + PORT));

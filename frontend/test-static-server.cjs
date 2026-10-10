// Local-only server for testing the already built release assets offline.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, 'dist');
http.createServer((req, res) => {
  let filename;
  try {
    const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    filename = path.resolve(root, '.' + pathname);
    if (filename !== root && !filename.startsWith(root + path.sep)) {
      res.writeHead(403); res.end(); return;
    }
    if (!fs.existsSync(filename) || fs.statSync(filename).isDirectory()) filename = path.join(root, 'index.html');
    const mime = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };
    res.writeHead(200, { 'Content-Type': mime[path.extname(filename)] || 'application/octet-stream' });
    fs.createReadStream(filename).pipe(res);
  } catch {
    res.writeHead(400); res.end();
  }
}).listen(4174, '127.0.0.1');

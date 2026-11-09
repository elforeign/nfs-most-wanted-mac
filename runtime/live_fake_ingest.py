#!/usr/bin/env python3
"""live_fake_ingest.py <dir> - a loopback-only fake of YouTube's HLS HTTP ingest for unattended live runs.

Listens on 127.0.0.1 (ephemeral port, written to <dir>/port) and accepts the POST/PUT of TS segments and media
playlists that runtime/livestream.mm sends to /http_upload_hls?cid=..&file=<n>.ts|live.m3u8. Answers 202 for a
segment and 200 for a playlist (as the real ingest did in the reported session). Each request appends one line to
<dir>/ingest.tsv: wall seconds, file, bytes, ok (TS: whole 188-byte packets, sync 0x47, PAT first; playlist: #EXTM3U),
max EXTINF seconds (playlists). Exits on SIGTERM/SIGINT. Never binds a non-loopback address.
"""
import http.server
import os
import re
import signal
import sys
import time
import urllib.parse


def ts_ok(body):
    if not body or len(body) % 188:
        return False
    if any(body[i] != 0x47 for i in range(0, len(body), 188)):
        return False
    return ((body[1] & 31) << 8 | body[2]) == 0  # PAT first


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    log = open(os.path.join(out, 'ingest.tsv'), 'a', buffering=1)
    start = time.monotonic()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def handle_upload(self):
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            name = (query.get('file') or [''])[0]
            length = int(self.headers.get('Content-Length') or 0)
            body = self.rfile.read(length) if length > 0 else b''
            extinf = 0.0
            if url.path != '/http_upload_hls' or not query.get('cid'):
                ok, code = False, 404
            elif name.endswith('.ts'):
                ok, code = ts_ok(body), 202
            elif name.endswith('.m3u8'):
                text = body.decode('utf-8', 'replace')
                ok, code = text.startswith('#EXTM3U'), 200
                durations = [float(v) for v in re.findall(r'#EXTINF:([0-9.]+)', text)]
                extinf = max(durations) if durations else 0.0
            else:
                ok, code = False, 400
            log.write(f'{time.monotonic() - start:.3f}\t{name}\t{len(body)}\t{int(ok)}\t{extinf:.3f}\n')
            self.send_response(code)
            self.send_header('Content-Length', '0')
            self.end_headers()

        do_POST = handle_upload
        do_PUT = handle_upload

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    with open(os.path.join(out, 'port.tmp'), 'w') as f:
        f.write(str(server.server_address[1]))
    os.replace(os.path.join(out, 'port.tmp'), os.path.join(out, 'port'))

    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

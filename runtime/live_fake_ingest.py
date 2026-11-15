#!/usr/bin/env python3
"""live_fake_ingest.py <dir> - a loopback-only fake of YouTube's HLS HTTP ingest for unattended live runs.

Listens on 127.0.0.1 (ephemeral port, written to <dir>/port) and accepts the POST/PUT of TS segments and media
playlists that runtime/livestream.mm sends to /http_upload_hls?cid=..&file=<n>.ts|live.m3u8. Answers 202 for a
segment and 200 for a playlist (as the real ingest did in the reported session). Each request appends one line to
<dir>/ingest.tsv: wall seconds, file, bytes, ok (TS: whole 188-byte packets, sync 0x47, PAT first; playlist: #EXTM3U),
max EXTINF seconds (playlists), ENDLIST, generation and referenced uploaded filenames. Exits on SIGTERM/SIGINT. Never binds a non-loopback address.
"""
import http.server
import os
import re
import signal
import sys
import time
import threading
import urllib.parse


def ts_ok(body):
    if not body or len(body) % 188:
        return False
    if any(body[i] != 0x47 for i in range(0, len(body), 188)):
        return False
    return ((body[1] & 31) << 8 | body[2]) == 0  # PAT first


class IngestState:
    """Validate generation ordering, acknowledged tail snapshots and one EOS."""
    def __init__(self):
        self.generations = {}
        self.lock = threading.Lock()

    def upload(self, name, body):
        with self.lock:
            return self._upload(name, body)

    def _upload(self, name, body):
        if name.endswith('.ts'):
            match = re.fullmatch(r'([A-Fa-f0-9-]{36})-(\d+)\.ts', name)
            if not match:
                return False, 0.0, False, '-', []
            generation, number = match[1], int(match[2])
            state = self.generations.setdefault(generation, dict(names=[], final=False, tail=None))
            ok = ts_ok(body) and not state['final'] and number == len(state['names'])
            if ok:
                state['names'].append(name)
            return ok, 0.0, False, generation, []
        if name != 'live.m3u8':
            return False, 0.0, False, '-', []
        try:
            text = body.decode('utf-8')
            lines = text.splitlines()
            names = [line for line in lines if line and not line.startswith('#')]
            durations = [float(v) for v in re.findall(r'^#EXTINF:([0-9.]+),$', text, re.M)]
            eos = '#EXT-X-ENDLIST' in lines
            assert lines[0] == '#EXTM3U' and 1 <= len(names) <= 3
            generation = names[0].rsplit('-', 1)[0]
            state = self.generations[generation]
            assert not state['final'] and names == state['names'][-3:]
            assert len(durations) == len(names) and all(0 < d <= 5 for d in durations)
            tags = dict(line.split(':', 1) for line in lines if ':' in line and not line.startswith('#EXTINF:'))
            assert tags['#EXT-X-VERSION'] == '3' and tags['#EXT-X-TARGETDURATION'] == '5'
            assert int(tags['#EXT-X-MEDIA-SEQUENCE']) == len(state['names'])-len(names)
            assert all(lines[lines.index(n)-1].startswith('#EXTINF:') for n in names)
            assert text.count('#EXT-X-ENDLIST') == int(eos)
            if eos:
                assert text.endswith('#EXT-X-ENDLIST\n') and body == state['tail']+b'#EXT-X-ENDLIST\n'
                state['final'] = True
            else:
                state['tail'] = body
            return True, max(durations), eos, generation, names
        except (ValueError, KeyError, IndexError, AssertionError, UnicodeError, TypeError):
            return False, 0.0, False, '-', []


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    log = open(os.path.join(out, 'ingest.tsv'), 'a', buffering=1)
    start = time.monotonic()
    state = IngestState()

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
            ok, extinf, eos, generation, names = state.upload(name, body)
            if url.path != '/http_upload_hls' or not query.get('cid'):
                ok = False
            code = (202 if name.endswith('.ts') else 200) if ok else 400
            try:
                self.send_response(code)
                self.send_header('Content-Length', '0')
                self.end_headers()
                self.wfile.flush()
                log.write(f'{time.monotonic() - start:.3f}\t{name}\t{len(body)}\t{int(ok)}\t{extinf:.3f}\t{int(eos)}\t{generation}\t{",".join(names)}\n')
            except (BrokenPipeError, ConnectionResetError):
                pass

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

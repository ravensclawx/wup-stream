#!/usr/bin/env python3
"""Stress the stream: hard video sources played on the GamePad (wupstream waiting, on this network), with the pad's
own counters for each. Needs ffmpeg and a built build/host/pipe (host/build.sh).

    python3 tools/stress.py [--seconds 20] [--only name,name] [--pad IP]

Per test: x264's bitrate, frames shown of those expected (60 a second), keyframes (each restarts the decoder: a brief roll
at the top), frames lost on the way, stalls (the decoder went idle: a keyframe restarted it), receive ring overruns, frames skipped for a full decoder ring, the most data waiting in
the decoder's ring, refreshes the PC let pass for the pad's decoder, and refreshes it filled with the last picture again
(the test source had no new frame in time)."""
import os, re, socket, subprocess, sys, threading, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = '854x480:rate=60'
TESTS = [('still', 'color=c=0x204060:size=%s' % S),                         # tiny frames: the decoder reads ahead
         ('bars', 'testsrc2=size=%s' % S),                                   # moving shapes and text
         ('mandelbrot', 'mandelbrot=size=%s' % S),                           # dense detail everywhere
         ('life', 'life=size=%s:mold=10:ratio=0.5:death_color=#205080:life_color=#f0d040' % S),   # detail changing every frame
         ('flash', 'color=c=red:size=%s,hue=H=PI*n' % S),                    # the whole screen changes every frame
         ('cuts', 'testsrc2=size=%s[a];testsrc=size=%s,hue=h=t*40[b];[a][b]overlay=enable=lt(mod(t\\,1)\\,0.5)' % (S, S)),   # a scene cut twice a second: very large frames
         ('noise', 'color=c=gray:size=%s,noise=alls=100:allf=t' % S)]        # nothing to predict: always at the cap


def main():
    a = sys.argv[1:]
    secs = int(a[a.index('--seconds') + 1]) if '--seconds' in a else 20
    only = a[a.index('--only') + 1].split(',') if '--only' in a else None
    pad = [a[a.index('--pad') + 1]] if '--pad' in a else []
    lines = []
    log = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    log.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try: log.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError): pass
    log.bind(('', 28803))                                                  # the pad's log lines (pad/stream/stream.c say())
    threading.Thread(target=lambda: [lines.append(log.recvfrom(2048)[0].decode('utf-8', 'replace')) for _ in iter(int, 1)], daemon=True).start()
    print('%-11s %6s %11s %4s %5s %6s %6s %8s %5s %8s %7s %8s' % ('test', 'Mbit/s', 'frames', 'keys', 'lost', 'resent', 'stalls', 'overruns', 'full', 'queue KB', 'skipped', 'repeats'))
    for name, src in TESTS:
        if only and name not in only: continue
        n0 = len(lines)
        ff = subprocess.Popen(['ffmpeg', '-hide_banner', '-v', 'error', '-f', 'lavfi', '-i', src, '-t', str(secs),
                               '-f', 'rawvideo', '-pix_fmt', 'rgba', '-'], stdout=subprocess.PIPE)
        r = subprocess.run([os.path.join(ROOT, 'build', 'host', 'pipe')] + pad, stdin=ff.stdout, capture_output=True, text=True,
                           env=dict(os.environ, WUPSTREAM_DEBUG='1'))
        ff.wait(); time.sleep(2.5)                                         # (the pad's last report, and back to waiting)
        rate = re.findall(r'kb/s:([\d.]+)', r.stderr)
        dbg = re.search(r'(\d+) refreshes skipped.* (\d+) refreshes filled', r.stderr)
        stats = [dict(zip(re.findall(r'([a-z-]+) \d+', l), map(int, re.findall(r' (\d+)', l)))) for l in lines[n0:] if l.startswith('frames')]
        if not stats: print('%-11s no report from the pad%s' % (name, ': ' + r.stderr.strip()[-200:] if r.returncode else '')); continue
        last = stats[-1]
        print('%-11s %6.1f %5d/%-5d %4d %5d %6d %6d %8d %5d %8d %7s %7s' % (name, float(rate[-1]) / 1000 if rate else 0, last['frames'], last['ms'] * 60 // 1001,
              last['keyframes'], last['lost'], last.get('resent', 0), last['stalls'], last['overruns'], last['full'], max(s['queue-kb'] for s in stats),
              dbg.group(1) if dbg else '?', dbg.group(2) if dbg else '?'))


if __name__ == '__main__':
    main()

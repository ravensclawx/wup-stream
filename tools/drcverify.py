#!/usr/bin/env python3
"""Check a GamePad (DRC) H.264 stream in software.

    python3 tools/drcverify.py STREAM.h264 SOURCE.yuv|- W H [--slices N] [--dump-ring DIR]

STREAM is Annex B from the DRC-patched x264 (drh-mode): its slice NALs carry no slice header, exactly
what the GamePad's hardware decoder takes (the pad strips start code + NAL header and unescapes the
rest into its bitstream ring). This rebuilds a normal stream from it, putting back the slice header the
hardware assumes (first_mb: each slice's place, from N slices of equal rows, slice QP 32 = pic_init_qp, cabac_init_idc 0, deblocking on, one
reference), decodes it with FFmpeg and compares every frame with SOURCE (PSNR). If the headerless data
were not what the hardware expects (planar prediction, other QP, a header left in), decoding breaks or
the PSNR collapses. --dump-ring writes each frame's ring bytes (frame_NNN.bin) for tests on the pad."""
import os, subprocess, sys, tempfile, math


def nals(data):
    i, out = 0, []
    starts = []
    while True:
        j = data.find(b'\0\0\1', i)
        if j < 0: break
        starts.append(j + 3); i = j + 3
    for k, s in enumerate(starts):
        e = starts[k + 1] - 3 if k + 1 < len(starts) else len(data)
        n = data[s:e]
        while k + 1 < len(starts) and n.endswith(b'\0'): n = n[:-1]        # 4-byte start code of the next
        out.append(n)
    return out


def unescape(b):
    out, z = bytearray(), 0
    for x in b:
        if z >= 2 and x == 3: z = 0; continue
        out.append(x); z = z + 1 if x == 0 else 0
    return bytes(out)


def escape(b):
    out, z = bytearray(), 0
    for x in b:
        if z >= 2 and x <= 3: out.append(3); z = 0
        out.append(x); z = z + 1 if x == 0 else 0
    return bytes(out)


class BR:
    def __init__(s, b): s.b, s.p = b, 0
    def u(s, n):
        v = 0
        for _ in range(n): v = v << 1 | (s.b[s.p >> 3] >> (7 - (s.p & 7)) & 1); s.p += 1
        return v
    def ue(s):
        z = 0
        while not s.u(1): z += 1
        return (1 << z) - 1 + s.u(z)
    def se(s):
        v = s.ue(); return (v + 1) // 2 if v & 1 else -(v // 2)


class BW:
    def __init__(s): s.bits = []
    def u(s, n, v): s.bits += [(v >> (n - 1 - i)) & 1 for i in range(n)]
    def ue(s, v):
        v += 1; n = v.bit_length(); s.u(n - 1, 0); s.u(n, v)
    def se(s, v): s.ue(2 * v - 1 if v > 0 else -2 * v)
    def align1(s):
        while len(s.bits) % 8: s.bits.append(1)
    def bytes(s):
        return bytes(int(''.join(map(str, s.bits[i:i + 8])), 2) for i in range(0, len(s.bits), 8))


def parse_sps(n):
    r = BR(unescape(n[1:]))
    prof = r.u(8); r.u(16); r.ue()
    if prof in (100, 110, 122, 244, 44, 83, 86, 118, 128):
        cf = r.ue()
        if cf == 3: r.u(1)
        r.ue(); r.ue(); r.u(1)
        if r.u(1): raise SystemExit('scaling matrices not handled')
    s = {'log2_fn': r.ue() + 4, 'poc_type': r.ue()}
    if s['poc_type'] == 0: s['log2_poc'] = r.ue() + 4
    elif s['poc_type'] == 1: raise SystemExit('poc type 1 not handled')
    s['refs'] = r.ue(); r.u(1)
    s['w'] = (r.ue() + 1) * 16; s['h'] = (r.ue() + 1) * 16
    if not r.u(1): raise SystemExit('interlaced not handled')
    return s


def parse_pps(n):
    r = BR(unescape(n[1:]))
    r.ue(); r.ue()
    p = {'cabac': r.u(1), 'bottom_poc': r.u(1)}
    if r.ue(): raise SystemExit('slice groups not handled')
    p['nref0'] = r.ue() + 1; r.ue()
    p['wp'] = r.u(1); p['wbp'] = r.u(2)
    p['init_qp'] = 26 + r.se(); r.se(); p['cqp_off'] = r.se()
    p['dbk_ctrl'] = r.u(1); p['cip'] = r.u(1); p['redund'] = r.u(1)
    return p


def main():
    a = sys.argv[1:]
    if len(a) < 4: sys.exit(__doc__)
    stream, src, W, H = a[0], a[1], int(a[2]), int(a[3])
    ring = a[a.index('--dump-ring') + 1] if '--dump-ring' in a else None
    nsl = int(a[a.index('--slices') + 1]) if '--slices' in a else 1
    sl = 0
    data = open(stream, 'rb').read()
    sps = pps = None
    out, frame, fn, idr_id, since_idr, issues = bytearray(), 0, 0, 0, 0, []
    planes = []
    for n in nals(data):
        t = n[0] & 31
        if t == 7: sps = parse_sps(n); out += b'\0\0\0\1' + n
        elif t == 8:
            pps = parse_pps(n); out += b'\0\0\0\1' + n
            if pps['init_qp'] != 32: issues.append('pic_init_qp %d, the GamePad assumes 32' % pps['init_qp'])
            if pps['cqp_off'] != 0: issues.append('chroma_qp_index_offset %d (want 0)' % pps['cqp_off'])
            if not pps['cabac']: issues.append('CAVLC')
            if not pps['cip']: issues.append('constrained intra off')
            if pps['wp']: issues.append('weighted prediction on')
        elif t in (1, 5):
            idr = t == 5
            body = unescape(n[1:])
            if ring:
                os.makedirs(ring, exist_ok=True)
                open(os.path.join(ring, 'frame_%03d.bin' % frame), 'wb').write(body)
            if idr and sl == 0: fn = 0; since_idr = 0
            w = BW()
            mbs = ((W + 15) // 16) * ((H + 15) // 16)
            w.ue(mbs // nsl * sl); w.ue(7 if idr else 5); w.ue(0)
            w.u(sps['log2_fn'], fn % (1 << sps['log2_fn']))
            if idr: w.ue(idr_id)
            if sps['poc_type'] == 0:
                w.u(sps['log2_poc'], (2 * since_idr) % (1 << sps['log2_poc']))
                if pps['bottom_poc']: w.se(0)
            if not idr:
                w.u(1, 0)                         # num_ref_idx_active_override_flag
                w.u(1, 0)                         # ref_pic_list_modification_flag_l0
            if n[0] >> 5:                         # nal_ref_idc: dec_ref_pic_marking
                if idr: w.u(1, 0); w.u(1, 0)
                else: w.u(1, 0)
            if not idr: w.ue(0)                   # cabac_init_idc
            w.se(32 - pps['init_qp'])             # slice_qp_delta: slice QP 32
            if pps['dbk_ctrl']: w.ue(0); w.se(0); w.se(0)
            w.align1()
            out += b'\0\0\0\1' + bytes([n[0]]) + escape(w.bytes() + body)
            sl += 1
            if sl < nsl: continue
            sl = 0
            if idr: idr_id = (idr_id + 1) % 2
            fn += 1; since_idr += 1; frame += 1
    if not sps: sys.exit('no SPS in the stream')
    tmp = tempfile.mkdtemp()
    h264, yuv = os.path.join(tmp, 'rebuilt.h264'), os.path.join(tmp, 'dec.yuv')
    open(h264, 'wb').write(out)
    if os.environ.get('DRC_KEEP'): open(os.environ['DRC_KEEP'], 'wb').write(out)
    r = subprocess.run(['ffmpeg', '-hide_banner', '-v', 'error', '-y', '-err_detect', 'explode', '-f', 'h264', '-i', h264,
                        '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'yuv420p', yuv], capture_output=True, text=True)
    errs = r.stderr.strip()
    dec = open(yuv, 'rb').read() if os.path.exists(yuv) else b''
    ref = open(src, 'rb').read() if src != '-' else dec          # '-': no source, only check decoding
    fsz = W * H * 3 // 2
    nd = len(dec) // fsz
    worst, tot = 99.0, 0.0
    for i in range(nd):
        d, s = dec[i * fsz:i * fsz + W * H], ref[i * fsz:i * fsz + W * H]
        mse = sum((x - y) ** 2 for x, y in zip(d[::7], s[::7])) / len(d[::7])
        p = 99.0 if mse == 0 else 10 * math.log10(255 * 255 / mse)
        worst = min(worst, p); tot += p
    print('%d frames encoded, %d decoded; luma PSNR avg %.1f dB, worst %.1f dB; SPS %dx%d log2_frame_num %d poc_type %d'
          % (frame, nd, tot / max(nd, 1), worst, sps['w'], sps['h'], sps['log2_fn'], sps['poc_type']))
    for i in issues: print('  ISSUE:', i)
    if errs: print('  decoder errors:\n   ', errs.replace('\n', '\n    ')[:2000])
    ok = nd == frame and not errs and not issues and (worst > 25 or src == '-')
    print('OK' if ok else 'FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()

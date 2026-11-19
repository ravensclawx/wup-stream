/* sketch: draw on the GamePad with your finger; the computer holds the picture and streams it back.
 *   build/host/sketch [pad IP]        WUP Stream must be open on the GamePad; HOME ends it
 * Touch draws (a soft tone follows the pen: higher towards the top). Left/Right: the colour. Up/Down: the brush.
 * X: a clean page (with a short rumble). Everything the GamePad shows is this program's picture: the line appears
 * under your finger after a trip to the computer and back. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "wupstream.h"
#ifndef M_PI
#define M_PI 3.14159265358979
#endif
#define RGB(r, g, b) ((uint32_t)(r) | (uint32_t)(g) << 8 | (uint32_t)(b) << 16)   /* (WUPSTREAM_RGBA, little-endian) */

static const uint32_t ink[] = { RGB(28, 28, 30), RGB(255, 59, 48), RGB(255, 149, 0), RGB(255, 204, 0), RGB(52, 199, 89), RGB(0, 122, 255), RGB(175, 82, 222) };
#define NINK (int)(sizeof ink / sizeof ink[0])
#define PAPER RGB(250, 248, 244)
static uint32_t page[WUPSTREAM_W * WUPSTREAM_H], screen[WUPSTREAM_W * WUPSTREAM_H];

static void dot(uint32_t *px, int x, int y, int r, uint32_t c) {
    for (int j = -r; j <= r; j++)
        for (int i = -r; i <= r; i++)
            if (i * i + j * j <= r * r && x + i >= 0 && x + i < WUPSTREAM_W && y + j >= 0 && y + j < WUPSTREAM_H) px[(y + j) * WUPSTREAM_W + x + i] = c;
}
static void line(int x0, int y0, int x1, int y1, int r, uint32_t c) {      /* (a dot every pixel: smooth at any speed) */
    int n = abs(x1 - x0) > abs(y1 - y0) ? abs(x1 - x0) : abs(y1 - y0);
    for (int k = 0; k <= n; k++) dot(page, x0 + (x1 - x0) * k / (n ? n : 1), y0 + (y1 - y0) * k / (n ? n : 1), r, c);
}

int main(int argc, char **argv) {
    printf("looking for a GamePad...\n");
    wupstream *pad = wupstream_open(argc > 1 ? argv[1] : NULL, 30000);
    if (!pad) { printf("none found: is WUP Stream open on the GamePad, on the same network?\n"); return 1; }
    printf("connected: draw on the GamePad; HOME ends it\n");
    for (int i = 0; i < WUPSTREAM_W * WUPSTREAM_H; i++) page[i] = PAPER;
    int colour = 0, size = 6, lastx = -1, lasty = -1, rumble = 0;
    double phase = 0, level = 0, pitch = 440, due = 0;
    static int16_t snd[802 * 2];
    uint32_t was = 0;
    wupstream_input_t in;
    while (wupstream_input(pad, &in) == 0 && !(in.buttons & WUPSTREAM_HOME)) {
        uint32_t press = in.buttons & ~was; was = in.buttons;
        if (press & WUPSTREAM_RIGHT) colour = (colour + 1) % NINK;
        if (press & WUPSTREAM_LEFT) colour = (colour + NINK - 1) % NINK;
        if (press & WUPSTREAM_UP && size < 24) size += 3;
        if (press & WUPSTREAM_DOWN && size > 3) size -= 3;
        if (press & WUPSTREAM_X) { for (int i = 0; i < WUPSTREAM_W * WUPSTREAM_H; i++) page[i] = PAPER; rumble = 8; }
        wupstream_rumble(pad, rumble > 0); if (rumble) rumble--;

        if (in.touched) {                                   /* the pen */
            if (lastx < 0) { lastx = in.tx; lasty = in.ty; }
            line(lastx, lasty, in.tx, in.ty, size, ink[colour]);
            lastx = in.tx; lasty = in.ty;
            pitch = 220 + (WUPSTREAM_H - in.ty) * 660.0 / WUPSTREAM_H;
        } else lastx = -1;

        memcpy(screen, page, sizeof page);                  /* the page, and on it the brush: its colour and size */
        dot(screen, 36, 36, size + 4, RGB(200, 198, 194)); dot(screen, 36, 36, size, ink[colour]);

        due += 48000 / 59.94; int n = (int)due; due -= n;   /* a soft tone while the pen is down (800.8 per frame) */
        for (int i = 0; i < n; i++) {
            level += ((in.touched ? 0.18 : 0) - level) * 0.002;
            int16_t s = (int16_t)(sin(phase) * level * 32767 * 0.5); phase += 2 * M_PI * pitch / 48000;
            snd[2 * i] = snd[2 * i + 1] = s;
        }
        if (wupstream_frame(pad, screen, WUPSTREAM_W * 4, WUPSTREAM_RGBA) || wupstream_audio(pad, snd, n)) break;
    }
    printf("done\n");
    wupstream_close(pad);
    return 0;
}

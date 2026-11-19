/* pipe: show raw video on the GamePad, e.g. any video file through ffmpeg:
 *   ffmpeg -i video.mp4 -vf scale=854:480 -f rawvideo -pix_fmt rgba - | build/host/pipe [pad IP]
 * (854 x 480 RGBA frames on stdin, shown at the GamePad's 59.94 a second; no sound) */
#include <stdio.h>
#include "wupstream.h"

int main(int argc, char **argv) {
    static unsigned char px[WUPSTREAM_W * WUPSTREAM_H * 4];
    static int16_t silence[800 * 2];
    wupstream *pad = wupstream_open(argc > 1 ? argv[1] : NULL, 30000);
    if (!pad) { fprintf(stderr, "no GamePad found\n"); return 1; }
    wupstream_input_t in;
    while (fread(px, 1, sizeof px, stdin) == sizeof px && !wupstream_input(pad, &in))
        if (wupstream_frame(pad, px, WUPSTREAM_W * 4, WUPSTREAM_RGBA) || wupstream_audio(pad, silence, 800)) break;
    wupstream_close(pad);
    return 0;
}

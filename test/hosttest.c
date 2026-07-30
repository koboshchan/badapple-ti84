/**
 * Runs the calculator's decoder (src/video.c) on the host.
 *
 *     test/hosttest DATADIR OUTFILE
 *
 * Decodes every frame out of the chunk .bin files in DATADIR and writes them to
 * OUTFILE as raw 1bpp frames. util/verify.py --dump produces the same file from
 * the independent Python reference decoder; if the two are byte-identical, the
 * shipping C decoder agrees with the encoder. Driven by `make hosttest`.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../src/lcd.h"
#include "../src/video.h"
#include "fileioc.h"

#define MAX_VARS 136

static char data_dir[512] = ".";

static struct {
    bool used;
    uint8_t *data;
    long size;
} vars[MAX_VARS];

void host_SetDataDir(const char *dir)
{
    snprintf(data_dir, sizeof data_dir, "%s", dir);
}

uint8_t ti_Open(const char *name, const char *mode)
{
    (void)mode;

    /* Variables are stored as lowercase <name>.bin, matching util/encode.py. */
    char lower[16] = {0};
    for (size_t i = 0; i < sizeof lower - 1 && name[i]; i++) {
        char c = name[i];
        lower[i] = (c >= 'A' && c <= 'Z') ? (char)(c - 'A' + 'a') : c;
    }

    char path[600];
    snprintf(path, sizeof path, "%s/%s.bin", data_dir, lower);
    FILE *f = fopen(path, "rb");
    if (!f) {
        return 0;  /* fileioc reports "not found" as handle 0 */
    }

    uint8_t handle = 0;
    for (uint8_t i = 1; i < MAX_VARS; i++) {
        if (!vars[i].used) {
            handle = i;
            break;
        }
    }
    if (!handle) {
        fclose(f);
        fprintf(stderr, "hosttest: out of variable handles\n");
        exit(2);
    }

    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *buf = malloc((size_t)size);
    if (!buf || fread(buf, 1, (size_t)size, f) != (size_t)size) {
        fprintf(stderr, "hosttest: failed reading %s\n", path);
        exit(2);
    }
    fclose(f);

    vars[handle].used = true;
    vars[handle].data = buf;
    vars[handle].size = size;
    return handle;
}

/* The real ti_Close releases the handle but leaves the variable's data in place;
 * video.c keeps using pointers afterwards, so the buffer must outlive it. */
void ti_Close(uint8_t handle) { (void)handle; }
void *ti_GetDataPtr(uint8_t handle) { return vars[handle].data; }
uint16_t ti_GetSize(uint8_t handle) { return (uint16_t)vars[handle].size; }
int ti_IsArchived(uint8_t handle) { (void)handle; return 1; }
int ti_SetArchiveStatus(bool a, uint8_t h) { (void)a; (void)h; return 1; }

int main(int argc, char **argv)
{
    if (argc != 3) {
        fprintf(stderr, "usage: %s DATADIR OUTFILE\n", argv[0]);
        return 1;
    }
    host_SetDataDir(argv[1]);

    video_info_t info;
    /* No block buffer: zx0 expansion exists only as calculator code, so the host
     * can only run uncompressed data (the encoder writes a copy for this). */
    video_status_t status = video_Open(&info, NULL);
    if (status != VIDEO_OK) {
        fprintf(stderr, "video_Open: %s\n", video_StatusText(status));
        if (status == VIDEO_BLOCK_TOO_BIG) {
            fprintf(stderr, "this data is zx0-compressed; point hosttest at the "
                            "uncompressed copy the encoder writes\n");
        }
        return 1;
    }
    printf("header: %u frames, %u fps, %u chunks, %u byte blocks, %s-first\n",
           info.frame_count, info.fps, info.chunk_count, info.block_size,
           info.msb_first ? "msb" : "lsb");

    FILE *out = fopen(argv[2], "wb");
    if (!out) {
        perror(argv[2]);
        return 1;
    }

    static uint8_t frame[FRAME_BYTES];
    unsigned int decoded = 0;
    for (;;) {
        status = video_NextFrame(frame);
        if (status != VIDEO_OK) {
            break;
        }
        fwrite(frame, 1, sizeof frame, out);
        decoded++;
    }
    fclose(out);

    if (status != VIDEO_END) {
        fprintf(stderr, "decode failed after %u frames: %s\n", decoded,
                video_StatusText(status));
        return 1;
    }
    if (decoded != info.frame_count) {
        fprintf(stderr, "decoded %u frames but the header says %u\n", decoded,
                info.frame_count);
        return 1;
    }
    printf("decoded %u frames into %s\n", decoded, argv[2]);
    return 0;
}

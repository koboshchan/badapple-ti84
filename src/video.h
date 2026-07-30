/**
 * Streaming decoder for the Bad Apple CE video format.
 *
 * Video lives in archived appvars (BADAPPLH plus BADAPP00, BADAPP01, ...)
 * written by util/encode.py. Archived variables are contiguous in flash and
 * directly addressable on the eZ80, so playback reads the opcode stream through
 * a plain pointer: no per-frame file I/O, no decompression, no copying beyond
 * the pixels that actually changed.
 *
 * The format is documented in util/badapple.py and the README; util/verify.py
 * contains a reference decoder that must stay in agreement with this one.
 */

#ifndef BADAPPLE_VIDEO_H
#define BADAPPLE_VIDEO_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    VIDEO_OK,
    VIDEO_NO_HEADER,      /* BADAPPLH is missing */
    VIDEO_BAD_HEADER,     /* wrong magic, version or geometry */
    VIDEO_MISSING_CHUNK,  /* a chunk named in the header is not present */
    VIDEO_TOO_MANY_CHUNKS,
    VIDEO_ARCHIVE_FAILED, /* a chunk is in RAM and could not be archived */
    VIDEO_BLOCK_TOO_BIG,  /* the file's block size exceeds our buffer */
    VIDEO_BAD_STREAM,     /* unrecognised opcode or truncated chunk */
    VIDEO_END,            /* end of video reached */
} video_status_t;

/**
 * Largest block the player can expand.
 *
 * util/encode.py cuts blocks at 16 KB; this must be at least that.
 */
#define VIDEO_MAX_BLOCK_SIZE 16384

typedef struct {
    unsigned int frame_count;
    unsigned int block_size;
    uint8_t fps;
    uint8_t chunk_count;
    bool msb_first;
    bool invert;
    bool compressed;
} video_info_t;

/** Human-readable text for a status, for reporting failures to the user. */
const char *video_StatusText(video_status_t status);

/**
 * Opens the video and caches a flash pointer for every chunk.
 *
 * Must be called before lcd_Begin: archiving a variable can prompt the user for
 * a garbage collect, which needs the LCD in its normal colour mode.
 *
 * @param block_buffer Scratch space of at least VIDEO_MAX_BLOCK_SIZE bytes, used
 *                     to expand compressed blocks. On the calculator this is
 *                     spare VRAM (see lcd_Scratch), so it costs the program no
 *                     RAM at all.
 */
video_status_t video_Open(video_info_t *info, uint8_t *block_buffer);

/** Points the decoder at the first frame, loading the first block. */
video_status_t video_Rewind(void);

/**
 * Decodes the next frame into buf.
 *
 * @returns VIDEO_OK if a frame was decoded, VIDEO_END at the end of the video,
 *          or an error status if the stream is malformed.
 */
video_status_t video_NextFrame(uint8_t *buf);

#endif /* BADAPPLE_VIDEO_H */

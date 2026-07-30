#include "video.h"

#include <compression.h>
#include <fileioc.h>
#include <string.h>

#include "lcd.h"

/* Frame opcodes, matching util/badapple.py. */
#define OP_PFRAME 1
#define OP_IFRAME 2
#define OP_DFRAME 3
#define OP_NEXTCHUNK 4
#define OP_END 5

/* Both masks are rounded up to whole bytes, matching util/badapple.py. The 20
 * columns of a row leave 4 unused bits in the last column-mask byte. */
#define ROW_MASK_BYTES ((FRAME_HEIGHT + 7) / 8)     /* 15 */
#define COL_MASK_BYTES ((FRAME_ROW_BYTES + 7) / 8)  /* 3 */

#define MAGIC "BAPL"
#define VERSION 2
#define HEADER_NAME "BADAPPLH"
#define HEADER_FIXED_BYTES 17
#define NAME_BYTES 8

#define FLAG_MSB_FIRST (1 << 0)
#define FLAG_INVERT (1 << 1)
#define FLAG_ZX0 (1 << 2)

/* Within a chunk each block is preceded by its stored size as a u16; a size of
 * zero ends that chunk's blocks. */
#define BLOCK_HEADER_BYTES 2

/* Sized to cover every value the header's one-byte chunk count can hold, so the
 * count never needs a range check. util/badapple.py caps encodes at 255 chunks,
 * which is roughly 12 MB -- far more than any archive holds. */
#define MAX_CHUNKS 256

static struct {
    const uint8_t *base;
    const uint8_t *end;
} chunks[MAX_CHUNKS];

static uint8_t chunk_count;
static uint8_t chunk_index;
static const uint8_t *block_next;  /* next block header within the chunk */
static bool compressed;
static uint8_t *block_buffer;      /* where compressed blocks are expanded */

/* Cursor within the block currently being decoded. */
static const uint8_t *pos;
static const uint8_t *end;

const char *video_StatusText(video_status_t status)
{
    switch (status) {
        case VIDEO_OK:
            return "ok";
        case VIDEO_UNCHANGED:
            return "frame unchanged";
        case VIDEO_NO_HEADER:
            return "BADAPPLH not found - send the appvars";
        case VIDEO_BAD_HEADER:
            return "BADAPPLH is not a valid v2 160x120 header";
        case VIDEO_BLOCK_TOO_BIG:
            return "video uses blocks too large for this player";
        case VIDEO_MISSING_CHUNK:
            return "a BADAPnnn chunk is missing";
        case VIDEO_ARCHIVE_FAILED:
            return "could not archive the video appvars";
        case VIDEO_BAD_STREAM:
            return "video data is corrupt";
        case VIDEO_END:
            return "end of video";
    }
    return "unknown error";
}

/** Moves a variable into the archive so its data has a stable flash address. */
static bool ensure_archived(const char *name)
{
    uint8_t handle = ti_Open(name, "r");
    if (!handle) {
        return false;
    }
    bool ok = true;
    if (!ti_IsArchived(handle)) {
        ok = ti_SetArchiveStatus(true, handle) != 0;
    }
    ti_Close(handle);
    return ok;
}

/** Caches the data pointer and length of an already-archived variable. */
static bool cache_chunk(const char *name, uint8_t slot)
{
    uint8_t handle = ti_Open(name, "r");
    if (!handle) {
        return false;
    }
    const uint8_t *data = ti_GetDataPtr(handle);
    uint16_t size = ti_GetSize(handle);
    ti_Close(handle);
    if (!data || !size) {
        return false;
    }
    chunks[slot].base = data;
    chunks[slot].end = data + size;
    return true;
}

video_status_t video_Open(video_info_t *info, uint8_t *buffer)
{
    block_buffer = buffer;

    uint8_t handle = ti_Open(HEADER_NAME, "r");
    if (!handle) {
        return VIDEO_NO_HEADER;
    }
    const uint8_t *header = ti_GetDataPtr(handle);
    uint16_t header_size = ti_GetSize(handle);
    if (!header || header_size < HEADER_FIXED_BYTES) {
        ti_Close(handle);
        return VIDEO_BAD_HEADER;
    }

    unsigned int width, height, count, block_size;
    uint8_t version, fps, flags, names;
    /* Static rather than automatic: a kilobyte is more than belongs on the
     * stack, and this is only needed while opening the video. */
    static uint8_t name_table[MAX_CHUNKS * NAME_BYTES];

    bool magic_ok = memcmp(header, MAGIC, 4) == 0;
    version = header[4];
    width = header[5] | ((unsigned int)header[6] << 8);
    height = header[7] | ((unsigned int)header[8] << 8);
    count = header[9] | ((unsigned int)header[10] << 8) |
            ((unsigned int)header[11] << 16);
    fps = header[12];
    names = header[13];
    flags = header[14];
    block_size = header[15] | ((unsigned int)header[16] << 8);

    if (!magic_ok || version != VERSION ||
        width != FRAME_WIDTH || height != FRAME_HEIGHT || fps == 0 ||
        block_size == 0) {
        ti_Close(handle);
        return VIDEO_BAD_HEADER;
    }
    if (block_size > VIDEO_MAX_BLOCK_SIZE) {
        ti_Close(handle);
        return VIDEO_BLOCK_TOO_BIG;
    }
    /* No upper bound needed: names is a byte and MAX_CHUNKS covers every value
     * one can hold. */
    if (names == 0) {
        ti_Close(handle);
        return VIDEO_BAD_HEADER;
    }
    if (header_size < HEADER_FIXED_BYTES + (unsigned int)names * NAME_BYTES) {
        ti_Close(handle);
        return VIDEO_BAD_HEADER;
    }
    /* Copy the names out before doing anything that can move variables around
     * in memory, which would invalidate this pointer into the header. */
    memcpy(name_table, header + HEADER_FIXED_BYTES,
           (unsigned int)names * NAME_BYTES);
    ti_Close(handle);

    char name[NAME_BYTES + 1];
    name[NAME_BYTES] = '\0';

    /* Archive everything first: archiving one variable relocates the others, so
     * no data pointer may be held while it happens. */
    for (uint8_t i = 0; i < names; i++) {
        memcpy(name, name_table + (unsigned int)i * NAME_BYTES, NAME_BYTES);
        if (!ensure_archived(name)) {
            return VIDEO_ARCHIVE_FAILED;
        }
    }
    for (uint8_t i = 0; i < names; i++) {
        memcpy(name, name_table + (unsigned int)i * NAME_BYTES, NAME_BYTES);
        if (!cache_chunk(name, i)) {
            return VIDEO_MISSING_CHUNK;
        }
    }

    chunk_count = names;
    compressed = (flags & FLAG_ZX0) != 0;
    if (compressed && !block_buffer) {
        return VIDEO_BLOCK_TOO_BIG;
    }

    if (info) {
        info->frame_count = count;
        info->fps = fps;
        info->chunk_count = names;
        info->block_size = block_size;
        info->msb_first = (flags & FLAG_MSB_FIRST) != 0;
        info->invert = (flags & FLAG_INVERT) != 0;
        info->compressed = compressed;
    }
    return video_Rewind();
}

/**
 * Loads the next block, moving to the next chunk when the current one runs out.
 *
 * Compressed blocks are expanded into the block buffer; uncompressed ones are
 * decoded in place, straight out of flash.
 */
static video_status_t load_block(void)
{
    for (;;) {
        const uint8_t *chunk_end = chunks[chunk_index].end;
        if (block_next + BLOCK_HEADER_BYTES > chunk_end) {
            return VIDEO_BAD_STREAM;
        }
        unsigned int size = block_next[0] | ((unsigned int)block_next[1] << 8);
        if (size == 0) {
            /* End of this chunk's blocks. */
            if (++chunk_index >= chunk_count) {
                return VIDEO_BAD_STREAM;
            }
            block_next = chunks[chunk_index].base;
            continue;
        }
        const uint8_t *data = block_next + BLOCK_HEADER_BYTES;
        if (data + size > chunk_end) {
            return VIDEO_BAD_STREAM;
        }
        block_next = data + size;

        if (compressed) {
            zx0_Decompress(block_buffer, data);
            pos = block_buffer;
            /* The expanded length is implicit in the zx0 stream; the frame
             * opcodes terminate the block, and the buffer bounds the damage if
             * the data is corrupt. */
            end = block_buffer + VIDEO_MAX_BLOCK_SIZE;
        } else {
            pos = data;
            end = data + size;
        }
        return VIDEO_OK;
    }
}

video_status_t video_Rewind(void)
{
    chunk_index = 0;
    block_next = chunks[0].base;
    return load_block();
}

/** Expands an RLE'd whole frame. */
static void decode_iframe(uint8_t *buf)
{
    const uint8_t *src = pos;
    uint8_t escape = *src++;
    uint8_t *dst = buf;
    uint8_t *frame_end = buf + FRAME_BYTES;

    while (dst < frame_end) {
        uint8_t b = *src++;
        if (b != escape) {
            *dst++ = b;
        } else {
            unsigned int run = *src++;
            if (run == 0) {
                run = 256;  /* a count of 256 wraps to 0 in one byte */
            }
            uint8_t value = *src++;
            if (run > (unsigned int)(frame_end - dst)) {
                run = (unsigned int)(frame_end - dst);
            }
            memset(dst, value, run);
            dst += run;
        }
    }
    pos = src;
}

/** Applies a delta frame: a row mask, then a column mask per changed row. */
static void decode_pframe(uint8_t *buf)
{
    const uint8_t *src = pos;
    const uint8_t *row_mask = src;
    src += ROW_MASK_BYTES;
    uint8_t *row = buf;

    for (uint8_t i = 0; i < ROW_MASK_BYTES; i++) {
        uint8_t rows = row_mask[i];
        if (rows == 0) {
            row += 8 * FRAME_ROW_BYTES;
            continue;
        }
        for (uint8_t bit = 0; bit < 8; bit++, row += FRAME_ROW_BYTES) {
            if (!(rows & 1)) {
                rows >>= 1;
                continue;
            }
            rows >>= 1;

            const uint8_t *col_mask = src;
            src += COL_MASK_BYTES;
            uint8_t *dst = row;
            /* The column mask is rounded up to whole bytes, so the last one can
             * cover fewer than 8 columns; stop at the end of the row. */
            unsigned int remaining = FRAME_ROW_BYTES;
            for (uint8_t c = 0; c < COL_MASK_BYTES; c++) {
                uint8_t cols = col_mask[c];
                unsigned int width = remaining < 8 ? remaining : 8;
                remaining -= width;
                if (cols == 0) {
                    dst += width;
                    continue;
                }
                for (unsigned int k = 0; k < width; k++, dst++) {
                    if (cols & 1) {
                        *dst = *src++;
                    }
                    cols >>= 1;
                }
            }
        }
    }
    pos = src;
}

video_status_t video_NextFrame(uint8_t *buf)
{
    for (;;) {
        if (pos >= end) {
            return VIDEO_BAD_STREAM;
        }
        uint8_t op = *pos++;
        switch (op) {
            case OP_DFRAME:
                /* Nothing was touched, so the caller can skip redrawing. */
                return VIDEO_UNCHANGED;
            case OP_IFRAME:
                decode_iframe(buf);
                break;
            case OP_PFRAME:
                decode_pframe(buf);
                break;
            case OP_NEXTCHUNK: {
                video_status_t next = load_block();
                if (next != VIDEO_OK) {
                    return next;
                }
                continue;
            }
            case OP_END:
                return VIDEO_END;
            default:
                return VIDEO_BAD_STREAM;
        }
        /* Decoding runs without per-byte bounds checks for speed; catch a
         * corrupt or truncated chunk here instead. */
        return pos <= end ? VIDEO_OK : VIDEO_BAD_STREAM;
    }
}

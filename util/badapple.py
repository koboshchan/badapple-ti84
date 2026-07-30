"""Shared definitions for the Bad Apple CE video format.

Used by encode.py (writer) and verify.py (reader). The C player in src/video.c
implements the same format; keep the three in sync.

Frame geometry is fixed at the CE's native LCD size in 1bpp mode:
320x240 pixels = 40 bytes per row = 9600 bytes per frame.
"""

WIDTH = 320
HEIGHT = 240
ROW_BYTES = WIDTH // 8       # 40
FRAME_BYTES = ROW_BYTES * HEIGHT  # 9600
ROW_MASK_BYTES = HEIGHT // 8      # 30
COL_MASK_BYTES = ROW_BYTES // 8   # 5

# Frame opcodes. Numbering is inherited from the original Z80 encoder.
OP_PFRAME = 1     # row/column delta against the previous frame
OP_IFRAME = 2     # whole frame, byte RLE with an escape byte
OP_DFRAME = 3     # identical to the previous frame, no payload
OP_NEXTCHUNK = 4  # end of this appvar, continue at offset 0 of the next
OP_END = 5        # end of video

MAGIC = b"BAPL"
VERSION = 2

HEADER_NAME = "BADAPPLH"
# Appvar names are limited to eight characters, so this leaves room for three
# digits: BADAP000 through BADAP999.
CHUNK_NAME_FMT = "BADAP%03d"
NAME_BYTES = 8

# Must match MAX_CHUNKS in src/video.c. Blocks are packed whole, so a 60000-byte
# chunk holds three 16 KB blocks and runs about 20% short of full; 256 chunks is
# therefore roughly 12 MB, enough to cover uncompressed encodes of long videos as
# well as any compressed one.
MAX_CHUNKS = 256

# Appvar payloads must stay under the OS's 16-bit variable size field -- the
# player reads a chunk's length back through ti_GetSize, which returns a u16, so
# anything larger would be silently truncated. Held well clear of 65535 to leave
# room for the variable header convbin prepends.
CHUNK_MAX = 60000
CHUNK_HARD_MAX = 0xFFFF

# The frame stream is cut into blocks, which are the unit of compression: the
# player decompresses one block at a time into a buffer and decodes frames out of
# it. Frames never straddle a block, and blocks never straddle an appvar.
BLOCK_SIZE = 16384

# Header layout (little-endian, matching the eZ80):
#   0  magic[4]      "BAPL"
#   4  version       u8
#   5  width         u16
#   7  height        u16
#   9  frame_count   u24
#  12  fps           u8
#  13  chunk_count   u8
#  14  flags         u8
#  15  block_size    u16
#  17  names         u8[8] * chunk_count
HEADER_FIXED_BYTES = 17

FLAG_MSB_FIRST = 1 << 0  # pixel bit order within a byte
FLAG_INVERT = 1 << 1     # 1 bits are black rather than white
FLAG_ZX0 = 1 << 2        # blocks are zx0-compressed

# Within an appvar, each stored block is preceded by its size as a u16; a size of
# zero marks the end of that appvar's blocks.
BLOCK_HEADER_BYTES = 2


def chunk_name(index):
    return CHUNK_NAME_FMT % index


def build_header(frame_count, fps, chunk_count, flags, block_size=BLOCK_SIZE):
    if chunk_count > MAX_CHUNKS:
        raise ValueError(
            "%d chunks exceeds the player's limit of %d; encode at a lower "
            "framerate or with a smaller --budget" % (chunk_count, MAX_CHUNKS))
    h = bytearray(MAGIC)
    h.append(VERSION)
    h += WIDTH.to_bytes(2, "little")
    h += HEIGHT.to_bytes(2, "little")
    h += frame_count.to_bytes(3, "little")
    h.append(fps)
    h.append(chunk_count)
    h.append(flags)
    h += block_size.to_bytes(2, "little")
    for i in range(chunk_count):
        name = chunk_name(i).encode("ascii")
        assert len(name) <= NAME_BYTES
        h += name.ljust(NAME_BYTES, b"\0")
    return bytes(h)


def parse_header(data):
    if data[:4] != MAGIC:
        raise ValueError("bad magic %r" % data[:4])
    if data[4] != VERSION:
        raise ValueError("unsupported version %d" % data[4])
    width = int.from_bytes(data[5:7], "little")
    height = int.from_bytes(data[7:9], "little")
    if (width, height) != (WIDTH, HEIGHT):
        raise ValueError("unexpected geometry %dx%d" % (width, height))
    chunk_count = data[13]
    return {
        "width": width,
        "height": height,
        "frame_count": int.from_bytes(data[9:12], "little"),
        "fps": data[12],
        "chunk_count": chunk_count,
        "flags": data[14],
        "block_size": int.from_bytes(data[15:17], "little"),
        "names": [
            data[HEADER_FIXED_BYTES + i * NAME_BYTES:
                 HEADER_FIXED_BYTES + i * NAME_BYTES + NAME_BYTES]
            .rstrip(b"\0").decode("ascii")
            for i in range(chunk_count)
        ],
    }

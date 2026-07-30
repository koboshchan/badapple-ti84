# Bad Apple for the TI-84 Plus CE

Full-screen 320x240 monochrome video playback on a TI-84 Plus CE, written in C for the
[CE C toolchain](https://github.com/CE-Programming/toolchain). The player streams video
straight out of the flash archive; the encoder is Python and takes any video file ffmpeg
can read.

This is a rewrite for the CE. The original TI-83+/84+ SE version — a 96-page signed flash
application in Z80 assembly, with 96x64 video and four channels of tracker audio bit-banged
out of the link port — is in this repository's git history, before the CE rewrite commit.
Video of the original running: https://www.youtube.com/watch?v=6pAeWf3NPNU

## How it works

The CE's LCD controller can scan out **1 bit per pixel** through a two-entry palette. In
that mode a 320x240 frame is 9600 bytes and every decoded byte is eight finished pixels, so
frame data is written directly into VRAM with no pixel expansion, no blitting, and no
double buffer. Frames are stored as one of three types, whichever is smallest:

- **I-frame** — the whole frame, byte run-length encoded with an escape byte.
- **P-frame** — a delta: a bitmask of changed rows, then per changed row a bitmask of
  changed byte-columns followed by just those bytes.
- **D-frame** — identical to the previous frame; no payload at all.

That stream is cut into 16 KB blocks, each zx0-compressed, and the blocks are packed into
archived appvars. Archived variables are contiguous in flash and directly addressable on the
eZ80, so the player expands one block at a time straight out of flash and then decodes frames
from it with no further copying. The expansion buffer lives in the part of VRAM the display
never reads — a 1bpp frame is 9600 of the 153600 bytes of video RAM — so it costs no RAM at
all.

### The archive budget

A CE has roughly 3 MB of user archive, and 320x240 at 30fps encodes to something like
6-8 MB. It does not fit, and lowering the framerate barely helps: halving it cuts only about
a quarter of the data, because consecutive frames then differ more and the encoder falls back
to I-frames. Compression is what makes full resolution possible — zx0 takes this video's
stream to about 36% of its encoded size.

So the encoder is **budget-driven**: it measures how well the video compresses, searches
downwards through candidate framerates for the highest one that should fit the budget
(2.6 MB by default), compresses it for real, and drops to the next framerate if the actual
result misses. Expect somewhere in the 12-18fps range for a full-length video, depending on
how much motion it has.

For the reference encode — the full 5:24 Bad Apple video — that lands on **15fps in 2.40 MB
across 43 appvars**.

There is no audio. The CE has no link port — only USB — so the original's bit-banged
tracker has no output path on stock hardware.

## Building

You need:

- The [CE C toolchain](https://github.com/CE-Programming/toolchain) (`CEdev`). The makefile
  looks in `~/CEdev`; override with `make CEDEV=/path/to/CEdev`.
- Python 3 with `numpy`.
- `ffmpeg` and `ffprobe` on your `PATH`.

Encode the video first, then build the program:

```bash
make data VIDEO=badapple.mp4
```

```bash
make
```

`make data` writes chunk files into `data/`, then wraps each as an archived appvar in
`bin/`. It skips re-encoding if `data/` is already populated — delete `data/` (or run
`make distclean`) to force a re-encode. `make` produces `bin/BADAPPLE.8xp`.

Encoding the full video takes a few minutes, nearly all of it in zx0 compression (about
two seconds per 16 KB block, spread across your cores). For a quick check that the whole
pipeline works, encode just the first few frames:

```bash
make data VIDEO=badapple.mp4 ENCFLAGS="--max-frames 15"
```

Encoder options are forwarded with `ENCFLAGS`:

```bash
make data VIDEO=badapple.mp4 ENCFLAGS="--fps 15 --budget 2000000 --threshold 100"
```

Run `python3 util/encode.py --help` for the full list, including `--invert`,
`--stretch` (instead of letterboxing a non-4:3 source), `--no-compress`, `--hwaccel`,
and `--bit-order`.

## Transferring and running

Send **`bin/BADAPPLE.8xp` and every `bin/BADAP*.8xv` plus `bin/BADAPPLH.8xv`** to the
calculator with TI Connect CE or TiLP. There will be a few dozen appvars; they must all be
present. They are marked archived already, so they go straight to flash without eating RAM.

Run it with `Asm(prgmBADAPPLE)` (on OS 5.5+ you will need a shell such as
[Cesium](https://github.com/mateoconlechuga/cesium) or arTIfiCE, since TI removed native
assembly program support). Press `clear` to stop early. On exit the player reports how many
frames it played and whether any ran late.

## Testing

`make hosttest` compiles the calculator's own decoder (`src/video.c`) for the host, runs it
over the encoded blocks, and checks the frames come out byte-identical to those from the
independent reference decoder in `util/verify.py`. That validates the shipping decode path
without a calculator.

`make verify VIDEO=badapple.mp4` goes further: it decodes the encoded stream and compares
every frame bit-for-bit against the thresholded source video, confirming the encode is
lossless with respect to what the player will show.

zx0 expansion only exists as calculator code, so neither check can read the compressed
payload directly. The encoder therefore writes an uncompressed copy of the same blocks and
appvars to `data/hostcheck/`, and both targets use it automatically. Everything is covered
except the single `zx0_Decompress` call itself, which is a matched pair with the `convbin`
that produced the data. When verifying a partial encode, pass the same limit:
`make verify VIDEO=badapple.mp4 VERIFYFLAGS="--max-frames 15"`.

## Layout

| Path | What it is |
|---|---|
| `src/main.c` | Startup, frame pacing off timer 1, key handling, teardown |
| `src/lcd.c` | Puts the LCD into 1bpp mode and restores it afterwards |
| `src/video.c` | Appvar and block streaming, zx0 expansion, I/P/D frame decoding |
| `util/encode.py` | Video file to appvar chunks, with the framerate budget search |
| `util/verify.py` | Reference decoder and round-trip verification |
| `util/badapple.py` | Format constants shared by the encoder and verifier |
| `test/hosttest.c` | Runs `src/video.c` on the host against the reference decoder |

## Data format

All little-endian, matching the eZ80. `util/badapple.py` is the authoritative definition;
`src/video.c` and `util/verify.py` both implement it.

Header appvar `BADAPPLH`:

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | magic `"BAPL"` |
| 4 | 1 | version (2) |
| 5 | 2 | width (320) |
| 7 | 2 | height (240) |
| 9 | 3 | frame count |
| 12 | 1 | fps |
| 13 | 1 | chunk count |
| 14 | 1 | flags: bit 0 = MSB-first pixels, bit 1 = inverted, bit 2 = zx0-compressed blocks |
| 15 | 2 | block size (16384) |
| 17 | 8 x count | chunk names (`"BADAP000"`, ...) |

Data appvars `BADAP000`, `BADAP001`, ... hold a sequence of blocks. Each block is stored as
its size in bytes as a u16, followed by that many bytes; a size of zero ends the appvar's
blocks and playback continues in the next one. A block never straddles an appvar, so the
player can expand one directly from flash. Chunks stay under 60000 bytes: their length is
read back through a 16-bit field, so a larger one would be truncated.

Expanded (or read in place, when the zx0 flag is clear), a block is a flat stream of frames,
each starting with an opcode byte:

| Opcode | Meaning | Payload |
|---|---|---|
| 1 | P-frame | 30-byte row mask; per set row a 5-byte column mask then the changed bytes |
| 2 | I-frame | escape byte, then RLE data: a literal byte, or `escape, count, value` (count 0 means 256) |
| 3 | D-frame | none |
| 4 | end of block | none; continue with the next block |
| 5 | end of video | none |

Frames never straddle a block. Masks are LSB-first: bit 0 of the first row-mask byte is
screen row 0. Pixel bit order within a frame byte is set by the header's flags and must
match how the player configures the LCD.

## Credits

Bad Apple!! is by Alstroemeria Records; the shadow-art video is by Anira. The original
TI-83+/84+ SE demo, and the RLE/delta encoding approach this rewrite inherits, are in the
git history of this repository.

Licensed under the terms in [LICENSE](LICENSE).

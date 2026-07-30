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

Video lives in archived appvars. Archived variables are contiguous in flash and directly
addressable on the eZ80, so playback reads the stream through a plain pointer: no per-frame
file I/O, no decompression, and no copying beyond the pixels that actually changed.

### The archive budget

A CE has roughly 3 MB of user archive, and 320x240 at 30fps encodes to something like
6-8 MB. It does not fit. So the encoder is **budget-driven**: it searches downwards through
candidate framerates, encodes each one to measure it, and picks the highest framerate whose
total fits the budget (2.6 MB by default), reporting what it chose. Expect somewhere in the
12-20fps range for a full-length video, depending on how much motion it has.

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

Encoder options are forwarded with `ENCFLAGS`:

```bash
make data VIDEO=badapple.mp4 ENCFLAGS="--fps 15 --budget 2000000 --threshold 100"
```

Run `python3 util/encode.py --help` for the full list, including `--invert`,
`--stretch` (instead of letterboxing a non-4:3 source), and `--bit-order`.

## Transferring and running

Send **`bin/BADAPPLE.8xp` and every `bin/BADAPP*.8xv` plus `bin/BADAPPLH.8xv`** to the
calculator with TI Connect CE or TiLP. There will be a few dozen appvars; they must all be
present. They are marked archived already, so they go straight to flash without eating RAM.

Run it with `Asm(prgmBADAPPLE)` (on OS 5.5+ you will need a shell such as
[Cesium](https://github.com/mateoconlechuga/cesium) or arTIfiCE, since TI removed native
assembly program support). Press `clear` to stop early. On exit the player reports how many
frames it played and whether any ran late.

## Testing

`make hosttest` compiles the calculator's own decoder (`src/video.c`) for the host, runs it
over the encoded chunks, and checks the frames come out byte-identical to those from the
independent reference decoder in `util/verify.py`. That validates the shipping decode path
without a calculator.

`make verify VIDEO=badapple.mp4` goes further: it decodes the encoded stream and compares
every frame bit-for-bit against the thresholded source video, confirming the encode is
lossless with respect to what the player will show.

## Layout

| Path | What it is |
|---|---|
| `src/main.c` | Startup, frame pacing off timer 1, key handling, teardown |
| `src/lcd.c` | Puts the LCD into 1bpp mode and restores it afterwards |
| `src/video.c` | Appvar streaming and I/P/D frame decoding |
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
| 4 | 1 | version (1) |
| 5 | 2 | width (320) |
| 7 | 2 | height (240) |
| 9 | 3 | frame count |
| 12 | 1 | fps |
| 13 | 1 | chunk count |
| 14 | 1 | flags: bit 0 = MSB-first pixels, bit 1 = inverted |
| 15 | 8 x count | chunk names (`"BADAPP00"`, ...) |

Data appvars `BADAPP00`, `BADAPP01`, ... are a flat stream of frames, each starting with an
opcode byte:

| Opcode | Meaning | Payload |
|---|---|---|
| 1 | P-frame | 30-byte row mask; per set row a 5-byte column mask then the changed bytes |
| 2 | I-frame | escape byte, then RLE data: a literal byte, or `escape, count, value` (count 0 means 256) |
| 3 | D-frame | none |
| 4 | next chunk | none; continue at offset 0 of the next appvar |
| 5 | end of video | none |

Frames never straddle a chunk boundary. Masks are LSB-first: bit 0 of the first row-mask
byte is screen row 0. Pixel bit order within a frame byte is set by the header's flags and
must match how the player configures the LCD.

## Credits

Bad Apple!! is by Alstroemeria Records; the shadow-art video is by Anira. The original
TI-83+/84+ SE demo, and the RLE/delta encoding approach this rewrite inherits, are in the
git history of this repository.

Licensed under the terms in [LICENSE](LICENSE).

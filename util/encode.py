#!/usr/bin/env python3
"""Encode a video file into Bad Apple CE playback chunks.

    util/encode.py INPUT_VIDEO OUTPUT_DIR [options]

Frames are decoded by ffmpeg at the CE's native 320x240, thresholded to 1bpp,
and encoded as one of three frame types (see util/badapple.py):

  I-frame  whole frame, byte RLE with an escape byte
  P-frame  row mask + per-row column mask + changed bytes
  D-frame  identical to the previous frame

Each frame is encoded whichever way is smallest. The stream is then cut into
16 KB blocks, each zx0-compressed, and the blocks are packed into appvar-sized
chunks. Frames never straddle a block, and blocks never straddle a chunk.

The TI-84 Plus CE has only about 3 MB of user archive, and 320x240 at 30fps
does not come close to fitting. By default the encoder measures how well the
video compresses, searches downwards through candidate framerates for the
highest one that should fit --budget, compresses it, and drops to the next
framerate if the real result misses. It reports what it chose.
"""

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import time

try:
    import numpy as np
except ImportError:
    sys.exit("error: numpy is required (pip install numpy)")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import badapple as ba

FPS_CANDIDATES = [30, 25, 24, 20, 18, 15, 12, 10, 8]
GRAY_FRAME_BYTES = ba.WIDTH * ba.HEIGHT

# Subdirectory holding an uncompressed twin of the payload, for host-side checks.
HOSTCHECK_DIR = "hostcheck"

# Matches the chunk files this script writes, so stale ones can be cleared out
# between attempts without touching anything else in the directory.
CHUNK_FILE_RE = re.compile(
    re.escape(ba.CHUNK_NAME_FMT.split("%")[0].lower()) + r"\d+\.bin")


# --------------------------------------------------------------------------
# ffmpeg ingest
# --------------------------------------------------------------------------

def probe(path):
    """Returns (duration_seconds, source_fps); either may be None."""
    exe = shutil.which("ffprobe")
    if exe is None:
        sys.exit("error: ffprobe not found on PATH")
    out = subprocess.run(
        [exe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "format=duration", "-show_entries",
         "stream=r_frame_rate", "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    info = json.loads(out)

    duration = None
    try:
        duration = float(info["format"]["duration"])
    except (KeyError, ValueError, TypeError):
        pass

    fps = None
    try:
        num, den = info["streams"][0]["r_frame_rate"].split("/")
        if float(den) != 0:
            fps = float(num) / float(den)
    except (KeyError, IndexError, ValueError, TypeError):
        pass
    return duration, fps


def gray_frames(path, fps, stretch, hwaccel="auto"):
    """Yield successive 320x240 grayscale frames as uint8 arrays."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        sys.exit("error: ffmpeg not found on PATH")
    if stretch:
        scale = "scale=%d:%d" % (ba.WIDTH, ba.HEIGHT)
    else:
        scale = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
                 "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black"
                 % (ba.WIDTH, ba.HEIGHT, ba.WIDTH, ba.HEIGHT))
    cmd = [exe, "-v", "error"]
    if hwaccel and hwaccel != "none":
        # Hardware decoding, where the platform offers it. ffmpeg falls back to
        # software on its own if the requested backend is unavailable.
        cmd += ["-hwaccel", hwaccel]
    cmd += ["-i", path,
            "-vf", "fps=%d,%s,format=gray" % (fps, scale),
            "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            bufsize=GRAY_FRAME_BYTES * 4)
    drained = False
    try:
        while True:
            buf = proc.stdout.read(GRAY_FRAME_BYTES)
            if len(buf) < GRAY_FRAME_BYTES:
                drained = True
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(
                ba.HEIGHT, ba.WIDTH)
    finally:
        # A caller that stops early (budget exceeded) leaves ffmpeg writing into
        # a closed pipe; kill it rather than reporting the resulting SIGPIPE as
        # a decode failure.
        if not drained:
            proc.kill()
            proc.stdout.close()
            proc.stderr.close()
            proc.wait()
        else:
            proc.stdout.close()
            err = proc.stderr.read().decode("utf-8", "replace").strip()
            proc.stderr.close()
            if proc.wait() != 0:
                sys.exit("error: ffmpeg failed while decoding %s%s"
                         % (path, "\n" + err if err else ""))


def packed_frames(path, fps, threshold, invert, msb_first, stretch,
                  hwaccel="auto"):
    """Yield successive frames as packed 1bpp uint8 arrays, shape (240, 40)."""
    bitorder = "big" if msb_first else "little"
    for gray in gray_frames(path, fps, stretch, hwaccel):
        bits = gray > threshold
        if invert:
            bits = ~bits
        yield np.packbits(bits, axis=1, bitorder=bitorder)


def candidate_framerates(source_fps, forced_fps=None):
    """The framerates to consider, highest first.

    Shared with util/verify.py: the first entry is the rate the video is decoded
    at, and every lower rate is sampled from that decode, so a verifier has to
    make the same choice to reproduce the encoder's frames exactly.
    """
    if forced_fps:
        return [forced_fps]
    cands = list(FPS_CANDIDATES)
    if source_fps:
        # Asking for more frames per second than the source has just inserts
        # duplicates, which would label the result with a rate the video does
        # not actually have.
        capped = [f for f in cands if f <= source_fps + 0.5]
        if capped:
            cands = capped
    return cands


class FrameCache:
    """Decodes the video once, then serves any framerate from memory.

    The framerate search would otherwise re-run ffmpeg and re-threshold every
    frame for each candidate. Packed 1bpp frames are 9600 bytes each, so even a
    long video fits in RAM (a five-minute one at 24fps is about 75 MB) and lower
    framerates are just a matter of picking frames out of the cache.
    """

    def __init__(self, path, base_fps, threshold, invert, msb_first, stretch,
                 hwaccel="auto", max_frames=None):
        self.base_fps = base_fps
        frames = []
        for frame in packed_frames(path, base_fps, threshold, invert,
                                   msb_first, stretch, hwaccel):
            frames.append(frame)
            if max_frames and len(frames) >= max_frames:
                break
        self.cache = (np.stack(frames) if frames
                      else np.zeros((0, ba.HEIGHT, ba.ROW_BYTES), np.uint8))

    def __len__(self):
        return len(self.cache)

    @property
    def nbytes(self):
        return self.cache.nbytes

    def at(self, fps):
        """Yields the frames for a given framerate, sampled from the cache."""
        if fps >= self.base_fps or not len(self.cache):
            return iter(self.cache)
        count = int(len(self.cache) * fps / self.base_fps)
        # Pick the frame in effect at each output timestamp, as ffmpeg's fps
        # filter does.
        idx = (np.arange(count) * (self.base_fps / fps)).astype(int)
        np.clip(idx, 0, len(self.cache) - 1, out=idx)
        return (self.cache[i] for i in idx)


# --------------------------------------------------------------------------
# Frame encoding
# --------------------------------------------------------------------------

def rle_pieces(flat):
    """Split a flat frame into (value, length) runs, each at most 256 long.

    Returns (values, lengths, escape_byte) as numpy arrays / int.
    """
    edges = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    starts = np.concatenate(([0], edges))
    lengths = np.diff(np.concatenate((starts, [flat.size])))
    values = flat[starts]

    # Long runs are chopped into pieces of at most 256 bytes, since the count
    # field is a single byte (0 meaning 256).
    counts = (lengths + 255) // 256
    idx = np.repeat(np.arange(lengths.size), counts)
    group_start = np.repeat(np.cumsum(counts) - counts, counts)
    pos = np.arange(idx.size) - group_start
    last = pos == (counts[idx] - 1)
    plen = np.where(last, lengths[idx] - 256 * (counts[idx] - 1), 256)

    escape = int(np.bincount(flat, minlength=256).argmin())
    return values[idx], plen, escape


def iframe_size(flat):
    """Size of the RLE payload for a frame, without materialising it."""
    values, plen, escape = rle_pieces(flat)
    # A run costs 3 bytes as escape/count/value, except that runs of one or two
    # non-escape bytes are cheaper written out literally.
    cost = np.where((plen <= 2) & (values != escape), plen, 3)
    return 1 + int(cost.sum())  # +1 for the leading escape byte


def iframe_encode(flat):
    values, plen, escape = rle_pieces(flat)
    out = bytearray()
    out.append(escape)
    esc = escape
    for v, n in zip(values.tolist(), plen.tolist()):
        if n <= 2 and v != esc:
            out.append(v)
            if n == 2:
                out.append(v)
        else:
            out.append(esc)
            out.append(n & 0xFF)  # 256 wraps to 0
            out.append(v)
    return bytes(out)


def pframe_size(diff, changed_rows):
    return ba.ROW_MASK_BYTES + ba.COL_MASK_BYTES * changed_rows + int(diff.sum())


def pframe_encode(cur, diff, row_changed):
    out = bytearray(np.packbits(row_changed, bitorder="little").tobytes())
    for r in np.flatnonzero(row_changed).tolist():
        rowdiff = diff[r]
        out += np.packbits(rowdiff, bitorder="little").tobytes()
        out += cur[r][rowdiff].tobytes()
    return bytes(out)


class Stats:
    def __init__(self):
        self.i = self.p = self.d = 0
        self.total = 0

    def report(self, fps, budget=None):
        frames = self.i + self.p + self.d
        line = ("fps %-3d frames %-5d  I %-5d P %-5d D %-5d  %8d bytes (%.2f MB)"
                % (fps, frames, self.i, self.p, self.d,
                   self.total, self.total / 1e6))
        if budget is not None:
            line += "  %s" % ("fits" if self.total <= budget else "OVER BUDGET")
        return line


def encode_stream(frames, stats, materialise, budget=None, progress=True):
    """Encode a frame iterator, accumulating counts into stats.

    Yields (opcode, payload) for each frame; payload is b"" unless materialise
    is set. Returns early once budget is exceeded.
    """
    prev = None
    last_report = 0.0

    for n, cur in enumerate(frames):
        flat = cur.reshape(-1)
        if prev is None:
            op = ba.OP_IFRAME
            size = 1 + iframe_size(flat)
            payload = iframe_encode(flat) if materialise else b""
        else:
            diff = cur != prev
            row_changed = diff.any(axis=1)
            changed_rows = int(row_changed.sum())
            if changed_rows == 0:
                op, size, payload = ba.OP_DFRAME, 1, b""
            else:
                psize = 1 + pframe_size(diff, changed_rows)
                isize = 1 + iframe_size(flat)
                # Force a periodic I-frame every two frames at the start so the
                # stream opens on a known-good full frame.
                if isize <= psize or n < 2:
                    op, size = ba.OP_IFRAME, isize
                    payload = iframe_encode(flat) if materialise else b""
                else:
                    op, size = ba.OP_PFRAME, psize
                    payload = (pframe_encode(cur, diff, row_changed)
                               if materialise else b"")

        if op == ba.OP_IFRAME:
            stats.i += 1
        elif op == ba.OP_PFRAME:
            stats.p += 1
        else:
            stats.d += 1
        stats.total += size
        if materialise:
            assert len(payload) + 1 == size, (op, len(payload), size)

        yield op, payload
        prev = cur

        if budget is not None and stats.total > budget:
            if progress:
                sys.stderr.write("\r%-72s\n" % "  exceeded budget, abandoning")
            return
        if progress:
            now = time.monotonic()
            if now - last_report > 0.25:
                last_report = now
                sys.stderr.write("\r  frame %-6d %7.2f MB"
                                 % (n + 1, stats.total / 1e6))
                sys.stderr.flush()

    if progress:
        sys.stderr.write("\r%-72s\r" % "")


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------

def build_blocks(stream):
    """Cut the encoded frame stream into blocks.

    A block is the unit of compression and decoding: the player expands one into
    a buffer and reads frames out of it, so frames must not straddle blocks. Each
    block ends with OP_NEXTCHUNK, or OP_END for the last one.
    """
    blocks = []
    cur = bytearray()
    frame_count = 0

    for op, payload in stream:
        frame = bytes([op]) + payload
        # Keep one byte spare for the terminating opcode.
        if len(cur) + len(frame) + 1 > ba.BLOCK_SIZE:
            cur.append(ba.OP_NEXTCHUNK)
            blocks.append(bytes(cur))
            cur = bytearray()
        cur += frame
        frame_count += 1
    cur.append(ba.OP_END)
    blocks.append(bytes(cur))
    return blocks, frame_count


def estimate_ratio(blocks, workdir, samples=24):
    """Estimates how much zx0 will shrink this video, from a sample of blocks.

    The budget describes the size on the calculator, so the framerate search has
    to compare against compressed sizes. Compressing every block of every
    candidate framerate would be far too slow, and the ratio is stable enough
    across a video that a spread-out sample predicts it well. The final size is
    checked exactly once the real encode is done.
    """
    if not blocks:
        return 1.0
    step = max(1, len(blocks) // samples)
    sample = blocks[::step][:samples]
    packed = compress_blocks(sample, workdir, label="sampling compression")
    raw_total = sum(len(b) for b in sample)
    return sum(len(b) for b in packed) / raw_total


def compress_blocks(blocks, workdir, label="compressing block"):
    """zx0-compress each block using convbin from the CE toolchain.

    zx0 is used because the calculator can expand it with zx0_Decompress from
    the toolchain's compression.h; convbin and that routine are a matched pair.

    convbin's optimal parser takes a couple of seconds per block, and blocks are
    independent, so they are compressed in parallel.
    """
    convbin = shutil.which("convbin")
    if convbin is None:
        sys.exit("error: convbin not found on PATH; add CEdev/bin to PATH or "
                 "encode with --no-compress")
    os.makedirs(workdir, exist_ok=True)

    def compress_one(item):
        i, block = item
        src = os.path.join(workdir, "block%05d.bin" % i)
        dst = os.path.join(workdir, "block%05d.zx0" % i)
        try:
            with open(src, "wb") as f:
                f.write(block)
            subprocess.run([convbin, "-l", "1", "-j", "bin", "-k", "bin",
                            "-c", "zx0", "-i", src, "-o", dst],
                           check=True, capture_output=True)
            with open(dst, "rb") as f:
                return i, f.read()
        finally:
            for path in (src, dst):
                if os.path.isfile(path):
                    os.remove(path)

    out = [None] * len(blocks)
    done = 0
    workers = min(len(blocks), (os.cpu_count() or 2))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for i, packed in pool.map(compress_one, enumerate(blocks)):
            out[i] = packed
            done += 1
            sys.stderr.write("\r  %s %d/%d" % (label, done, len(blocks)))
            sys.stderr.flush()
    sys.stderr.write("\r%-40s\r" % "")
    return out


def pack_chunks(stored, outdir, chunk_max=ba.CHUNK_MAX):
    """Pack stored blocks into appvar-sized chunk files.

    Each block is written as a u16 size followed by its data; a zero size marks
    the end of an appvar's blocks. A block never straddles an appvar, so the
    player can expand one straight out of flash.
    """
    os.makedirs(outdir, exist_ok=True)
    chunks = []
    cur = bytearray()

    def flush():
        cur.extend(b"\0\0")  # end-of-appvar marker
        path = os.path.join(outdir, ba.chunk_name(len(chunks)).lower() + ".bin")
        with open(path, "wb") as f:
            f.write(cur)
        chunks.append(path)

    for block in stored:
        need = ba.BLOCK_HEADER_BYTES + len(block)
        if len(block) >= 0x10000:
            sys.exit("error: a stored block is %d bytes, too big for its "
                     "16-bit size field" % len(block))
        # Leave room for the end-of-appvar marker.
        if len(cur) + need + ba.BLOCK_HEADER_BYTES > chunk_max:
            flush()
            cur = bytearray()
        cur += len(block).to_bytes(2, "little")
        cur += block
    flush()
    return chunks


def write_payload(blocks, outdir, frame_count, fps, flags,
                  chunk_max=ba.CHUNK_MAX):
    """Writes the chunk files and header for one packing of the blocks."""
    # Clear any previous packing first: a retry at a lower framerate produces
    # fewer chunks, and leftovers would look like part of the video.
    if os.path.isdir(outdir):
        current = {os.path.basename(ba.chunk_name(i).lower() + ".bin")
                   for i in range(len(blocks) + 1)}
        for stale in os.listdir(outdir):
            if CHUNK_FILE_RE.fullmatch(stale) and stale not in current:
                os.remove(os.path.join(outdir, stale))
    chunks = pack_chunks(blocks, outdir, chunk_max)
    header = ba.build_header(frame_count, fps, len(chunks), flags)
    header_path = os.path.join(outdir, ba.HEADER_NAME.lower() + ".bin")
    with open(header_path, "wb") as f:
        f.write(header)

    total = len(header)
    for path in chunks:
        size = os.path.getsize(path)
        total += size
        if size > chunk_max:
            sys.exit("error: %s is %d bytes, over the %d byte chunk limit"
                     % (path, size, chunk_max))
        if size > ba.CHUNK_HARD_MAX:
            # The player reads chunk lengths through a 16-bit field.
            sys.exit("error: %s is %d bytes, which cannot be expressed in the "
                     "16-bit size field" % (path, size))
    return chunks, total


def main():
    ap = argparse.ArgumentParser(
        description="Encode a video into Bad Apple CE playback chunks.")
    ap.add_argument("input", help="source video file (anything ffmpeg reads)")
    ap.add_argument("outdir", help="directory to write chunk .bin files into")
    ap.add_argument("--budget", type=int, default=2_600_000,
                    help="maximum total encoded bytes (default: 2600000)")
    ap.add_argument("--fps", type=int,
                    help="force a framerate instead of searching for one")
    ap.add_argument("--threshold", type=int, default=127,
                    help="grayscale threshold, 0-255 (default: 127)")
    ap.add_argument("--invert", action="store_true",
                    help="swap black and white")
    ap.add_argument("--bit-order", choices=("lsb", "msb"), default="lsb",
                    help="pixel bit order within a byte; must match the "
                         "player's LCD configuration (default: lsb)")
    ap.add_argument("--stretch", action="store_true",
                    help="stretch to 320x240 instead of letterboxing")
    ap.add_argument("--max-frames", type=int, metavar="N",
                    help="stop after N frames; for quick pipeline test runs")
    ap.add_argument("--hwaccel", default="auto",
                    help="ffmpeg -hwaccel backend for decoding, or 'none' "
                         "(default: auto)")
    ap.add_argument("--no-compress", action="store_true",
                    help="store blocks uncompressed; roughly three times larger, "
                         "but needs no zx0 expansion on the calculator")
    args = ap.parse_args()

    # Progress goes to stderr and results to stdout; keep them in step when
    # either is redirected.
    sys.stdout.reconfigure(line_buffering=True)

    if not os.path.isfile(args.input):
        sys.exit("error: no such file: %s" % args.input)

    msb_first = args.bit_order == "msb"
    duration, source_fps = probe(args.input)
    print("Source: %s%s%s"
          % (args.input,
             "" if duration is None else " (%.1fs" % duration,
             "" if duration is None else
             (", %g fps)" % source_fps if source_fps else ")")))
    print("Target: %dx%d 1bpp, %s-first pixels, budget %.2f MB"
          % (ba.WIDTH, ba.HEIGHT, args.bit_order, args.budget / 1e6))

    candidates = candidate_framerates(source_fps, args.fps)
    if not args.fps and source_fps and candidates != FPS_CANDIDATES:
        print("Capping the search at the source's %g fps." % source_fps)

    # Decode once at the highest framerate under consideration; every lower
    # candidate is then sampled from the cache rather than decoded again.
    started = time.monotonic()
    print("\nDecoding at %d fps:" % candidates[0])
    cache = FrameCache(args.input, candidates[0], args.threshold, args.invert,
                       msb_first, args.stretch, args.hwaccel, args.max_frames)
    if args.max_frames:
        print("(--max-frames %d: this is a partial test encode)"
              % args.max_frames)
    if not len(cache):
        sys.exit("error: no frames decoded from %s" % args.input)
    print("Cached %d frames (%.1f MB) in %.1fs."
          % (len(cache), cache.nbytes / 1e6, time.monotonic() - started))

    def frames(fps):
        return cache.at(fps)

    tmpdir = os.path.join(args.outdir, "tmp")
    blocks = None
    ratio = 1.0

    if not args.no_compress:
        # The budget is an on-calculator size, so the search has to work in
        # compressed bytes. Measure this video's compression ratio once, from a
        # real encode at the top candidate framerate.
        print("\nMeasuring compression at %d fps:" % candidates[0])
        stats = Stats()
        blocks, probe_frames = build_blocks(
            encode_stream(frames(candidates[0]), stats, materialise=True))
        ratio = estimate_ratio(blocks, tmpdir)
        print("%s\nzx0 compresses this video to about %.0f%% of its encoded "
              "size." % (stats.report(candidates[0]), 100 * ratio))
        probe_fps = candidates[0]
        probe_frame_count = probe_frames
    else:
        probe_fps = None

    # No safety margin is applied here on purpose. The estimate is only used to
    # choose which framerate to try; the real compressed size is checked after
    # compressing, and a miss drops to the next framerate. Padding the estimate
    # would throw away a framerate that actually fits.
    raw_budget = int(args.budget / ratio)
    if not args.no_compress:
        print("So the budget of %.2f MB allows roughly %.2f MB of encoded data."
              % (args.budget / 1e6, raw_budget / 1e6))

    if args.fps:
        fps = args.fps
        if blocks is None:
            print("\nSizing pass at %d fps:" % fps)
            stats = Stats()
            for _ in encode_stream(frames(fps), stats, materialise=False):
                pass
            print(stats.report(fps, raw_budget))
            total_raw = stats.total
        else:
            total_raw = sum(len(b) for b in blocks)
        if total_raw > raw_budget:
            print("warning: %d fps looks like about %.2f MB, over the %.2f MB "
                  "budget; encoding anyway because --fps was given"
                  % (fps, total_raw * ratio / 1e6, args.budget / 1e6))
    else:
        print("\nSearching for the highest framerate that fits:")
        fps = None
        for candidate in candidates:
            if candidate == probe_fps:
                # Already encoded while measuring compression.
                total_raw = sum(len(b) for b in blocks)
                stats = Stats()
                stats.total = total_raw
                print("fps %-3d %8d bytes encoded, about %.2f MB packed  %s"
                      % (candidate, total_raw, total_raw * ratio / 1e6,
                         "fits" if total_raw <= raw_budget else "OVER BUDGET"))
            else:
                stats = Stats()
                for _ in encode_stream(frames(candidate), stats,
                                       materialise=False, budget=raw_budget):
                    pass
                total_raw = stats.total
                print("fps %-3d %8d bytes encoded, about %.2f MB packed  %s"
                      % (candidate, total_raw, total_raw * ratio / 1e6,
                         "fits" if total_raw <= raw_budget else "OVER BUDGET"))
            if total_raw <= raw_budget:
                fps = candidate
                break
            if candidate != probe_fps:
                blocks = None
        if fps is None:
            sys.exit("error: even %d fps does not fit in %.2f MB; raise "
                     "--budget or shorten the video" % (candidates[-1],
                                                        args.budget / 1e6))
        print("Chose %d fps." % fps)

    base_flags = 0
    if msb_first:
        base_flags |= ba.FLAG_MSB_FIRST
    if args.invert:
        base_flags |= ba.FLAG_INVERT

    # The estimate can still come out slightly optimistic. Compress for real,
    # and if the result misses the budget, step down a framerate and try again so
    # what lands in data/ is always something that fits.
    attempt = candidates.index(fps)
    while True:
        fps = candidates[attempt]
        if blocks is not None and fps == probe_fps:
            frame_count = probe_frame_count
            print("\nReusing the %d fps encode." % fps)
        else:
            print("\nEncoding at %d fps:" % fps)
            stats = Stats()
            blocks, frame_count = build_blocks(
                encode_stream(frames(fps), stats, materialise=True))
            print(stats.report(fps))
        print("Cut into %d blocks of at most %d bytes." % (len(blocks),
                                                          ba.BLOCK_SIZE))

        if args.no_compress:
            stored = blocks
            flags = base_flags
        else:
            print("Compressing %d blocks with zx0:" % len(blocks))
            stored = compress_blocks(blocks, tmpdir)
            flags = base_flags | ba.FLAG_ZX0
            raw = sum(len(b) for b in blocks)
            packed = sum(len(b) for b in stored)
            print("zx0: %.2f MB -> %.2f MB (%.0f%% of raw)"
                  % (raw / 1e6, packed / 1e6, 100.0 * packed / raw))

        chunks, total = write_payload(stored, args.outdir, frame_count, fps,
                                     flags)
        print("Total on-calculator size: %.2f MB in %d variables"
              % (total / 1e6, len(chunks) + 1))

        if total <= args.budget or args.fps or attempt + 1 >= len(candidates):
            break
        print("That misses the %.2f MB budget; dropping to %d fps and "
              "re-encoding." % (args.budget / 1e6, candidates[attempt + 1]))
        attempt += 1
        blocks = None

    if os.path.isdir(tmpdir):
        shutil.rmtree(tmpdir, ignore_errors=True)
    print("Wrote %s and %d chunks to %s/"
          % (ba.HEADER_NAME.lower() + ".bin", len(chunks), args.outdir))
    if total > args.budget:
        print("warning: that is still over the %.2f MB budget; it may not fit "
              "your calculator's archive" % (args.budget / 1e6))
    if frame_count:
        print("Playback: %d frames at %d fps = %.1fs"
              % (frame_count, fps, frame_count / fps))

    # The compressed payload cannot be decoded on the host, since zx0 expansion
    # only exists as calculator code. Write an uncompressed twin of exactly the
    # same block and appvar structure so util/verify.py and test/hosttest.c can
    # still check every other part of the format and the player's decoder.
    if not args.no_compress:
        host_dir = os.path.join(args.outdir, HOSTCHECK_DIR)
        # Same chunk size as the real payload: chunks are read back through a
        # 16-bit size field, so oversized ones would be silently truncated.
        write_payload(blocks, host_dir, frame_count, fps, base_flags)
        print("Wrote an uncompressed copy to %s/ for host verification."
              % host_dir)


if __name__ == "__main__":
    main()

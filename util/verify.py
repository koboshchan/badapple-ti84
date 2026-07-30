#!/usr/bin/env python3
"""Verify encoded Bad Apple CE chunks by decoding them back to frames.

    util/verify.py DATA_DIR [--source INPUT_VIDEO [encode options]]

Without --source, this decodes the chunk stream and checks it is structurally
sound: every frame decodes to exactly 9600 bytes, chunk boundaries line up, and
the stream ends with OP_END on the last chunk.

With --source, it additionally re-decodes the original video with the same
settings and asserts every frame matches bit-for-bit. This is the real test of
the format: it exercises the same decode logic the C player in src/video.c
implements, without needing a calculator.
"""

import argparse
import os
import sys

try:
    import numpy as np
except ImportError:
    sys.exit("error: numpy is required (pip install numpy)")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import badapple as ba
import encode


class StreamError(Exception):
    pass


class Decoder:
    """Mirror of the C player's decode loop in src/video.c.

    Walks the same two-level structure: appvar chunks hold size-prefixed blocks,
    and each block holds a run of frames ending in OP_NEXTCHUNK or OP_END.
    """

    def __init__(self, chunks):
        self.chunks = chunks
        self.chunk = 0
        self.chunk_pos = 0   # offset of the next block header in this chunk
        self.block = b""
        self.pos = 0
        self.buf = np.zeros(ba.FRAME_BYTES, dtype=np.uint8)
        self.block_count = 0
        self._load_block()

    def _load_block(self):
        """Loads the next block, advancing to the next chunk if needed."""
        while True:
            data = self.chunks[self.chunk]
            if self.chunk_pos + ba.BLOCK_HEADER_BYTES > len(data):
                raise StreamError("chunk %d ends without a block terminator"
                                  % self.chunk)
            size = int.from_bytes(
                data[self.chunk_pos:self.chunk_pos + ba.BLOCK_HEADER_BYTES],
                "little")
            if size == 0:
                # End of this chunk's blocks; continue in the next one.
                self.chunk += 1
                self.chunk_pos = 0
                if self.chunk >= len(self.chunks):
                    raise StreamError("ran out of chunks looking for a block")
                continue
            start = self.chunk_pos + ba.BLOCK_HEADER_BYTES
            if start + size > len(data):
                raise StreamError("block at chunk %d offset %d is truncated"
                                  % (self.chunk, self.chunk_pos))
            self.block = data[start:start + size]
            self.chunk_pos = start + size
            self.pos = 0
            self.block_count += 1
            if len(self.block) > ba.BLOCK_SIZE:
                raise StreamError("block %d is %d bytes, over the %d limit"
                                  % (self.block_count, len(self.block),
                                     ba.BLOCK_SIZE))
            return

    def _u8(self):
        if self.pos >= len(self.block):
            raise StreamError("ran off the end of block %d" % self.block_count)
        b = self.block[self.pos]
        self.pos += 1
        return b

    def _take(self, n):
        if self.pos + n > len(self.block):
            raise StreamError("block %d truncated" % self.block_count)
        out = self.block[self.pos:self.pos + n]
        self.pos += n
        return out

    def frames(self):
        """Yield each decoded frame as a (240, 40) uint8 array."""
        while True:
            op = self._u8()
            if op == ba.OP_NEXTCHUNK:
                if self.pos != len(self.block):
                    raise StreamError(
                        "block %d has %d trailing bytes after OP_NEXTCHUNK"
                        % (self.block_count, len(self.block) - self.pos))
                self._load_block()
                continue
            if op == ba.OP_END:
                return
            if op == ba.OP_DFRAME:
                pass
            elif op == ba.OP_IFRAME:
                self._iframe()
            elif op == ba.OP_PFRAME:
                self._pframe()
            else:
                raise StreamError("bad opcode %d at chunk %d offset %d"
                                  % (op, self.chunk, self.pos - 1))
            yield self.buf.reshape(ba.HEIGHT, ba.ROW_BYTES)

    def _iframe(self):
        escape = self._u8()
        buf = self.buf
        out = 0
        while out < ba.FRAME_BYTES:
            b = self._u8()
            if b != escape:
                buf[out] = b
                out += 1
            else:
                count = self._u8() or 256
                value = self._u8()
                if out + count > ba.FRAME_BYTES:
                    raise StreamError("RLE run overruns the frame")
                buf[out:out + count] = value
                out += count

    def _pframe(self):
        row_mask = np.unpackbits(
            np.frombuffer(self._take(ba.ROW_MASK_BYTES), dtype=np.uint8),
            bitorder="little").astype(bool)
        buf = self.buf.reshape(ba.HEIGHT, ba.ROW_BYTES)
        for row in np.flatnonzero(row_mask).tolist():
            col_mask = np.unpackbits(
                np.frombuffer(self._take(ba.COL_MASK_BYTES), dtype=np.uint8),
                bitorder="little").astype(bool)
            n = int(col_mask.sum())
            buf[row][col_mask] = np.frombuffer(self._take(n), dtype=np.uint8)


def load(datadir):
    header_path = os.path.join(datadir, ba.HEADER_NAME.lower() + ".bin")
    if not os.path.isfile(header_path):
        sys.exit("error: %s not found; run util/encode.py first" % header_path)
    with open(header_path, "rb") as f:
        header = ba.parse_header(f.read())
    chunks = []
    for i in range(header["chunk_count"]):
        path = os.path.join(datadir, ba.chunk_name(i).lower() + ".bin")
        if not os.path.isfile(path):
            sys.exit("error: header lists %d chunks but %s is missing"
                     % (header["chunk_count"], path))
        with open(path, "rb") as f:
            chunks.append(f.read())
    return header, chunks


def main():
    ap = argparse.ArgumentParser(
        description="Verify encoded Bad Apple CE chunks.")
    ap.add_argument("datadir", help="directory written by util/encode.py")
    ap.add_argument("--source",
                    help="original video, to compare decoded frames against")
    ap.add_argument("--threshold", type=int, default=127)
    ap.add_argument("--stretch", action="store_true")
    ap.add_argument("--max-frames", type=int, metavar="N",
                    help="match an encode that used --max-frames N")
    ap.add_argument("--dump", metavar="FILE",
                    help="write every decoded frame as raw 1bpp bytes, for "
                         "comparison against the C decoder (see test/)")
    args = ap.parse_args()

    header, chunks = load(args.datadir)
    print("Header: %d frames, %d fps, %d chunks, %d byte blocks, flags 0x%02X"
          % (header["frame_count"], header["fps"], header["chunk_count"],
             header["block_size"], header["flags"]))
    for i, data in enumerate(chunks):
        if len(data) > ba.CHUNK_MAX:
            sys.exit("error: chunk %d is %d bytes, over the %d byte limit"
                     % (i, len(data), ba.CHUNK_MAX))
    if header["flags"] & ba.FLAG_ZX0:
        sys.exit("error: %s holds zx0-compressed blocks, which can only be "
                 "expanded on the calculator. Verify the uncompressed twin the "
                 "encoder writes alongside it:\n  %s"
                 % (args.datadir, os.path.join(args.datadir, "hostcheck")))

    reference = None
    if args.source:
        # Reproduce the encoder's frames exactly: it decodes once at the highest
        # candidate framerate and samples lower rates from that, so decoding the
        # source directly at the playback rate would select slightly different
        # frames and report spurious mismatches.
        _, source_fps = encode.probe(args.source)
        base_fps = encode.candidate_framerates(source_fps)[0]
        cache = encode.FrameCache(
            args.source, base_fps, args.threshold,
            bool(header["flags"] & ba.FLAG_INVERT),
            bool(header["flags"] & ba.FLAG_MSB_FIRST), args.stretch,
            max_frames=args.max_frames)
        reference = cache.at(header["fps"])

    dump = open(args.dump, "wb") if args.dump else None
    decoded = 0
    try:
        for frame in Decoder(chunks).frames():
            if dump is not None:
                dump.write(frame.tobytes())
            if reference is not None:
                want = next(reference, None)
                if want is None:
                    sys.exit("error: stream has more frames than the source")
                if not np.array_equal(frame, want):
                    bad = int((frame != want).sum())
                    sys.exit("error: frame %d differs in %d of %d bytes"
                             % (decoded, bad, ba.FRAME_BYTES))
            decoded += 1
            if decoded % 100 == 0:
                sys.stderr.write("\r  verified %d frames" % decoded)
                sys.stderr.flush()
    except StreamError as e:
        sys.exit("\nerror: %s (after %d frames)" % (e, decoded))
    finally:
        if dump is not None:
            dump.close()
    sys.stderr.write("\r%-40s\r" % "")

    if decoded != header["frame_count"]:
        sys.exit("error: decoded %d frames but the header says %d"
                 % (decoded, header["frame_count"]))
    if reference is not None and next(reference, None) is not None:
        sys.exit("error: the source has more frames than the stream")

    print("OK: %d frames decoded%s"
          % (decoded, ", all matching the source" if reference else ""))


if __name__ == "__main__":
    main()

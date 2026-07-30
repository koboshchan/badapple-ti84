# ----------------------------
# Bad Apple for the TI-84 Plus CE
#
#   make data VIDEO=badapple.mp4   encode the video into appvars (do this first)
#   make                           build bin/BADAPPLE.8xp
#   make clean                     remove build output, keep encoded video
#   make distclean                 also remove the encoded video
# ----------------------------

NAME = BADAPPLE
DESCRIPTION = "Bad Apple for the TI-84 Plus CE"
COMPRESSED = YES
ARCHIVED = NO

CFLAGS = -Wall -Wextra -Oz
CXXFLAGS = -Wall -Wextra -Oz

# ----------------------------

CEDEV ?= $(HOME)/CEdev
export CEDEV
export PATH := $(CEDEV)/bin:$(PATH)

include $(shell $(CEDEV)/bin/cedev-config --makefile)

# ----------------------------
# Video data pipeline
#
# util/encode.py turns a video file into appvar-sized chunks, then convbin wraps
# each chunk as an archived 8xv. The video is not part of the program: the
# player streams it out of the flash archive at runtime.
# ----------------------------

DATADIR := data
ENCODE := python3 util/encode.py
VERIFY := python3 util/verify.py
CONVBIN := $(CEDEV)/bin/convbin

# Extra options forwarded to the encoder, e.g. make data VIDEO=x.mp4 ENCFLAGS="--fps 15"
ENCFLAGS ?=

HEADER_BIN := $(DATADIR)/badapplh.bin

.PHONY: data appvars verify hosttest distclean

data: $(HEADER_BIN)
	@$(MAKE) --no-print-directory appvars

# Once the video has been encoded, a plain `make` repackages the appvars too, so
# that `make clean && make` leaves bin/ complete and ready to send.
ifneq ($(wildcard $(HEADER_BIN)),)
build: appvars
endif

$(HEADER_BIN):
ifndef VIDEO
	$(error Set VIDEO to a source video, e.g. make data VIDEO=badapple.mp4)
endif
	$(ENCODE) "$(VIDEO)" $(DATADIR) $(ENCFLAGS)

# Wrap every encoded chunk as an archived appvar named after its file.
appvars: $(HEADER_BIN)
	@mkdir -p $(BINDIR)
	@rm -f $(BINDIR)/*.8xv
	@for f in $(DATADIR)/*.bin; do \
	    name=$$(basename $$f .bin | tr 'a-z' 'A-Z'); \
	    echo "convbin $$name.8xv"; \
	    $(CONVBIN) -l 1 -j bin -k 8xv -r -n $$name -i $$f -o $(BINDIR)/$$name.8xv || exit 1; \
	done

# The calculator payload is zx0-compressed, which only the calculator can expand,
# so host checks run against the uncompressed copy the encoder writes alongside
# it. That covers everything but the single zx0_Decompress call.
HOSTCHECK := $(DATADIR)/hostcheck
CHECKDIR = $(if $(wildcard $(HOSTCHECK)/badapplh.bin),$(HOSTCHECK),$(DATADIR))

# Round-trip the encoded data through the reference decoder. Pass the source
# video to also compare every frame against it bit-for-bit.
verify:
	$(VERIFY) $(CHECKDIR) $(if $(VIDEO),--source "$(VIDEO)")

# Compile the calculator's decoder (src/video.c) for the host and check it
# produces exactly the same frames as the independent Python reference decoder.
# This is how the shipping decode path gets tested without a calculator.
HOSTCC ?= cc
HOSTDIR := $(DATADIR)/hosttest

hosttest: $(HEADER_BIN)
	@mkdir -p $(HOSTDIR)
	$(HOSTCC) -Wall -Wextra -O2 -I test -o $(HOSTDIR)/hosttest \
	    test/hosttest.c src/video.c
	$(HOSTDIR)/hosttest $(CHECKDIR) $(HOSTDIR)/c.raw
	$(VERIFY) $(CHECKDIR) --dump $(HOSTDIR)/py.raw
	cmp $(HOSTDIR)/c.raw $(HOSTDIR)/py.raw
	@echo "hosttest: the C decoder matches the reference decoder"

distclean: clean
	rm -rf $(DATADIR)

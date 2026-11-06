#!/bin/bash
# Build a diting boot image: kernel + freshly generated ADT + diting shim.
#
#   tools/diting-build-boot.sh <kernel> [out.img]
#
# Everything here goes into a boot.img that is only ever handed to
# `fastboot boot` (RAM). Nothing is flashed, nothing is written to a partition.
#
# GATES, in order. Each one is a failure we have already paid for on K60 or a
# class of mistake that is invisible until the screen stays dark:
#
#   1. shim/ must be the staged diting build (diting-apply-shim.sh --check).
#      Staging K60's 1440x3200 console draws everything off-panel: the device
#      looks dead while the kernel is actually running.
#   2. The ADT must advertise the 0xb4000000 window, not the old 0x80000000
#      one. The old artifact makes the kernel see 873 MB of RAM and every
#      daemon crashes at random (K60, 2026-10-25, two rounds lost).
#   3. The ADT must carry the diting framebuffer geometry. The kernel console
#      reads /sweet-fbcon, so a K60-sized ADT gives a skewed, wrapped console.
#   4. sweettouchoff=1 must be on the cmdline: bsd/dev/sweet_touch.c is written
#      for another SoC's SE7/GENI-I2C path and panics this one during probe.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
K60=${K60:-$ROOT/../ios-sweet-k60}
KERNEL=$(readlink -f "${1:?usage: diting-build-boot.sh <kernel> [out.img]}")
[ -f "$KERNEL" ] || { echo "error: kernel not found: $KERNEL" >&2; exit 1; }
OUT=$(readlink -m "${2:-$HOME/hackios/diting.img}")
ADT=/tmp/diting-adt-$$.bin
PAYLOAD=/tmp/diting-payload-$$.bin
trap 'rm -f "$ADT" "$PAYLOAD"' EXIT

# A live dump wins when there is one; otherwise fall back to the base DTB
# extracted from the unit's vendor_boot (board/base.dtb), which is the same tree
# before ABL fills /memory.
if [ -z "${DTB:-}" ]; then
	if [ -f "$ROOT/../dump/live.dtb" ]; then DTB=$ROOT/../dump/live.dtb; else DTB=$ROOT/board/base.dtb; fi
fi
[ -f "$DTB" ] || {
	echo "error: boot dtb not found at $DTB" >&2
	echo "       dump it first (docs/DEVICE-DUMP.md) or pass DTB=/path/live.dtb" >&2
	exit 1
}
# The shim build runs with cwd=shim/, so a relative DTB stops resolving there.
DTB=$(readlink -f "$DTB")

# MEMPLAN mirrors K60 (see ios-sweet-k60/MEMORY-PLAN.md). p0 is the layout that
# boots; p1/p2 are wider windows and depend on 0184's ram notches. The dram-size
# is always 1 MiB below the window: the top MiB is the embedded panic log.
case "${MEMPLAN:-p0}" in
p0) ADT_BASE=0xb4000000; ADT_WINDOW=0x2c000000 ;;   # 704 MiB
p1) ADT_BASE=0xb4000000; ADT_WINDOW=0x315f0000 ;;   # 790 MiB
p2) ADT_BASE=0xa3000000; ADT_WINDOW=0x425f0000 ;;   # 1063 MiB
*)  echo "unknown MEMPLAN '${MEMPLAN}' (p0|p1|p2)" >&2; exit 2 ;;
esac
echo "mem plan: ${MEMPLAN:-p0}  adt dram ${ADT_BASE}+$(printf '0x%x' $((ADT_WINDOW - 0x100000)))"

# ---- gate 1: shim -----------------------------------------------------------
# --check re-stages from the K60 tree and byte-compares, which is the strong
# form. CI has no K60 checkout, so there the committed shim is the only truth
# and the geometry grep below is all that can run.
if [ -d "$K60/shim" ]; then
	if ! SHIM_CHECK=$(bash tools/diting-apply-shim.sh --check 2>&1); then
		echo "$SHIM_CHECK" >&2
		echo "error: shim/ is not the staged diting build (run tools/diting-apply-shim.sh)" >&2
		exit 1
	fi
else
	echo "gate 1: no K60 tree at $K60, checking the committed shim only"
fi
grep -q "1220" shim/console.h && grep -q "2712" shim/console.h || {
	echo "error: shim/console.h does not carry diting geometry" >&2; exit 1; }

# ---- gates 2+3: ADT ---------------------------------------------------------
ADT_DUMP=$(python3 tools/mkadt-diting.py "$ADT" \
	--mem-base "$ADT_BASE" --mem-window "$ADT_WINDOW" --dump 2>&1)
echo "$ADT_DUMP" | grep -E "^(  dram|  framebuffer|  gic)" || true
echo "$ADT_DUMP" | grep -q "framebuffer 0xb8000000 1220x2712 stride 4880" || {
	echo "error: ADT does not advertise the diting framebuffer (1220x2712/4880)" >&2
	echo "$ADT_DUMP" | grep framebuffer >&2
	exit 1
}
python3 - "$ADT" "$ADT_BASE" "$ADT_WINDOW" <<'PY'
import struct, sys
d = open(sys.argv[1], 'rb').read()
base, window = int(sys.argv[2], 0), int(sys.argv[3], 0)
dram = window - 0x100000
for label, v in (('dram-base', base), ('dram-size', dram)):
    if struct.pack('>Q', v)[::-1] not in d:
        sys.exit('ADT check failed: missing %s %#x' % (label, v))
for label, v in (('fb width', 1220), ('fb height', 2712), ('fb stride', 4880)):
    if struct.pack('<I', v) not in d:
        sys.exit('ADT check failed: missing %s %d' % (label, v))
print('ADT ok: dram %#x + %#x (%d MiB), fbcon 1220x2712/4880'
      % (base, dram, dram >> 20))
PY

# ---- payload ----------------------------------------------------------------
# DITING_CMDLINE_EXTRA appends to the standard cmdline; it never replaces it.
EXTRA=${DITING_CMDLINE_EXTRA:-}
CMDLINE="debug=0x8 keepsyms=1 cpus=8 -noprogress serial=1 sweet_notests=1 rd=ufsroot0 ufsroot=userdata ufsrw=1 amfi_get_out_of_my_way=1 cs_enforcement_disable=1 amfi_allow_any_signature=1 ufslogsecs=5 sweettouchoff=1"
case "$CMDLINE" in
*sweettouchoff=1*) ;;
*) echo "error: gate 4: sweettouchoff=1 missing from the cmdline" >&2; exit 1 ;;
esac
[ -n "$EXTRA" ] && echo "cmdline extra: $EXTRA"
MKPAYLOAD=$ROOT/tools/mkpayload.py
[ -f "$MKPAYLOAD" ] || MKPAYLOAD=$K60/tools/mkpayload.py
python3 "$MKPAYLOAD" "$KERNEL" "$ADT" "$PAYLOAD" \
	--cmdline "$CMDLINE${EXTRA:+ $EXTRA}" >/dev/null

# ---- shim -> boot.img -------------------------------------------------------
# The full shim log goes to /tmp so a cross-compile failure on the CI box is
# readable: on K60 this line used to grep for "error" and continue, so a broken
# NDK build surfaced only as a missing out/shim-boot.img two lines later.
cd shim
if ! HDRVER=4 CFLAGS_EXTRA="-DK60_XNULOG -DK60_NO_PHYSMAP_EXT" PAYLOAD="$PAYLOAD" DTB="$DTB" \
	./build.sh > /tmp/shim-build.log 2>&1; then
	echo "error: shim build failed; last lines of /tmp/shim-build.log:" >&2
	tail -40 /tmp/shim-build.log >&2
	exit 1
fi
grep -E "error|appended payload" /tmp/shim-build.log || true
cp out/shim-boot.img "$OUT"
cd ..

ls -l "$OUT"
echo
echo "verify the image is RAM-only material, never flash it:"
echo "  fastboot boot $OUT"
echo "then, from a root shell/TWRP:"
echo "  tools/diting-log-pull.sh"
echo
# Only strings that really appear in the log count as markers ("panel buffer"
# lives in a comment only, so checking for it was a permanent MISS on K60).
echo "sanity: the kernel must contain every marker below"
for s in "ufs busy, retry next interval" "panic flush, total" "ABORT %s[%d] esr"; do
	if grep -qaF "$s" "$KERNEL"; then echo "  ok   $s"; else echo "  MISS $s"; fi
done
echo "info: present only when the kernel carries that subsystem"
for s in "SWEET FB: swap" "SWEET FB: AppleCLCD" "K60 TOUCH: abi"; do
	if grep -qaF "$s" "$KERNEL"; then echo "  have $s"; else echo "  none $s"; fi
done

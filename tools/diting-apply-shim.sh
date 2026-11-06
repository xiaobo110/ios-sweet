#!/bin/bash
# Stage the diting shim tree from the K60 sources.
#
#   tools/diting-apply-shim.sh          # stage into shim/
#   tools/diting-apply-shim.sh --check  # rebuild in a temp dir and diff shim/
#
# The shim is 95% board-independent (boot protocol, UFS, MMU, USB, font) and
# every line of it is already debugged on K60/mondrian. So instead of keeping a
# drifting copy, this stages the K60 sources and rewrites only the
# panel-dependent literals. Every rewrite carries an exact occurrence count and
# runs on bytes, so CRLF files stay CRLF and a K60 shim update that moves one of
# these lines aborts the run instead of producing a half-retargeted tree.
#
# Panel init sequences are regenerated from the diting panel dtsi, because that
# is the one thing that genuinely differs from K60.
#
# --check stages into a temp dir, applies the same transforms, and diffs the
# result against shim/. It never writes shim/, so it is safe to run anywhere.
#
# Never writes a partition: it only stages files and builds out/shim-boot.img
# for `fastboot boot`.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
K60=${K60:-$ROOT/../ios-sweet-k60}
SHIM_SRC=$K60/shim
DST=$ROOT/shim
DT=${DT:-$ROOT/../lineage-diting-dt}
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

echo "shim source: $SHIM_SRC"
[ -d "$SHIM_SRC" ] || { echo "error: K60 shim not found at $SHIM_SRC (set K60=)" >&2; exit 1; }
[ -d "$DT/qcom/display/display" ] || { echo "error: diting devicetree not found at $DT" >&2; exit 1; }

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

# ---- panel selection --------------------------------------------------------
# Both L12 panels are 1220x2712; the bootloader picks 42 02 0a or 36 02 0b by
# DDIC id. Init replay only matters for the DSI debug paths, so a wrong pick
# cannot cost a boot - but it does print in the log, which is how you notice.
PANEL=${DITING_PANEL:-$(python3 - "$ROOT/board/diting.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding='utf-8'))['display'].get('panel_selected', 'l12-42-02-0a'))
PY
)}
case "$PANEL" in
l12-42-02-0a)
	DTSI=$DT/qcom/display/display/dsi-panel-l12-42-02-0a-dsc-cmd.dtsi
	NODE=dsi_l12_42_02_0a_dsc_cmd ;;
l12-36-02-0b)
	DTSI=$DT/qcom/display/display/dsi-panel-l12-36-02-0b-dsc-cmd.dtsi
	NODE=dsi_l12_36_02_0b_dsc_cmd ;;
*)
	echo "error: unknown panel '$PANEL' (l12-42-02-0a|l12-36-02-0b)" >&2
	exit 1 ;;
esac
echo "panel: $PANEL  ($NODE)"

# ---- stage -----------------------------------------------------------------
mkdir -p "$STAGE"
for f in "$SHIM_SRC"/*; do
	b=$(basename "$f")
	case "$b" in
	out|panel_init.h|panel_init_nodsc.h) continue ;;
	esac
	[ -f "$f" ] && cp -p "$f" "$STAGE/$b"
done

python3 tools/diting-extract.py panel --dtsi "$DTSI" --out "$STAGE" --node "$NODE" \
	--basename panel_init \
	--symbol-full panel_init_seq --symbol-nodsc panel_init_nodsc

# ---- rewrite ---------------------------------------------------------------
# The rules live in Python so tabs are explicit \t escapes, not shell heredoc
# whitespace that looks identical but is not.
python3 - "$STAGE" <<'PY'
import os, sys

stage = sys.argv[1]

# file: [(old, new, expected_occurrences)]  -- bytes, CRLF-safe, cumulative
RULES = {
    'console.h': [
        (b'#define FB_BASE\t\t0xb8000000UL\t/* mondrian: cont_splash_region, 45MB */',
         b'#define FB_BASE\t\t0xb8000000UL\t/* diting: cont_splash_region, 43MB */', 1),
        (b'#define FB_W\t\t1440', b'#define FB_W\t\t1220', 1),
        (b'#define FB_H\t\t3200', b'#define FB_H\t\t2712', 1),
    ],
    'shim.c': [
        (b'con_str("FB", "0xb8000000 1440x3200 stride 5760");',
         b'con_str("FB", "0xb8000000 1220x2712 stride 4880");', 1),
        (b'con_kv("CONSOLE", "framebuffer 1440x3200 @ 0xb8000000", "OK", 1);',
         b'con_kv("CONSOLE", "framebuffer 1220x2712 @ 0xb8000000", "OK", 1);', 1),
        (b'const unsigned LINE = 1440u * 3u;\t/* 4320 B/row, RGB888 */',
         b'const unsigned LINE = 1220u * 3u;\t/* 3660 B/row, RGB888 */', 1),
        (b'const unsigned LINE = 1440u * 3u;', b'const unsigned LINE = 1220u * 3u;', 2),
        (b'const unsigned ROWS = 3200u;', b'const unsigned ROWS = 2712u;', 3),
    ],
    # No mkbootimg in this workspace (not on PATH, not on PyPI). Pack with
    # tools/diting-mkbootimg.py instead: its --selftest re-encodes a real v4
    # header byte for byte, which is what lets the boot image be built here.
    'build.sh': [
        (b'mkbootimg --header_version "$HDRVER" --kernel out/Image --ramdisk out/ramdisk.bin \\\r\n'
         b'\t--dtb "$DTB" --base 0 --kernel_offset 0x8000 --ramdisk_offset 0x1000000 \\\r\n'
         b'\t--tags_offset 0x100 --dtb_offset 0x1f00000 --pagesize 4096 \\\r\n'
         b'\t--os_version 17.0.0 --os_patch_level 2035-06 \\\r\n'
         b'\t--cmdline "${CMDLINE:-console=null}" -o out/shim-boot.img\r\n',
         b'python3 ../tools/diting-mkbootimg.py --header-version "$HDRVER" \\\r\n'
         b'\t--kernel out/Image --ramdisk out/ramdisk.bin --dtb "$DTB" \\\r\n'
         b'\t--os-version 17.0.0 --os-patch-level 2035-06 \\\r\n'
         b'\t--cmdline "${CMDLINE:-console=null}" --out out/shim-boot.img\r\n', 1),
        # Ubuntu's awk is mawk and has no strtonum, so the size is converted by
        # bash arithmetic instead - the CI builds the shim on ubuntu-latest.
        (b'size=$($NM out/shim.elf | awk \'$3=="_end"{print strtonum("0x"$1)}\')',
         b'size_hex=$($NM out/shim.elf | awk \'$3=="_end"{print $1}\')\n'
         b'size=$((0x$size_hex))', 1),
    ],
}

fail = 0
for name, rules in RULES.items():
    path = os.path.join(stage, name)
    data = open(path, 'rb').read()
    for old, new, want in rules:
        got = data.count(old)
        if got != want:
            sys.stderr.write('MISS %s: expected %d x %r, found %d\n'
                             % (name, want, old, got))
            fail = 1
            continue
        print('  ok   %s: %d x %s' % (name, got, old.decode('utf-8')[:56]))
        data = data.replace(old, new)
    open(path, 'wb').write(data)
sys.exit(1 if fail else 0)
PY

# ---- verify panel headers ---------------------------------------------------
for f in "$STAGE/panel_init.h" "$STAGE/panel_init_nodsc.h"; do
	grep -q "1220x2712" "$f" || { echo "MISS $f: not a diting 1220x2712 sequence" >&2; exit 1; }
done
grep -q "panel_init_seq\[" "$STAGE/panel_init.h" || { echo "MISS panel_init_seq symbol" >&2; exit 1; }
grep -q "panel_init_nodsc\[" "$STAGE/panel_init_nodsc.h" || { echo "MISS panel_init_nodsc symbol" >&2; exit 1; }

# ---- install or diff -------------------------------------------------------
if [ $CHECK = 1 ]; then
	rc=0
	for f in "$STAGE"/*; do
		b=$(basename "$f")
		if [ ! -f "$DST/$b" ]; then
			echo "DIFF $b: missing from shim/"; rc=1
		elif ! cmp -s "$f" "$DST/$b"; then
			echo "DIFF $b: shim/ differs from the staged build"; rc=1
		fi
	done
	for f in "$DST"/*; do
		b=$(basename "$f")
		[ -f "$STAGE/$b" ] || echo "stale $b: in shim/ but not produced any more"
	done
	if [ $rc = 0 ]; then
		echo "check ok: shim/ is exactly the staged diting build"
	else
		echo "run tools/diting-apply-shim.sh to refresh shim/" >&2
	fi
	exit $rc
fi

mkdir -p "$DST"
for f in "$STAGE"/*; do
	cp -p "$f" "$DST/$(basename "$f")"
done
echo
echo "shim/ staged from $SHIM_SRC with diting panel geometry"
ls -l "$DST/panel_init.h" "$DST/panel_init_nodsc.h" | sed 's/^/  /'
echo
echo "next: tools/diting-build-boot.sh <kernel>"

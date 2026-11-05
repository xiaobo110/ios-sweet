# XNU for diting

Same build as `ios-sweet-k60/xnu`, against a **retargeted copy** of its patch
series. Nothing in `patches/` is hand-edited: `tools/retarget-k60-patches.py`
applies one ordered table of literal rewrites to every patch and freezes each
rewrite's occurrence count in `tools/patch-retarget.lock.json`.

- Panel: 1220x2712, stride 4880 (K60: 1440x3200, stride 5760).
- Touch digitizer maximums: 12200x27120 (K60: 14400x32000).
- Everything else - GIC 0x17100000/0x17180000, vtimer intid 27, UFS
  0x1d84000, RSC 0x17a00000, GENI SE4 touch pins, PMIC keys, memory notches -
  is shared cape/SM8475 and is left byte-identical.

The table is applied globally because patches quote each other's lines as
plain context: a line added by 0209 is context in 0211, so both copies must be
rewritten identically or the later patch stops applying.

Refresh after any K60 series change:

```sh
python3 tools/retarget-k60-patches.py --dry-run   # counts must match the lock
python3 tools/retarget-k60-patches.py             # rewrite xnu/patches
python3 tools/retarget-k60-patches.py --freeze    # only if the K60 change was
                                                  # intentional; then review the diff
```

`--dry-run` failing with "upstream drift" means the K60 series moved a literal
this port depends on. Do not hand-patch the copy; look at the K60 change and
extend the table.

Build: `.github/workflows/xnu.yml` (macOS, blacktop/darwin-xnu-build pinned,
`MACHINE_CONFIG=SWEET`), artifact `kernel.development.sweet`. The machine config
stays SWEET because that is the configuration the K60 series was brought up on;
the K60/ diting board values come from the ADT and the patches, not from the
machine config.

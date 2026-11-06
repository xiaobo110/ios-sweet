#!/usr/bin/env python3
"""Fork the K60 XNU patch series into a diting (Redmi K50 Ultra) series.

The K60 series is already a cape/SM8475 bring-up; diting differs only in the
panel (1220x2712 vs 1440x3200, stride 4880 vs 5760) and in the touch
digitizer's reported maximums (12200x27120 vs 14400x32000). Everything else -
GIC, timer, UFS, RSC/GENI touch path, memory notches - is shared. The keys
PON page is NOT shared: diting's arbiter map lacks periph 0x13 (pon_hlos), so
the keys driver is retargeted to periph 0x08 (pon_pbs, 0x0810) - see the keys
section of TRANSFORMS below.

TRANSFORMS is applied to *every* patch in the series, in order. That matters:
a line added by patch 0209 is quoted as plain context by patch 0211, so both
copies must be rewritten identically or the later patch stops applying. Doing
it globally is the only way that invariant holds without hand-tracking hunks.

Every transform carries the exact number of occurrences it expects across the
whole series (frozen in tools/patch-retarget.lock.json). A mismatch aborts
before anything is written, so an upstream K60 commit that moves one of these
numbers cannot silently produce a half-retargeted series.

  tools/retarget-k60-patches.py --dry-run     # verify counts, write nothing
  tools/retarget-k60-patches.py --freeze      # recompute the lock (after an
                                              # intentional K60 series update)
  tools/retarget-k60-patches.py               # write xnu/patches

Default src: ../ios-sweet-k60/xnu/patches
Default dst: xnu/patches
"""
import argparse
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOCK = os.path.join(HERE, 'patch-retarget.lock.json')

# Ordered: code literals first (exact spacing), prose/log strings after.
# Each entry is (old, new). Occurrence counts live in the lock file.
TRANSFORMS = [
    # --- early console / bootstrap splash (0094, 0095) ---------------------
    ('#define EARLY_WIDTH 1440u', '#define EARLY_WIDTH 1220u'),
    ('#define EARLY_HEIGHT 3200u', '#define EARLY_HEIGHT 2712u'),
    ('#define EARLY_STRIDE (5760u / 4)', '#define EARLY_STRIDE (4880u / 4)'),
    ('0xb8000000 + 2300 * 5760)', '0xb8000000 + 1949 * 4880)'),
    ('mov		x4, #4320', 'mov		x4, #4880'),
    ('/* K60 splash: 1440x3200, stride 5760 */',
     '/* diting splash: 1220x2712, stride 4880 */'),
    ('/* K60 splash_region */', '/* diting splash_region */'),
    ("/* K60: the splash framebuffer", "/* diting: the splash framebuffer"),
    ('This fork targets the Redmi K60 (SM8475): 1440x3200 with a',
     'This fork targets the Redmi K50 Ultra (diting, SM8475): 1220x2712 with a'),
    (' * 5760-byte stride. The sweet constants (1080x2400, 4320)',
     ' * 4880-byte stride. The sweet constants (1080x2400, 4320)'),

    # --- boot logo band (0147) ---------------------------------------------
    ('asks for 18 MiB of kernel VA (5760 x 3200)',
     'asks for 12.6 MiB of kernel VA (4880 x 2712)'),
    ('320;      /* rows, ~1.8 MiB */', '320;      /* rows, ~1.5 MiB */'),

    # --- framebuffer constants (0218) --------------------------------------
    ('#define SWEET_FB_LOGICAL_W              1440',
     '#define SWEET_FB_LOGICAL_W              1220'),
    ('#define SWEET_FB_LOGICAL_H              3200',
     '#define SWEET_FB_LOGICAL_H              2712'),

    # --- SweetHID (0214, 0218) --------------------------------------------
    ('#define SWEET_TOUCH_PANEL_W 1440', '#define SWEET_TOUCH_PANEL_W 1220'),
    ('#define SWEET_TOUCH_PANEL_H 3200', '#define SWEET_TOUCH_PANEL_H 2712'),
    ('SWEET_TOUCH_PANEL_W : 1440', 'SWEET_TOUCH_PANEL_W : 1220'),
    ('SWEET_TOUCH_PANEL_H : 3200', 'SWEET_TOUCH_PANEL_H : 2712'),
    ('	 * K60 的面板是 1440x3200，上游那套 1080x2400 的常量在这里是错的。',
     '	 * diting 的面板是 1220x2712，上游那套 1080x2400 的常量在这里是错的。'),

    # --- touch digitizer (0240, 0242) -------------------------------------
    ('#define K60_TOUCH_PANEL_W\t\t1440\t/* 面板与逻辑分辨率都是 1440x3200 */',
     '#define K60_TOUCH_PANEL_W\t\t1220\t/* 面板与逻辑分辨率都是 1220x2712 */'),
    ('#define K60_TOUCH_PANEL_H\t\t3200', '#define K60_TOUCH_PANEL_H\t\t2712'),
    ('#define K60_TOUCH_ABS_MAX_X\t\t14400', '#define K60_TOUCH_ABS_MAX_X\t\t12200'),
    ('#define K60_TOUCH_ABS_MAX_Y\t\t32000', '#define K60_TOUCH_ABS_MAX_Y\t\t27120'),
    ('panel-max-x/y = 14400 / 32000', 'panel-max-x/y = 12200 / 27120'),
    ('axes 14400 x 32000', 'axes 12200 x 27120'),
    ('（14400 / 32000）', '（12200 / 27120）'),
    ('0x3840', '0x2fa8'),
    ('0x7d00', '0x69f0'),
    ('0x2fa8 (14400)', '0x2fa8 (12200)'),
    ('0x69f0 (32000)', '0x69f0 (27120)'),

    # --- wallpaper size sniffing (0249) -----------------------------------
    ('bytes == 1440u * 3200u * 4 && (w != 1440 || h != 3200)',
     'bytes == 1220u * 2712u * 4 && (w != 1220 || h != 2712)'),
    ('looks like 1440x3200, add sweetwp_w=1440 sweetwp_h=3200',
     'looks like 1220x2712, add sweetwp_w=1220 sweetwp_h=2712'),

    # --- repairs to the upstream series -----------------------------------
    # K60 commit 886101c6 (2026-11-01, "keys: 合体探测...") pasted a stray '+'
    # between a C statement and the comment that follows it, so k60_keys.c
    # stops at "expected expression". Drop the '+' rather than splitting the
    # line, so the hunk's line counts stay exactly as upstream wrote them.
    ('probe cnfg 0x700 nonzero: %u\\n", own);+\t\t/*',
     'probe cnfg 0x700 nonzero: %u\\n", own);\t\t/*'),

    # --- keys PON page (0251) ----------------------------------------------
    # 2026-11-06 实机探测（k60keysprobe=1）：diting 的 SPMI arbiter 表跳过
    # periph 0x12→0x15，pon_hlos@1300 不在表里，apid 读回 ID 全 0；pon_pbs@800
    # (periph 0x08) 在表内（entry 4）。按键页改读 0x0810。
    ('#define K60_KEYS_PON_PPID\t0x0013u\t\t/* SID 0, periph 0x13 */',
     '#define K60_KEYS_PON_PPID\t0x0008u\t\t/* diting: 只有 periph 0x08 (pon_pbs) 在 arbiter 表里 */'),
    ('#define K60_KEYS_PON_RT\t\t0x1310u',
     '#define K60_KEYS_PON_RT\t\t0x0810u'),
    ('0x1305u, 0x1310u, 0x8d46u', '0x1305u, 0x0810u, 0x8d46u'),
    ('那是 PBS 的 PON，APPS 读不到',
     '那是 PBS 的 PON。K60 上 APPS 读不到它，但 diting 的 arbiter 表里只有 PBS 页在（periph 0x13 读回全 0），本 fork 因此改读 0x0810'),
    ('所以按键状态 = periph 0x13 的 0x10：',
     '所以按键状态 = 按键页外设的 0x10（K60: periph 0x13；diting: periph 0x08）：'),

    # --- iovdd LDO vote (0240) ---------------------------------------------
    # 2026-11-07 实机定位：VRM 的 VOLTAGE 寄存器对 LDO 要直接投毫伏，
    # 不是 ARC 档位索引。原码投了 index=0（=0V），触摸 IC 因此一直没电，
    # MISO 悬空读 0。cmd_db 对 LDO 条目的 aux data 不是电压表（ldoc2 解析出
    # 10753=0x2A01，是 periph 表），level_index 只是碰巧没报错。
    ('value[0] = index;', 'value[0] = mv;'),

    # --- remaining prose / log text ---------------------------------------
    ('等比放大 1.28x（1440/1125）并居中：1440x3118，',
     '等比放大 1.08x（1220/1125）并居中：1220x2642，'),
    ('上下各 41 行', '上下各 35 行'),
    ('1440x3118', '1220x2642'),
    ('1440x3200', '1220x2712'),
    ('1440 x 3200', '1220 x 2712'),
]


def load_sources(src):
    names = sorted(n for n in os.listdir(src) if not n.startswith('.'))
    out = []
    for name in names:
        path = os.path.join(src, name)
        if not os.path.isfile(path):
            continue
        out.append((name, open(path, 'rb').read()))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--src', default=os.path.join(ROOT, '..', 'ios-sweet-k60',
                                                  'xnu', 'patches'))
    ap.add_argument('--dst', default=os.path.join(ROOT, 'xnu', 'patches'))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--freeze', action='store_true',
                    help='recompute occurrence counts and rewrite the lock file')
    a = ap.parse_args()
    src, dst = os.path.abspath(a.src), os.path.abspath(a.dst)
    if not os.path.isdir(src):
        sys.exit('source patch dir not found: %s' % src)

    files = load_sources(src)
    # The K60 series is authored on Windows (CRLF). A patch is line based, so
    # keep only LF here: otherwise every regeneration rewrites all 209 files
    # and buries the one line that actually changed.
    texts = [(name, data.decode('utf-8', 'surrogateescape').replace('\r\n', '\n'))
             for name, data in files]

    lock = None
    if os.path.isfile(LOCK) and not a.freeze:
        lock = json.load(open(LOCK, encoding='utf-8'))
        if len(lock) != len(TRANSFORMS):
            sys.exit('lock has %d entries, table has %d - re-run --freeze'
                     % (len(lock), len(TRANSFORMS)))
        for i, (entry, (old, new)) in enumerate(zip(lock, TRANSFORMS)):
            if entry['old'] != old:
                sys.exit('lock entry %d is %r, table has %r - re-run --freeze'
                         % (i, entry['old'], old))

    # Apply the table cumulatively: each transform sees the output of the
    # previous one, and its count is measured at that point.
    totals = []
    for old, new in TRANSFORMS:
        n = sum(text.count(old) for _, text in texts)
        if n == 0:
            sys.exit('transform never matches: %r' % old)
        totals.append(n)
        texts = [(name, text.replace(old, new)) for name, text in texts]

    if lock is not None:
        for i, (entry, (old, _)) in enumerate(zip(lock, TRANSFORMS)):
            if entry['count'] != totals[i]:
                sys.exit('upstream drift: %r occurs %d times, lock says %d'
                         % (old, totals[i], entry['count']))

    changed = []
    for (name, data), (_, text) in zip(files, texts):
        out = text.encode('utf-8', 'surrogateescape')
        if out != data:
            changed.append((name, len(data), len(out)))
        if a.dry_run:
            continue
        os.makedirs(dst, exist_ok=True)
        with open(os.path.join(dst, name), 'wb') as f:
            f.write(out)
        shutil.copymode(os.path.join(src, name), os.path.join(dst, name))

    if a.freeze and not a.dry_run:
        frozen = [{'old': old, 'new': new, 'count': totals[i]}
                  for i, (old, new) in enumerate(TRANSFORMS)]
        with open(LOCK, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(frozen, f, ensure_ascii=False, indent=1)
            f.write('\n')

    print('%s -> %s  (%d files, %d rewritten, %d transforms)%s'
          % (src, dst, len(files), len(changed), len(TRANSFORMS),
             '  [dry run]' if a.dry_run else ''))
    for i, (old, new) in enumerate(TRANSFORMS):
        print('  %2d  x%-3d %s' % (i, totals[i], old.splitlines()[0][:72]))
    for name, before, after in changed:
        print('  %-46s %6d -> %6d bytes' % (name, before, after))
    if not a.dry_run:
        h = hashlib.sha256()
        for name, _ in files:
            h.update(open(os.path.join(dst, name), 'rb').read())
        print('series sha256: %s' % h.hexdigest()[:16])
    return 0


if __name__ == '__main__':
    sys.exit(main())

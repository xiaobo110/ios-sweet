#!/usr/bin/env python3
"""Fork the K60 XNU patch series into a diting (Redmi K50 Ultra) series.

The K60 series is already a cape/SM8475 bring-up; diting differs only in the
panel (1220x2712 vs 1440x3200, stride 4880 vs 5760) and in the touch
digitizer's reported maximums (12200x27120 vs 14400x32000). Everything else -
GIC, timer, UFS, RSC/GENI touch path, memory notches - is shared. The keys
PON read path is NOT: the K60 driver reads PON through sweet_spmi_read(),
which needs SweetBattery's sweet_spmi_init() to succeed first. On diting that
reports "unsupported" and every PON poll fails, so the fork reads PON on the
key driver's own observation channel (see the keys section of TRANSFORMS).

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
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOCK = os.path.join(HERE, 'patch-retarget.lock.json')

HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$')

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

    # --- keys PON read path (0251) -----------------------------------------
    # PON 的 APID 在 diting 上找得到（init 行：ppid 0x0013 apid=407 owner=0），
    # 地址不用改。坏的是读取入口：K60 走 sweet_spmi_read()，那条路要先
    # SweetBattery 把 sweet_spmi_init() 跑成功；diting 上电池驱动报
    # "unsupported SPMI arb"，于是每个 PON 轮询都失败（实机日志从第 15801
    # 次起一直 "pon 0x1310 unreadable"），而音量加的直读通道是好的。
    # 改成用按键自己的观察通道读 PON：APID 分槽，不与电池的 lane 抢锁。
    ('''+static bool
+keys_read_pon(uint16_t addr, uint8_t *out)
+{
+	if (sweet_spmi_read(0, addr, out) == 0) {
+		return true;
+	}
+	if (!ks.spmi_retried) {
+		int r;
+
+		ks.spmi_retried = true;
+		r = sweet_spmi_init();
+		printf("K60 KEYS: pon read failed, sweet_spmi_init() = %d "
+		    "(0 = the battery half was not up yet)\\n", r);
+		keys_flush();
+		if (r == 0 && sweet_spmi_read(0, addr, out) == 0) {
+			return true;
+		}
+	}
+	return false;
+}''', '''+static bool
+keys_read_pon(uint16_t addr, uint8_t *out)
+{
+	/*
+	 * diting: 不走 sweet_spmi_read()。那条路要先 SweetBattery 把
+	 * sweet_spmi_init() 跑成功，diting 上电池驱动报 "unsupported"，
+	 * PON 轮询因此永远失败（实机日志：pon 0x1310 unreadable）。
+	 * 观察通道按 APID 分槽，PON 用自己找到的 APID 直读，不与电池
+	 * 的 lane 抢 sp_lock。
+	 */
+	return keys_read(keys_pon_apid, addr, out);
+}'''),

    # --- iovdd LDO vote (0240) ---------------------------------------------
    # 2026-11-07 实机定位：VRM 的 VOLTAGE 寄存器对 LDO 要直接投毫伏，
    # 不是 ARC 档位索引。原码投了 index=0（=0V），触摸 IC 因此一直没电，
    # MISO 悬空读 0。cmd_db 对 LDO 条目的 aux data 不是电压表（ldoc2 解析出
    # 10753=0x2A01，是 periph 表），level_index 只是碰巧没报错。
    ('value[0] = index;', 'value[0] = mv;'),

    # --- GENI FIFO interface (0241) ----------------------------------------
    # 2026-11-07 实机定位：iovdd 投上 1800 mV 之后 GENI 仍然 status 0、
    # sent 36 received 0 —— 引擎根本没启动。ABL 给 SE4 留的是 GSI DMA 配置：
    # GENI_IF_DISABLE_RO(0x64) 读 1 = FIFO 接口关着。mainline
    # drivers/soc/qcom/qcom-geni-se.c 的固件加载尾巴上有三步我们缺了：
    #   CGC 的 PROG_RAM 位(8/9) toggling 之下写 SE_GENI_CLK_CTRL(0x2000)
    #   选 SER_CLK_SEL、SE_DMA_IF_EN(0x2004) 置位、
    #   SE_FIFO_IF_DISABLE(0x2008) 清零。
    # 之前"0x1000 以上一读就挂总线"正是因为没做这个 CGC toggling。
    ('+static vm_offset_t k60_gcc, k60_se;', '''+/* CLK_CTRL(0x2000) 的 bit0 选串行时钟；CGC_CTRL(0x28) 的 bit8/9 是 PROG_RAM。 */
+#define K60_SE_SER_CLK_SEL		(1u << 0)
+#define K60_SE_CGC_PROG_RAM		((1u << 8) | (1u << 9))
+
+static vm_offset_t k60_gcc, k60_se;'''),
    ('''+	/*
+	 * 0x64 只读，bit0=1 表示这颗 SE 的 FIFO 接口是关的。真正的开关在 0x2008，
+	 * 但那页寄存器（0x1000 以上）一读就挂总线，实测直接把整机带走 —— 所以
+	 * 只读 0x64，然后走 k60touchbb=1 的手工 SPI。
+	 */''', '''+	/*
+	 * 0x64 只读，bit0=1 表示这颗 SE 的 FIFO 接口是关的（ABL 留的是 GSI DMA
+	 * 配置）。开关在 0x2008，下面按 mainline 的 CGC toggling 打开它。
+	 */'''),
    ('+	/* 模式 0、CS0、8 位、MSB 先、四个字节一个 FIFO 字。 */', '''+	/*
+	 * 打开 FIFO 接口。k60touchfifo=0 可以整段跳过：0x2000 页万一仍然挂总线，
+	 * cmdline 是镜像里外科补丁改的，不用重编就能退回手工 SPI。
+	 * 每一次 0x2000 页访问之前都先刷盘，挂住时盘上最后一行就是凶手。
+	 */
+	{
+		int fifo_if = 1;
+
+		(void)PE_parse_boot_argn("k60touchfifo", &fifo_if,
+		    sizeof(fifo_if));
+		if (fifo_if != 0) {
+			printf("K60 TOUCH: geni cgc %#x, opening the 0x2000 page\\n",
+			    K60_TOUCH_RD(k60_se + K60_SE_CGC_CTRL));
+			k60_touch_flush();
+			K60_TOUCH_WR(k60_se + K60_SE_CGC_CTRL,
+			    K60_TOUCH_RD(k60_se + K60_SE_CGC_CTRL) |
+			    K60_SE_CGC_PROG_RAM);
+			printf("K60 TOUCH: geni clk_ctrl <- 0 (cgc %#x)\\n",
+			    K60_TOUCH_RD(k60_se + K60_SE_CGC_CTRL));
+			k60_touch_flush();
+			K60_TOUCH_WR(k60_se + K60_SE_GENI_CLK_CTRL, 0);
+			K60_TOUCH_WR(k60_se + K60_SE_CGC_CTRL,
+			    K60_TOUCH_RD(k60_se + K60_SE_CGC_CTRL) &
+			    ~K60_SE_CGC_PROG_RAM);
+			printf("K60 TOUCH: geni clk_ctrl %#x\\n",
+			    K60_TOUCH_RD(k60_se + K60_SE_GENI_CLK_CTRL));
+			k60_touch_flush();
+			K60_TOUCH_WR(k60_se + K60_SE_CGC_CTRL,
+			    K60_TOUCH_RD(k60_se + K60_SE_CGC_CTRL) |
+			    K60_SE_CGC_PROG_RAM);
+			K60_TOUCH_WR(k60_se + K60_SE_GENI_CLK_CTRL,
+			    K60_SE_SER_CLK_SEL);
+			K60_TOUCH_WR(k60_se + K60_SE_CGC_CTRL,
+			    K60_TOUCH_RD(k60_se + K60_SE_CGC_CTRL) &
+			    ~K60_SE_CGC_PROG_RAM);
+			printf("K60 TOUCH: geni ser clk selected, clk_ctrl %#x\\n",
+			    K60_TOUCH_RD(k60_se + K60_SE_GENI_CLK_CTRL));
+			k60_touch_flush();
+			K60_TOUCH_WR(k60_se + K60_SE_DMA_IF_EN,
+			    K60_TOUCH_RD(k60_se + K60_SE_DMA_IF_EN) | 1u);
+			printf("K60 TOUCH: geni dma_if_en %#x\\n",
+			    K60_TOUCH_RD(k60_se + K60_SE_DMA_IF_EN));
+			k60_touch_flush();
+			K60_TOUCH_WR(k60_se + K60_SE_FIFO_IF_DISABLE,
+			    K60_TOUCH_RD(k60_se + K60_SE_FIFO_IF_DISABLE) & ~1u);
+			printf("K60 TOUCH: geni fifo_if_disable_ro now %#x "
+			    "(0 = FIFO usable)\\n",
+			    K60_TOUCH_RD(k60_se + K60_SE_IF_DISABLE_RO));
+			k60_touch_flush();
+		}
+	}
+
+	/* 模式 0、CS0、8 位、MSB 先、四个字节一个 FIFO 字。 */'''),
    ('+	printf("K60 TOUCH: geni fifo_if_disable_ro %#x (the 0x2000 page is unsafe)\\n",',
     '+	printf("K60 TOUCH: geni fifo_if_disable_ro %#x (before the 0x2008 write)\\n",'),
    ('+	/* 0x2000 那页碰不得（读了挂总线），这里只报一句；FIFO 路径留给以后的 DMA 版本。 */',
     '+	/* FIFO 接口已经在上面打开，这里报最终状态。 */'),

    # --- remaining prose / log text ---------------------------------------
    ('等比放大 1.28x（1440/1125）并居中：1440x3118，',
     '等比放大 1.08x（1220/1125）并居中：1220x2642，'),
    ('上下各 41 行', '上下各 35 行'),
    ('1440x3118', '1220x2642'),
    ('1440x3200', '1220x2712'),
    ('1440 x 3200', '1220 x 2712'),
]


def normalise_hunks(text, name):
    """Rewrite every @@ header so its counts match the body.

    TRANSFORMS are plain string substitutions, so a multi-line one changes how
    many lines a hunk holds while the header keeps the upstream count. git then
    rejects the patch as malformed ("corrupt patch at line N"), build.sh only
    logs "Skipping patch" and the kernel silently loses a whole driver - the
    0251 keys driver went missing exactly that way. Recount here, and hard-fail
    on a line that is not a hunk line at all (a transform that glued two lines
    together leaves the body unparseable rather than just miscounted).
    """
    lines = text.split('\n')
    out = []
    i = 0
    fixed = 0
    while i < len(lines):
        m = HUNK.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        old = new = 0
        while j < len(lines):
            line = lines[j]
            if HUNK.match(line) or line.startswith('diff --git') \
                    or line.startswith('Index: '):
                break
            # The K60 series separates files with a bare ---/+++ pair instead of
            # a diff --git line; those two lines are not hunk content.
            if line.startswith('--- ') and j + 1 < len(lines) \
                    and lines[j + 1].startswith('+++ '):
                break
            if line == '' and (j + 1 >= len(lines)
                               or lines[j + 1].startswith('diff --git')):
                break                  # separator or the trailing newline
            if line.startswith('\\'):      # "\ No newline at end of file"
                pass
            elif line.startswith('+'):
                new += 1
            elif line.startswith('-'):
                old += 1
            elif line.startswith(' ') or line == '':
                old += 1
                new += 1
            else:
                sys.exit('%s:%d: not a hunk line: %r - a transform joined two '
                         'lines' % (name, j + 1, line[:70]))
            j += 1
        a, b, c, d = m.group(1), m.group(2), m.group(3), m.group(4)
        if old == int(b or 1) and new == int(d or 1):
            out.append(lines[i])
        else:
            fixed += 1
            out.append('@@ -%s%s +%s%s @@%s' % (
                a, '' if old == 1 else ',%d' % old,
                c, '' if new == 1 else ',%d' % new, m.group(5)))
        out.extend(lines[i + 1:j])
        i = j
    return '\n'.join(out)


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

    texts = [(name, normalise_hunks(text, name) if name.endswith('.patch')
              else text) for name, text in texts]

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

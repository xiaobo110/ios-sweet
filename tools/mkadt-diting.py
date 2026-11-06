#!/usr/bin/env python3
"""Build the Apple Device Tree for the Redmi K50 Ultra (diting, SM8475).

Same ADT shape as the K60 one (tools/mkadt.py in ios-sweet-k60) with diting's
numbers, all of them read from board/diting.json so there is one source of
truth:

  * /chosen dram-base + dram-size  -> where XNU thinks RAM starts (drives where
    the shim places the payload/ADT, see MEMORY-PLAN.md)
  * /sweet-fbcon reg/width/height/stride -> kernel console and, through
    boot_args.Video, the IOMobileFramebuffer physical surface
  * /arm-io/disp0 canvas-width/height -> panel geometry CA sees before
    SweetFramebuffer overrides it
  * GICD/GICR + virtual-timer-intid 27 -> cape's standard PPI numbering

  python3 tools/mkadt-diting.py OUT.adt [--mem-base 0xb4000000 --mem-window 0x2c000000]
                                   [--dump] [--no-product]

Requires adt.py from the ios-sweet-k60 checkout (IOS_SWEET_K60, default
../ios-sweet-k60).
"""
import argparse
import hashlib
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BOARD = os.path.join(ROOT, 'board', 'diting.json')
K60 = os.environ.get('IOS_SWEET_K60', os.path.join(ROOT, '..', 'ios-sweet-k60'))

sys.path.insert(0, os.path.join(K60, 'tools'))
import adt  # noqa: E402

u32 = lambda *v: struct.pack('<%dI' % len(v), *v)                    # noqa: E731
u64 = lambda *v: struct.pack('<%dQ' % len(v), *v)                    # noqa: E731
cstr = lambda s: s.encode() + b'\0'                                  # noqa: E731

BOOT_MANIFEST_HASH = hashlib.sha384(b'sweet boot manifest').digest()


def node(name, props=(), children=()):
    n = adt.Node()
    n.props = [('name', 0, cstr(name))] + [(k, 0, v) for k, v in props]
    n.children = list(children)
    return n


def product_props(path):
    """/product copied from the real iPhone X tree (MobileGestalt identity)."""
    if not path or not os.path.exists(path):
        print('warning: %s missing, no /product node' % path, file=sys.stderr)
        return []
    root, _ = adt.parse(open(path, 'rb').read())
    for c in root.children:
        if dict((k, v) for k, f, v in c.props).get('name') == b'product\0':
            return [(k, v) for k, f, v in c.props
                    if not f and k not in ('name', 'AAPL,phandle')]
    raise SystemExit('%s: no /product node' % path)


def build(b, mem_base, mem_window, with_product):
    d = b['display']
    panic_size = int(b['memory']['panic_log_size'], 16)
    if mem_window <= panic_size + 0x1000000:
        raise SystemExit('memory window %#x too small' % mem_window)
    dram_size = mem_window - panic_size
    panic_base = mem_base + dram_size

    phandle = iter(range(1, 1000))
    ph = lambda: ('AAPL,phandle', u32(next(phandle)))                 # noqa: E731

    idn = b['identity']
    chosen = node('chosen', [
        ph(),
        ('dram-base', u64(mem_base)),
        ('dram-size', u64(dram_size)),
        ('random-seed', os.urandom(64)),
        ('debug-enabled', u32(1)),
        ('embedded-panic-log-size', u32(panic_size)),
        ('firmware-version', cstr('sweet-shim')),
        ('system-firmware-version', cstr('sweet-shim')),
        ('boot-manifest-hash', BOOT_MANIFEST_HASH),
        ('crypto-hash-method', cstr('sha2-384')),
        ('display-scale', u32(idn['display_scale'])),
        ('unique-chip-id', struct.pack('<Q', int(idn['synthetic_ecid'], 16))),
        ('chip-id', u32(idn['chip_id'])),
        # dev-fused, undemoted board: mobileactivationd takes its native
        # "Hactivation" branch without talking to Apple.
        ('certificate-production-status', u32(0)),
        ('effective-production-status-ap', u32(0)),
        ('certificate-security-mode', u32(1)),
        ('effective-security-mode-sep', u32(1)),
        # MobileGestalt reads this first; the IORegistry fallback waits on a
        # semaphore that never fires without a WiFi service (lsd hangs).
        ('mac-address-wifi0', bytes(int(x, 16) for x in b['wifi_mac'].split(':'))),
    ], [node('memory-map', [ph()]),
        # kern_csr.c: SIP relaxed as with `csrutil disable`; lp-sip1 = unsealed
        # root, needed because the iOS shared cache is in no trust cache.
        node('asmb', [ph(), ('lp-sip0', u64(0x6f)), ('lp-sip1', u32(1))])])

    cpu_nodes = []
    for core in range(b['cpu']['cores']):
        cpu_nodes.append(node('cpu%d' % core, [
            ph(),
            ('device_type', cstr('cpu')),
            ('compatible', cstr('qcom,kryo') + cstr('ARM,v8')),
            ('reg', u32(core << 8)),
            ('cpu-id', u32(core)),
            ('cluster-id', u32(0 if core < 4 else (1 if core < 7 else 2))),
            ('cluster-type', u32(0)),
            ('die-cluster-id', u32(0)),
            ('cluster-core-id', u32(core)),
            ('l2-cache-id', u32(core)),
            ('l3-cache-id', u32(0)),
            ('state', cstr('running' if core == 0 else 'stopped')),
            ('timebase-frequency', u32(b['timer']['timebase'])),
            ('clock-frequency', u32(3187200000 if core == 0 else
                                    2803200000 if core < 4 else 2016000000)),
            ('bus-frequency', u32(100000000)),
            ('memory-frequency', u32(3200000000)),
            ('peripheral-frequency', u32(b['timer']['timebase'])),
            ('fixed-frequency', u32(b['timer']['timebase'])),
        ]))
    cpus = node('cpus', [ph(), ('#address-cells', u32(1)),
                         ('#size-cells', u32(0))], cpu_nodes)

    gicd = int(b['gic']['gicd'], 16)
    gicr = int(b['gic']['gicr'], 16)
    soc_base = 0x01000000
    gic = node('gic', [
        ph(),
        ('compatible', cstr('arm,gic-v3')),
        ('interrupt-controller', b''),
        ('virtual-timer-intid', u32(b['gic']['vtimer_intid'])),
        ('redistributor-stride', u32(int(b['gic']['gicr_pe_size'], 16))),
        ('reg', u64(gicd - soc_base, int(b['gic']['gicd_size'], 16),
                    gicr - soc_base, int(b['gic']['gicr_size'], 16))),
    ])
    arm_io = node('arm-io', [
        ph(),
        ('device_type', cstr('sweet-io')),
        ('compatible', cstr('sweet-io') + cstr('arm-io')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
        ('ranges', u64(0, soc_base, 0x100000000 - soc_base)),
    ], [gic,
        node('wdt', [ph(), ('no-pmu', u32(1))]),
        node('disp0', [
            ph(),
            ('device_type', cstr('display-subsystem')),
            ('compatible', cstr('sweet,display')),
            ('canvas-width', u32(d['width'])),
            ('canvas-height', u32(d['height'])),
        ])])

    fbcon = node('sweet-fbcon', [
        ph(),
        ('reg', u64(int(d['fb_base'], 16), int(d['fb_size'], 16))),
        ('width', u32(d['width'])),
        ('height', u32(d['height'])),
        ('stride', u32(d['stride'])),
    ])
    ufs = node('sweet-ufs', [
        ph(),
        ('compatible', cstr('qcom,ufshc')),
        ('reg', u64(int(b['ufs']['base'], 16), int(b['ufs']['size'], 16))),
    ])
    defaults = node('defaults', [ph(), ('no-effaceable-storage', u32(1))])
    buttons = node('buttons', [
        ph(),
        ('button-names', cstr('volup') + cstr('voldown') + cstr('hold')),
        ('home-button-type', u32(2)),
        ('opposed-power-vol-buttons', u32(1)),
    ])
    pram = node('pram', [ph(), ('reg', u64(panic_base, panic_size))])

    prod = [node('product', [ph()] + product_props(idn.get('product_adt'))) ] \
        if with_product else []

    compat = idn['adt_compatible']
    return node('device-tree', [
        ph(),
        ('compatible', b''.join(cstr(c) for c in compat)),
        ('model', cstr(idn['model'])),
        ('target-type', cstr(idn['target_type'])),
        ('target-sub-type', cstr(idn['target_sub_type'])),
        ('manufacturer', cstr('Apple Inc.')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
    ], [chosen, defaults, cpus, arm_io, fbcon, buttons, pram, ufs] + prod)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('out')
    ap.add_argument('--board', default=BOARD)
    ap.add_argument('--mem-base', default=None)
    ap.add_argument('--mem-window', default=None)
    ap.add_argument('--no-product', action='store_true')
    ap.add_argument('--dump', action='store_true')
    a = ap.parse_args()

    b = json.load(open(a.board, encoding='utf-8'))
    # product_adt is written relative to the workspace (it lives in the K60
    # checkout); resolve it against the board file and the K60 root.
    pa = b['identity'].get('product_adt')
    if pa and not os.path.isabs(pa):
        for cand in (os.path.join(os.path.dirname(os.path.abspath(a.board)), pa),
                     os.path.join(K60, pa),
                     os.path.join(ROOT, '..', pa)):
            if os.path.exists(cand):
                b['identity']['product_adt'] = os.path.normpath(cand)
                break
    mem_base = int(a.mem_base, 0) if a.mem_base else int(b['memory']['dram_base'], 16)
    mem_window = int(a.mem_window, 0) if a.mem_window else int(b['memory']['dram_window'], 16)
    root = build(b, mem_base, mem_window, not a.no_product)
    blob = adt.serialize(root)
    with open(a.out, 'wb') as f:
        f.write(blob)
    if a.dump:
        adt.dump(adt.parse(blob)[0])

    d = b['display']
    print('%s: %d bytes' % (a.out, len(blob)), file=sys.stderr)
    print('  dram       %#x + %#x (%d MiB), panic log at %#x'
          % (mem_base, mem_window - 0x100000, (mem_window - 0x100000) >> 20,
             mem_base + mem_window - 0x100000), file=sys.stderr)
    print('  framebuffer %s %dx%d stride %d'
          % (d['fb_base'], d['width'], d['height'], d['stride']), file=sys.stderr)
    print('  gic        %s/%s timer intid %d'
          % (b['gic']['gicd'], b['gic']['gicr'], b['gic']['vtimer_intid']),
          file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())

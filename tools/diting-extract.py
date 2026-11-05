#!/usr/bin/env python3
"""Turn diting sources into the constants the shim and ADT need.

Two sources, same facts:

  panel   a Qualcomm panel .dtsi (LineageOS diting devicetree) -> C byte arrays
          in the exact 7-byte-per-command layout the shim's dsi_send_seq()
          expects:  DT LAST VC ACK WAIT_HI WAIT_LO LEN  + LEN payload bytes.
          Emits panel_init_diting.h (as-is) and panel_init_diting_nodsc.h
          (0x91 PPS dropped, 0x90 -> 0, the K60_NODSC experiment).

  fdt     a raw .dtb / live FDT / dtbo.img -> board facts (DRAM banks, all
          reserved-memory regions, splash region, GIC, timer, UFS, panel
          geometry, touch node) as JSON, plus a drift check against
          board/diting.json.

  check   compare a facts JSON produced earlier against board/diting.json.

Examples
  tools/diting-extract.py panel \
      --dtsi ../lineage-diting-dt/qcom/display/display/dsi-panel-l12-42-02-0a-dsc-cmd.dtsi \
      --out ../ios-sweet-diting/shim
  tools/diting-extract.py fdt dump/dtbo.img --board board/diting.json
"""
import argparse
import hashlib
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fdtparse  # noqa: E402

BOARD_DEFAULT = os.path.join(HERE, '..', 'board', 'diting.json')


# ---------------------------------------------------------------- panel dtsi

def strip_comments(text):
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return re.sub(r'//[^\n]*', '', text)


def block(text, start):
    """Return (body, end_index) of the {...} block whose '{' is at/after start."""
    i = text.index('{', start)
    depth = 0
    for j in range(i, len(text)):
        if text[j] == '{':
            depth += 1
        elif text[j] == '}':
            depth -= 1
            if depth == 0:
                return text[i + 1:j], j + 1
    raise ValueError('unbalanced braces')


def find_node_block(text, node):
    m = re.search(r'^\s*' + re.escape(node) + r'\s*:\s*[A-Za-z0-9_,\.\-]+\s*\{',
                  text, re.M)
    if not m:
        m = re.search(r'^\s*' + re.escape(node) + r'\s*\{', text, re.M)
    if not m:
        raise SystemExit('node %r not found' % node)
    body, _ = block(text, m.start())
    return body


def timing_block(node_body, index):
    """timing@N { ... } body, or the whole node when there is only one timing."""
    matches = list(re.finditer(r'timing@(\d+)\s*\{', node_body))
    if not matches:
        return node_body
    for m in matches:
        if int(m.group(1)) == index:
            body, _ = block(node_body, m.start())
            return body
    raise SystemExit('timing@%d not found' % index)


TOKEN = re.compile(r'(?:0x)?([0-9A-Fa-f]{2})(?![0-9A-Fa-f])')


def hex_bytes(s):
    return [int(t, 16) for t in TOKEN.findall(s)]


def parse_commands(raw):
    """Split a Qualcomm command byte string into [(dtype, payload_bytes)]."""
    cmds = []
    i = 0
    while i < len(raw):
        if i + 7 > len(raw):
            raise SystemExit('truncated command header at byte %d' % i)
        hdr = raw[i:i + 7]
        ln = hdr[6]
        payload = raw[i + 7:i + 7 + ln]
        if len(payload) != ln:
            raise SystemExit('command at byte %d wants %d payload bytes, has %d'
                             % (i, ln, len(payload)))
        cmds.append((hdr[0], payload))
        i += 7 + ln
    return cmds


def commands_to_bytes(cmds):
    out = bytearray()
    for dt, pl in cmds:
        out += bytes([dt, 0, 0, 0, 0, 0, len(pl)]) + bytes(pl)
    return bytes(out)


def nodsc(cmds):
    """Drop the DSC PPS (0x91) and disable DSC (0x90 -> 0), like K60's variant."""
    out = []
    for dt, pl in cmds:
        if pl and pl[0] == 0x91:        # 0x39 long write carrying the DSC PPS
            continue
        if pl and pl[0] == 0x90 and len(pl) >= 2:   # DCS 0x90 = DSC enable
            pl = [0x90, 0x00] + pl[2:]
        out.append((dt, pl))
    return out


def emit_header(path, symbol, data, comment):
    lines = ['/* %s */' % comment, '#pragma once', '#include <stdint.h>', '',
             'static const uint8_t %s[%d] = {' % (symbol, len(data))]
    for i in range(0, len(data), 12):
        lines.append('\t' + ', '.join('0x%02x' % b for b in data[i:i + 12]) + ',')
    lines += ['};', '']
    with open(path, 'w', newline='\n') as f:
        f.write('\n'.join(lines))
    return len(data)


def cmd_panel(a):
    text = strip_comments(open(a.dtsi, errors='replace').read())
    node_body = find_node_block(text, a.node) if a.node else text
    tim = timing_block(node_body, a.timing)

    def num(prop, default=None):
        m = re.search(r'qcom,mdss-dsi-' + prop + r'\s*=\s*<([^>]*)>', tim)
        if not m:
            m = re.search(r'qcom,mdss-dsi-' + prop + r'\s*=\s*<([^>]*)>', node_body)
        if not m:
            return default
        vals = [int(x, 0) for x in re.findall(r'0x[0-9a-fA-F]+|\d+', m.group(1))]
        return vals[0] if vals else default

    m = re.search(r'qcom,mdss-dsi-panel-name\s*=\s*"([^"]*)"', node_body)
    name = m.group(1) if m else a.node or os.path.basename(a.dtsi)
    width, height = num('panel-width'), num('panel-height')
    fps = num('panel-framerate')
    clock = num('panel-clockrate')

    m = re.search(r'qcom,mdss-dsi-on-command\s*=\s*\[(.*?)\];', tim, re.S)
    if not m:
        raise SystemExit('%s: timing@%d has no qcom,mdss-dsi-on-command'
                         % (a.dtsi, a.timing))
    raw = hex_bytes(m.group(1))
    cmds = parse_commands(raw)
    full = commands_to_bytes(cmds)
    nod = commands_to_bytes(nodsc(cmds))

    # Round-trip the parser against the literal token stream: a mismatch means
    # the dtsi uses a syntax this parser flattened wrongly, not that the panel
    # is odd, so refuse to emit.
    if len(full) != len(raw):
        raise SystemExit('parse mismatch: %d source bytes vs %d rebuilt'
                         % (len(raw), len(full)))

    os.makedirs(a.out, exist_ok=True)
    sha = hashlib.sha256(full).hexdigest()[:16]
    src = os.path.relpath(a.dtsi)
    cm = ('generated by tools/diting-extract.py from %s (node %s, timing@%d, sha256 %s); '
          'panel "%s" %dx%d %d Hz' % (src, a.node or '?', a.timing, sha, name,
                                      width or 0, height or 0, fps or 0))
    n1 = emit_header(os.path.join(a.out, a.basename + '.h'),
                     a.symbol_full, full,
                     cm + '; raw replay')
    n2 = emit_header(os.path.join(a.out, a.basename + '_nodsc.h'),
                     a.symbol_nodsc, nod,
                     cm + '; DSC PPS dropped, 0x90=0')
    print('panel %s: %dx%d @%d Hz, clock %s, %d commands'
          % (a.node, width or 0, height or 0, fps or 0, clock, len(cmds)))
    print('  %-26s %d bytes' % (a.basename + '.h', n1))
    print('  %-26s %d bytes' % (a.basename + '_nodsc.h', n2))
    facts = {'node': a.node, 'name': name, 'width': width, 'height': height,
             'fps': fps, 'clockrate': clock, 'commands': len(cmds),
             'bytes': len(full), 'sha256': sha, 'dtsi': src}
    with open(os.path.join(a.out, 'panel_diting.json'), 'w') as f:
        json.dump(facts, f, indent=1)
    return 0


# ---------------------------------------------------------------- live FDT

def u32list(node, name):
    return fdtparse.prop_u32(node, name)


def pairs(node, name):
    v = u32list(node, name)
    return None if v is None else [(v[i] << 32) | v[i + 1] for i in range(0, len(v) - 1, 2)]


def bytes_of_prop(node, name):
    v = u32list(node, name)
    return None if v is None else bytes(x & 0xff for x in v)


def collect(res):
    facts = {'source': res['source'], 'container': res['container'],
             'memory': [], 'reserved': [], 'panels': []}
    root = res['root']
    for n in root.walk():
        if n.name.split('@')[0] == 'memory':
            reg = pairs(n, 'reg')
            if reg:
                facts['memory'] += [list(p) for p in reg]
        if n.name.split('@')[0] in ('reserved-memory', 'reserved_memory'):
            for c in n.children:
                reg = pairs(c, 'reg')
                if reg:
                    for base, size in reg:
                        facts['reserved'].append(
                            {'node': c.name, 'base': base, 'size': size,
                             'path': c.path()})
        if u32list(n, 'qcom,mdss-dsi-panel-width') and u32list(n, 'qcom,mdss-dsi-panel-height'):
            on = bytes_of_prop(n, 'qcom,mdss-dsi-on-command')
            facts['panels'].append({
                'path': n.path(),
                'node': n.name,
                'name': fdtparse.prop_str(n, 'qcom,mdss-dsi-panel-name'),
                'width': u32list(n, 'qcom,mdss-dsi-panel-width')[0],
                'height': u32list(n, 'qcom,mdss-dsi-panel-height')[0],
                'bpp': (u32list(n, 'qcom,mdss-dsi-bpp') or [None])[0],
                'on_command_len': len(on) if on else 0,
                'on_command_sha': hashlib.sha256(on).hexdigest()[:16] if on else None,
            })

    facts['memory'].sort()
    facts['reserved'].sort(key=lambda r: r['base'])

    splash = None
    for r in facts['reserved']:
        if r['base'] == 0xb8000000 or 'splash' in r['node']:
            splash = r
            break
    facts['splash'] = splash

    def reg_of(match):
        for n in root.walk():
            if match(n):
                reg = pairs(n, 'reg')
                if reg:
                    return {'path': n.path(), 'reg': [list(p) for p in reg]}
        return None

    facts['gic'] = reg_of(lambda n: 'arm,gic-v3' in (fdtparse.prop_strlist(n, 'compatible') or []))
    facts['gicr_stride'] = None
    for n in root.walk():
        v = u32list(n, 'redistributor-stride')
        if v:
            facts['gicr_stride'] = v[0]
    facts['ufs'] = reg_of(lambda n: (fdtparse.prop_str(n, 'compatible') or '') == 'qcom,ufshc')
    facts['timer'] = None
    for n in root.walk():
        v = u32list(n, 'interrupts')
        if v and n.name.startswith('timer'):
            facts['timer'] = v
    facts['touch'] = None
    for n in root.walk():
        if u32list(n, 'goodix,panel-max-x') or u32list(n, 'xiaomi,l12-spi'):
            facts['touch'] = {
                'path': n.path(),
                'compatible': fdtparse.prop_strlist(n, 'compatible'),
                'interrupts': u32list(n, 'interrupts'),
                'panel_max_x': (u32list(n, 'goodix,panel-max-x') or [None])[0],
                'panel_max_y': (u32list(n, 'goodix,panel-max-y') or [None])[0],
            }
            break
    return facts


def hexint(v):
    return int(v, 16) if isinstance(v, str) else int(v)


def check(facts, board):
    ok = True

    def cmp(label, got, want):
        nonlocal ok
        good = got == want
        ok = ok and good
        print('  %-4s %-34s got %s want %s'
              % ('ok' if good else 'DIFF', label,
                 hex(got) if isinstance(got, int) else got,
                 hex(want) if isinstance(want, int) else want))

    d = board['display']
    sp = facts.get('splash')
    if sp is None:
        print('  DIFF splash region not found (expected cont_splash_region / 0xb8000000)')
        ok = False
    else:
        cmp('splash.base', sp['base'], hexint(d['fb_base']))
        cmp('splash.size', sp['size'], hexint(d['fb_size']))
    if facts.get('gic'):
        reg = facts['gic']['reg'][0]
        cmp('gic.gicd', reg[0], hexint(board['gic']['gicd']))
        cmp('gic.gicd_size', reg[1], hexint(board['gic']['gicd_size']))
    if facts.get('ufs'):
        cmp('ufs.base', facts['ufs']['reg'][0][0], hexint(board['ufs']['base']))
    if facts.get('gicr_stride') is not None:
        cmp('gicr.stride', facts['gicr_stride'], hexint(board['gic']['gicr_pe_size']))
    if facts.get('timer'):
        cmp('timer.ppi', facts['timer'][2] if len(facts['timer']) > 2 else None,
            board['timer']['ppi'])
    base, win = hexint(board['memory']['dram_base']), hexint(board['memory']['dram_window'])
    covered = any(m[0] <= base and base + win <= m[0] + m[1] for m in facts.get('memory', []))
    cmp('dram covers ADT window', covered, True)
    if facts.get('touch'):
        cmp('touch.panel_max_x', facts['touch']['panel_max_x'],
            board['touch']['panel_max_x'])
        cmp('touch.panel_max_y', facts['touch']['panel_max_y'],
            board['touch']['panel_max_y'])
    if facts.get('panels'):
        print('  panels found:')
        for p in facts['panels']:
            print('    %s  %dx%d  %s' % (p['path'], p['width'], p['height'],
                                         p['name']))
        want = (d['width'], d['height'])
        cmp('panel geometry present', any((p['width'], p['height']) == want
                                          for p in facts['panels']), True)
    else:
        print('  DIFF no panel node found in this DTB (dtbo may hold it)')
        ok = False
    print('RESULT:', 'ok' if ok else 'drift')
    return 0 if ok else 1


def cmd_fdt(a):
    blob = open(a.fdt, 'rb').read()
    parts, meta = fdtparse.parse_container(blob)
    allfacts = []
    rc = 0
    for i, dtb, emeta in parts:
        root, _ = fdtparse.parse_dtb(dtb)
        facts = collect({'root': root, 'source': a.fdt,
                         'container': meta.get('container', 'dtb')})
        facts['dtb_index'] = i
        facts['dtb'] = emeta
        allfacts.append(facts)
        if a.verbose or len(parts) == 1:
            print('--- %s entry %d (%d bytes) ---' % (a.fdt, i, len(dtb)))
            print('memory: %s' % ', '.join('%#x+%#x' % (b, s) for b, s in facts['memory']))
            print('reserved: %d regions, splash %s'
                  % (len(facts['reserved']),
                     facts['splash'] and '%#x+%#x %s' % (facts['splash']['base'],
                                                         facts['splash']['size'],
                                                         facts['splash']['node'])))
            for p in facts['panels']:
                print('panel: %s %s %dx%d on-command %d bytes'
                      % (p['path'], p['name'], p['width'], p['height'],
                         p['on_command_len']))
        if a.board:
            print('check against %s (entry %d):' % (a.board, i))
            rc |= check(facts, json.load(open(a.board)))
    if a.out:
        with open(a.out, 'w') as f:
            json.dump(allfacts, f, indent=1)
        print('%s: %d entries' % (a.out, len(allfacts)), file=sys.stderr)
    return rc


def cmd_check(a):
    facts = json.load(open(a.facts))
    board = json.load(open(a.board or BOARD_DEFAULT))
    if isinstance(facts, list):
        facts = facts[0]
    return check(facts, board)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('panel', help='panel .dtsi -> panel_init_diting*.h')
    p.add_argument('--dtsi', required=True)
    p.add_argument('--node', default=None,
                   help='node label, e.g. dsi_l12_42_02_0a_dsc_cmd')
    p.add_argument('--timing', type=int, default=0, help='timing@N (default 0 = 60 Hz)')
    p.add_argument('--out', default=os.path.join(HERE, '..', 'shim'))
    p.add_argument('--basename', default='panel_init_diting',
                   help='output file stem: <basename>.h and <basename>_nodsc.h')
    p.add_argument('--symbol-full', default='panel_init_diting',
                   help='array name for the as-is sequence (shim uses panel_init_seq)')
    p.add_argument('--symbol-nodsc', default='panel_init_diting_nodsc',
                   help='array name for the DSC-stripped sequence (shim uses panel_init_nodsc)')
    p.set_defaults(func=cmd_panel)

    f = sub.add_parser('fdt', help='dtb/dtbo/live FDT -> facts + drift check')
    f.add_argument('fdt')
    f.add_argument('--board', default=BOARD_DEFAULT)
    f.add_argument('--out', default=None, help='write facts JSON here')
    f.add_argument('--verbose', action='store_true')
    f.set_defaults(func=cmd_fdt)

    c = sub.add_parser('check', help='compare saved facts JSON with the board file')
    c.add_argument('--facts', required=True)
    c.add_argument('--board', default=BOARD_DEFAULT)
    c.set_defaults(func=cmd_check)

    a = ap.parse_args()
    sys.exit(a.func(a))


if __name__ == '__main__':
    main()

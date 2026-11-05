#!/usr/bin/env python3
"""Read Flattened Device Trees: a raw .dtb / live FDT, or an Android dtbo.img.

Android's dtbo partition is a container: a dt_table_header (magic 0xd7b7ab1e,
all fields big-endian) followed by N dt_table_entry records, each pointing at
a complete embedded DTB. Both forms are handled here; nothing is written.

  fdtparse.py FILE                 list the container / tree summary
  fdtparse.py FILE --tree          print every node path and property name
  fdtparse.py FILE --node /a/b     dump one node (strings and hex)
  fdtparse.py FILE --prop REGEX    dump matching properties across the tree
  fdtparse.py FILE --json OUT      machine-readable dump (for diting-extract)

Used by tools/diting-extract.py; kept dependency-free so it also runs on the
Linux build box.
"""
import json
import re
import struct
import sys

FDT_MAGIC = 0xD00DFEED
DTBO_MAGIC = 0xD7B7AB1E

FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9


class Node:
    __slots__ = ('name', 'props', 'children', 'parent')

    def __init__(self, name, parent=None):
        self.name = name
        self.props = {}
        self.children = []
        self.parent = parent

    def path(self):
        bits = []
        n = self
        while n is not None and n.name != '':
            bits.append(n.name)
            n = n.parent
        return '/' + '/'.join(reversed(bits))

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def find(self, path):
        if path in ('', '/'):
            return self
        n = self
        for part in path.strip('/').split('/'):
            for c in n.children:
                if c.name == part or c.name.split('@')[0] == part:
                    n = c
                    break
            else:
                return None
        return n


def _u32(b, o):
    return struct.unpack_from('>I', b, o)[0]


def parse_dtb(blob):
    """Parse one DTB starting at blob[0]; returns (root, totalsize)."""
    if len(blob) < 40 or _u32(blob, 0) != FDT_MAGIC:
        raise ValueError('not an FDT (magic %#x)' % (len(blob) >= 4 and _u32(blob, 0) or 0))
    total = _u32(blob, 4)
    off_struct = _u32(blob, 8)
    off_strings = _u32(blob, 12)
    size_strings = _u32(blob, 32)

    strings = blob[off_strings:off_strings + size_strings]
    root = Node('', None)
    cur = root
    o = off_struct
    end = min(total, len(blob))
    while o + 4 <= end:
        tag = _u32(blob, o)
        o += 4
        if tag == FDT_BEGIN_NODE:
            e = blob.index(b'\0', o)
            name = blob[o:e].decode('ascii', 'replace')
            o = (e + 4) & ~3
            n = Node(name, cur)
            cur.children.append(n)
            cur = n
        elif tag == FDT_END_NODE:
            cur = cur.parent or root
        elif tag == FDT_PROP:
            ln, nameoff = struct.unpack_from('>II', blob, o)
            o += 8
            e = strings.index(b'\0', nameoff)
            pname = strings[nameoff:e].decode('ascii', 'replace')
            cur.props[pname] = blob[o:o + ln]
            o = (o + ln + 3) & ~3
        elif tag == FDT_NOP:
            pass
        elif tag == FDT_END:
            break
        else:
            raise ValueError('bad FDT tag %d at %#x' % (tag, o - 4))
    return root, total


def parse_container(blob):
    """Return [(index, dtb_bytes, meta)] for a raw DTB or an Android dtbo.img."""
    if len(blob) >= 4 and _u32(blob, 0) == DTBO_MAGIC:
        (magic, total_size, header_size, entry_size, entry_count,
         entries_offset, page_size, version) = struct.unpack_from('>8I', blob, 0)
        out = []
        for i in range(entry_count):
            o = entries_offset + i * entry_size
            dt_size, dt_off, did, rev = struct.unpack_from('>4I', blob, o)
            custom = blob[o + 16:o + entry_size]
            out.append((i, blob[dt_off:dt_off + dt_size],
                        {'id': did, 'rev': rev, 'offset': dt_off,
                         'size': dt_size, 'custom': custom.hex()}))
        if not out:
            raise SystemExit('dtbo container reports 0 entries')
        return out, {'container': 'dtbo', 'entries': entry_count,
                     'page_size': page_size, 'version': version}
    return [(0, blob, {})], {'container': 'dtb'}


def prop_u32(node, name):
    b = node.props.get(name)
    if b is None:
        return None
    return list(struct.unpack('>%dI' % (len(b) // 4), b[:len(b) // 4 * 4]))


def prop_u64_pairs(node, name):
    v = prop_u32(node, name)
    if v is None:
        return None
    return [(v[i] << 32) | v[i + 1] for i in range(0, len(v) - 1, 2)]


def prop_str(node, name):
    b = node.props.get(name)
    if b is None:
        return None
    return b.split(b'\0')[0].decode('ascii', 'replace')


def prop_strlist(node, name):
    b = node.props.get(name)
    if b is None:
        return None
    return [s.decode('ascii', 'replace') for s in b.split(b'\0') if s]


def _fmt_prop(name, b):
    if b and all(0x20 <= c < 0x7f or c == 0 for c in b) and b.endswith(b'\0'):
        return '%s = "%s"' % (name, b.rstrip(b'\0').decode('ascii', 'replace'))
    if len(b) % 4 == 0 and len(b):
        v = struct.unpack('>%dI' % (len(b) // 4), b)
        if len(v) <= 8:
            return '%s = <%s>' % (name, ' '.join('0x%x' % x for x in v))
    return '%s = [%s]' % (name, ' '.join('%02x' % c for c in b[:32]) +
                          (' ...' if len(b) > 32 else ''))


def dump_node(node, out=sys.stdout):
    print('%s {' % (node.path() if node.name else '/'), file=out)
    for k, v in node.props.items():
        print('    ' + _fmt_prop(k, v), file=out)
    for c in node.children:
        dump_node(c, out)
    print('};', file=out)


def to_json(root, meta):
    def one(n):
        return {'name': n.name, 'path': n.path(),
                'props': {k: v.hex() for k, v in n.props.items()},
                'children': [one(c) for c in n.children]}
    return {'meta': meta, 'tree': one(root)}


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    blob = open(argv[1], 'rb').read()
    parts, meta = parse_container(blob)
    print('%s: %s, %d DTB%s' % (argv[1], meta['container'], len(parts),
                                's' if len(parts) != 1 else ''), file=sys.stderr)
    roots = []
    for i, dtb, emeta in parts:
        root, total = parse_dtb(dtb)
        roots.append(root)
        if len(parts) > 1:
            print('--- entry %d id=%#x rev=%#x size=%d ---'
                  % (i, emeta.get('id', 0), emeta.get('rev', 0), len(dtb)), file=sys.stderr)

    if '--tree' in argv:
        for r in roots:
            for n in r.walk():
                if n.name:
                    print(n.path())
    if '--node' in argv:
        path = argv[argv.index('--node') + 1]
        for r in roots:
            n = r.find(path)
            if n is not None:
                dump_node(n)
    if '--prop' in argv:
        rx = re.compile(argv[argv.index('--prop') + 1])
        for r in roots:
            for n in r.walk():
                for k, v in n.props.items():
                    if rx.search(k):
                        print('%-60s %s' % (n.path() + ':' + k, _fmt_prop(k, v)))
    if '--json' in argv:
        out = argv[argv.index('--json') + 1]
        with open(out, 'w') as f:
            json.dump([to_json(r, meta) for r in roots], f, indent=1)
        print('%s: %d bytes' % (out, len(json.dumps([to_json(r, meta) for r in roots]))),
              file=sys.stderr)
    if not any(a in argv for a in ('--tree', '--node', '--prop', '--json')):
        for r in roots:
            print('/')
            for c in r.children:
                print('  %s' % c.path())
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))

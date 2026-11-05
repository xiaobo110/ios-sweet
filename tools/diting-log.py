#!/usr/bin/env python3
"""Decode the SWEET persistent console log (kernel patch 0047).

  diting-log.py AREA.bin [OUT.txt]

AREA.bin is the last (1 + ring/4096) device blocks of the userdata partition,
as read by tools/diting-log-pull.sh: block 0 is the header ("SWEETLOG", u32
version, u32 ring size, u64 total bytes, u64 flush seq), the ring follows.
Byte n of the console stream is at ring offset n % size.

Same format as ios-sweet-k60/tools/sweet-log.py (patch 0047 is shared); this
copy exists so a log can be decoded without the K60 tree at hand.
"""
import struct, sys

data = open(sys.argv[1], 'rb').read()
if len(data) < 4096:
    sys.exit('area file shorter than one block: %d bytes' % len(data))
magic, version, size, total, seq = struct.unpack_from('<8sIIQQ', data, 0)
if magic != b'SWEETLOG':
    sys.exit('no SWEETLOG header (log never flushed, or wrong area)')
ring = data[4096:4096 + size]
if total <= size:
    text = ring[:total]
else:
    start = total % size
    text = ring[start:] + ring[:start]
out = text.decode('utf-8', 'replace').replace('\r', '')
print(f'# version {version}, ring {size}, total {total} bytes, flush #{seq}'
      f'{" (wrapped: oldest bytes lost)" if total > size else ""}', file=sys.stderr)
if len(sys.argv) > 2:
    open(sys.argv[2], 'w', encoding='utf-8', newline='\n').write(out)
else:
    sys.stdout.write(out)

#!/usr/bin/env python3
"""Write an Android boot image (header v3/v4) without AOSP's mkbootimg.

    tools/diting-mkbootimg.py --kernel out/Image --ramdisk out/ramdisk.bin \
        --dtb board/base.dtb --out out/shim-boot.img

    tools/diting-mkbootimg.py --selftest <boot.img>

Nothing in this workspace ships mkbootimg, and the K60 build only uses the
header-v4 subset: magic, sizes, os_version, 1536-byte cmdline, signature_size.
--selftest re-encodes the header of a real v4 image and compares all 1584 bytes,
so a packing mistake fails here instead of on the phone.
"""
import argparse
import os
import struct
import sys

PAGE = 4096
BOOT_MAGIC = b'ANDROID!'
HEADER_V3 = 1584          # magic..cmdline, magic..signature_size for v4
CMDLINE_LEN = 1536


def encode_os_version(s):
    a, b, c = (int(x) for x in s.split('.'))
    return (a << 14) | (b << 7) | c


def encode_patch_level(s):
    y, m = (int(x) for x in s.split('-'))
    return ((y - 2000) << 4) | m


def build_header(version, kernel_size, ramdisk_size, os_version,
                 cmdline=b'', signature_size=0, reserved=(0, 0, 0, 0)):
    if version not in (3, 4):
        raise SystemExit('only header v3/v4, got %r' % version)
    cmdline = cmdline.encode('ascii') if isinstance(cmdline, str) else cmdline
    if len(cmdline) >= CMDLINE_LEN:
        raise SystemExit('cmdline longer than %d bytes' % (CMDLINE_LEN - 1))
    h = bytearray()
    h += BOOT_MAGIC
    h += struct.pack('<3I', kernel_size, ramdisk_size, os_version)
    h += struct.pack('<I', HEADER_V3)
    h += struct.pack('<4I', *reserved)
    h += struct.pack('<I', version)
    h += cmdline.ljust(CMDLINE_LEN, b'\0')
    if version == 4:
        h += struct.pack('<I', signature_size)
    assert len(h) == HEADER_V3, len(h)
    return bytes(h)


def parsed_header(blob):
    if blob[:8] != BOOT_MAGIC:
        raise SystemExit('not a boot image: magic %r' % blob[:8])
    kernel_size, ramdisk_size, os_version = struct.unpack_from('<3I', blob, 8)
    header_size = struct.unpack_from('<I', blob, 20)[0]
    reserved = struct.unpack_from('<4I', blob, 24)
    version = struct.unpack_from('<I', blob, 40)[0]
    cmdline = blob[44:44 + CMDLINE_LEN].split(b'\0')[0]
    signature_size = struct.unpack_from('<I', blob, 1580)[0] if version >= 4 else 0
    return {
        'version': version, 'kernel_size': kernel_size,
        'ramdisk_size': ramdisk_size, 'os_version': os_version,
        'header_size': header_size, 'reserved': reserved,
        'cmdline': cmdline, 'signature_size': signature_size,
    }


def selftest(path):
    blob = open(path, 'rb').read()
    h = parsed_header(blob)
    if h['header_size'] != HEADER_V3:
        raise SystemExit('header_size %d != %d' % (h['header_size'], HEADER_V3))
    mine = build_header(h['version'], h['kernel_size'], h['ramdisk_size'],
                        h['os_version'], h['cmdline'],
                        h['signature_size'], h['reserved'])
    if mine != blob[:HEADER_V3]:
        for i in range(HEADER_V3):
            if mine[i] != blob[i]:
                raise SystemExit('header mismatch at byte %d: %02x != %02x'
                                 % (i, mine[i], blob[i]))
        raise SystemExit('header mismatch')
    # signature_size is only a claim: the vendor image below declares 4096 yet
    # still starts its kernel on the first page after the header. Find it.
    sig = h['signature_size']
    magic = [off for off in (PAGE, PAGE + PAGE, PAGE + sig)
             if off + 0x40 <= len(blob) and blob[off + 0x38:off + 0x3c] == b'ARM\x64']
    print('header v%d ok, %d bytes identical' % (h['version'], HEADER_V3))
    print('  kernel=%d ramdisk=%d signature=%d os_version=%#x'
          % (h['kernel_size'], h['ramdisk_size'], sig, h['os_version']))
    print('  ARM64 kernel magic at %s' % (', '.join('%#x' % o for o in magic) or 'nowhere'))
    if magic and magic[0] != PAGE:
        print('  note: kernel not at %#x although signature_size=%d'
              % (PAGE, sig))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--kernel')
    ap.add_argument('--ramdisk')
    ap.add_argument('--dtb')
    ap.add_argument('--out')
    ap.add_argument('--header-version', type=int, default=4)
    ap.add_argument('--os-version', default='17.0.0')
    ap.add_argument('--os-patch-level', default='2035-06')
    ap.add_argument('--cmdline', default='console=null')
    ap.add_argument('--selftest', metavar='BOOT_IMG')
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest(a.selftest)
    if not (a.kernel and a.ramdisk and a.out):
        ap.error('--kernel, --ramdisk and --out are required')

    kernel = open(a.kernel, 'rb').read()
    ramdisk = open(a.ramdisk, 'rb').read()
    dtb = open(a.dtb, 'rb').read() if a.dtb else b''

    os_version = (encode_os_version(a.os_version) << 11) | encode_patch_level(a.os_patch_level)
    header = build_header(a.header_version, len(kernel), len(ramdisk),
                          os_version, a.cmdline)

    with open(a.out, 'wb') as f:
        f.write(header.ljust(PAGE, b'\0'))
        f.write(kernel)
        f.write(b'\0' * ((-len(kernel)) % PAGE))
        f.write(ramdisk)
        f.write(b'\0' * ((-len(ramdisk)) % PAGE))
        if dtb:
            f.write(dtb)
            f.write(b'\0' * ((-len(dtb)) % PAGE))
        total = f.tell()

    print('%s: v%d header, kernel %d, ramdisk %d, dtb %d -> %d bytes (%#x)'
          % (a.out, a.header_version, len(kernel), len(ramdisk), len(dtb), total, total))
    print('  kernel@%#x ramdisk@%#x dtb@%#x'
          % (PAGE,
             PAGE + len(kernel) + ((-len(kernel)) % PAGE),
             PAGE + len(kernel) + ((-len(kernel)) % PAGE)
             + len(ramdisk) + ((-len(ramdisk)) % PAGE)))
    return 0


if __name__ == '__main__':
    sys.exit(main())

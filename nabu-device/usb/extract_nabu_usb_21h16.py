"""Derive a compile-reference contract from the pinned 21H16 kernelcache.

No original addresses are runtime bindings. The caller supplies the existing
NativePrelink parser; both parser and input identities are saved in the receipt.
No input is modified and no Apple bytes are copied into the generated header.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

INPUT_SHA256 = 'f7d1952c7a21633d212eccf288e77444fff0cc86976db5de292f6819c2ffa1b2'
VERSION = b'root:xnu-10063.142.1.700.5~1/RELEASE_ARM64_T8010'
FAMILY = 'com.apple.iokit.IOUSBDeviceFamily'
PURE_SLOTS = [175, 177, 179, 180, 181, 182, 183, 184, 185, 186, 187, 188, 189, 192, 193, 199, 202, 227]


def address_pair(pc, adrp, add, register=None):
    """Decode adjacent ADRP/ADD-immediate with matching registers, no shift."""
    if adrp & 0x9f000000 != 0x90000000 or add & 0xffc00000 != 0x91000000:
        return None
    rd = adrp & 31
    if rd == 31 or (add & 31) != rd or (add >> 5) & 31 != rd:
        return None
    if register is not None and rd != register:
        return None
    imm = ((adrp >> 5) & 0x7ffff) << 2 | ((adrp >> 29) & 3)
    if imm & 1 << 20:
        imm -= 1 << 21
    return (pc & ~4095) + (imm << 12) + ((add >> 10) & 4095)


def meta_sites(words, base, names):
    """Find className in x1, MOVZ w3, then an immediate constructor BL.

    This deliberately recognizes the exact bounded instruction form rather
    than claiming arbitrary ARM64 data-flow analysis. x3 may not be modified
    between the size MOVZ and BL, and all constructions must agree.
    """
    sites = []
    for i in range(len(words) - 6):
        name = address_pair(base + i * 4, words[i], words[i + 1], 1)
        if name not in names:
            continue
        # ADRP x2; LDR x2,[x2,#imm]; MOVZ w3; BL OSMetaClass.
        a, b, size_word, branch = words[i + 2:i + 6]
        if a & 0x9f00001f != 0x90000002 or b & 0xffc003ff != 0xf9400042:
            continue
        if size_word & 0xffe0001f != 0x52800003 or branch & 0xfc000000 != 0x94000000:
            continue
        size = (size_word >> 5) & 0xffff
        immediate = branch & 0x3ffffff
        if immediate & (1 << 25):
            immediate -= 1 << 26
        meta = address_pair(base + (i - 2) * 4, words[i - 2], words[i - 1], 0) if i >= 2 else None
        sites.append({'class_name_site': base + i * 4, 'size': size,
                      'constructor_target': base + (i + 5) * 4 + immediate * 4,
                      'metaclass': meta})
    return sites


class Image:
    def __init__(self, data, parser):
        self.data, self.parser = data, parser
        self.meta = parser.macho(data)

    def read(self, va, size):
        at = self.parser.backed_offset(self.meta['segments'], va, size, len(self.data))
        return self.data[at:at + size]

    def executable(self, va):
        return va % 4 == 0 and any(s['initprot'] & 4 and s['va'] <= va < s['va'] + s['filesize']
                                 for s in self.meta['segments'])

    def module(self):
        info = next(s for s in self.meta['segments'] if s['name'] == '__PRELINK_INFO')
        entries = self.parser.unserialize_xml(self.read(info['va'], info['filesize']))['_PrelinkInfoDictionary']
        entries = [e for e in entries if e['CFBundleIdentifier'] == FAMILY]
        if len(entries) != 1:
            raise ValueError('Expected one IOUSBDeviceFamily')
        entry = entries[0]
        base = entry['_PrelinkExecutableLoadAddr']
        offset = self.parser.backed_offset(self.meta['segments'], base, 32, len(self.data))
        return entry, self.parser.macho(self.data, offset)


def derive(image):
    entry, module = image.module()
    text = next(s for s in module['segments'] if s['name'] == '__TEXT')
    executable = next(s for s in module['segments'] if s['name'] == '__TEXT_EXEC')
    raw = image.read(text['va'], text['filesize'])
    name = b'IOUSBDeviceController\0'
    names, pos = [], 0
    while (pos := raw.find(name, pos)) >= 0:
        names.append(text['va'] + pos)
        pos += len(name)
    raw_exec = image.read(executable['va'], executable['filesize'])
    if len(raw_exec) % 4:
        raise ValueError('Unaligned executable extent')
    words = struct.unpack('<' + 'I' * (len(raw_exec) // 4), raw_exec)
    sites = meta_sites(words, executable['va'], names)
    if len(sites) != 3 or {s['size'] for s in sites} != {552}:
        raise ValueError('Expected three agreeing 552-byte class constructor sites')
    if len({s['constructor_target'] for s in sites}) != 1:
        raise ValueError('Class constructor targets disagree')
    metas = {s['metaclass'] for s in sites if s['metaclass'] is not None}
    if len(metas) != 1:
        raise ValueError('Metaclass address is ambiguous')
    metaclass = metas.pop()
    exports = image.parser.exports(image.data, image.meta)
    pure = [va for va, names_ in exports.items() if '___cxa_pure_virtual' in names_]
    if len(pure) != 1 or not image.executable(pure[0]):
        raise ValueError('Expected exported executable __cxa_pure_virtual')
    candidates = []
    for i in range(len(words) - 2):
        if words[i + 2] != 0xf9000008:  # STR x8,[x0]
            continue
        vptr = address_pair(executable['va'] + i * 4, words[i], words[i + 1], 8)
        if vptr is None:
            continue
        try:
            rows = struct.unpack('<237Q', image.read(vptr, 237 * 8))
            if not all(image.executable(v) for v in rows[:234]) or any(rows[234:]):
                continue
            getter_words = struct.unpack('<3I', image.read(rows[7], 12))
            getter_meta = address_pair(rows[7], *getter_words[:2], register=0)
            if getter_words[2] != 0xd65f03c0 or getter_meta != metaclass:
                continue
            pure_slots = [n for n, va in enumerate(rows[:234]) if va == pure[0]]
            if pure_slots != PURE_SLOTS:
                raise ValueError('Pure virtual slots differ from 21H16 contract')
            # Constructor body is one BL-super followed by the vptr store.
            start = i - 3
            if start < 0 or words[start:start + 2] != (0xa9bf7bfd, 0x910003fd):
                continue
            if words[start + 2] & 0xfc000000 != 0x94000000:
                continue
            if words[i + 3:i + 5] != (0xa8c17bfd, 0xd65f03c0):
                continue
            candidates.append({'vptr': vptr, 'constructor': executable['va'] + start * 4,
                               'constructor_vptr_store': executable['va'] + (i + 2) * 4,
                               'metaclass_getter': rows[7], 'metaclass': metaclass,
                               'slot_count': 234, 'pure_slots': pure_slots,
                               'slots': [{'slot': n, 'original_va': hex(va), 'pure': va == pure[0],
                                          'signature': 'unrecovered; not callable'}
                                         for n, va in enumerate(rows[:234])]})
        except (ValueError, struct.error):
            continue
    if len(candidates) != 1:
        raise ValueError(f'Expected unique class vptr, found {len(candidates)}')
    return {'schema_version': 1, 'family': FAMILY, 'class': 'IOUSBDeviceController',
            'class_size': 552, 'metaclass_constructor_sites': sites,
            'module_load': hex(entry['_PrelinkExecutableLoadAddr']), **candidates[0],
            'compile_reference_only': True, 'runtime_qualified': False, 'hardware_tested': False,
            'limitations': ['No virtual or helper signatures qualified.',
                            'No internal field access authorized by this contract.',
                            'No generated VA is a runtime binding.',
                            'AppleUSBDeviceMux closure, board USB identity, VBUS and exclusive DWC3 owner pending.']}


def qualify_identity(data):
    if hashlib.sha256(data).hexdigest() != INPUT_SHA256 or VERSION not in data:
        raise ValueError('Input is not the pinned original 21H16 ordinary ARM64 kernelcache')


def header(receipt):
    """Emit only scalar observations. Apple addresses/bytes stay in private JSON."""
    return ('/* Generated compile-only observations; no runtime bindings. */\n'
            '#pragma once\n#define NABU_USB_21H16_OBSERVED_CLASS_SIZE 552u\n'
            '#define NABU_USB_21H16_OBSERVED_SLOT_COUNT 234u\n'
            '#define NABU_USB_21H16_OBSERVED_PURE_COUNT 18u\n'
            f'#define NABU_USB_21H16_INPUT_SHA256 "{receipt["kernelcache_sha256"]}"\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernelcache', type=Path, required=True)
    parser.add_argument('--native-prelink', type=Path, required=True, help='existing native_prelink.py')
    parser.add_argument('--output', type=Path, required=True, help='new private evidence directory')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error('output must be a new directory')
    data = args.kernelcache.read_bytes()
    qualify_identity(data)
    spec = importlib.util.spec_from_file_location('_nabu_usb_21h16_prelink', args.native_prelink)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    receipt = derive(Image(data, module))
    receipt.update({'kernelcache_path': str(args.kernelcache.resolve()), 'kernelcache_bytes': len(data),
                    'kernelcache_sha256': hashlib.sha256(data).hexdigest(),
                    'parser_path': str(args.native_prelink.resolve()),
                    'parser_sha256': hashlib.sha256(args.native_prelink.read_bytes()).hexdigest(),
                    'extractor_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    output.mkdir(parents=True)
    (output / 'controller-abi.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    (output / 'NabuUsb21H16Observed.h').write_text(header(receipt), encoding='utf-8')
    print(json.dumps({k: receipt[k] for k in ['class_size', 'slot_count', 'pure_slots', 'kernelcache_sha256',
                                             'compile_reference_only', 'runtime_qualified']}))


if __name__ == '__main__':
    main()

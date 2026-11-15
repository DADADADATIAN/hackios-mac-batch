"""Bind explicitly selected peripheral source units to objects and final code."""
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import struct

from nabu_idle_ci import _image, _function, require
from nabu_backlight_ci import archive_member, bl_calls, compile_gate, unique

BASE = 'outputs/tree/build/armdt-platform/peripherals/'
ARCHIVE = 'outputs/tree/build/armdt-platform/libArmDTPlatform.a'
XNU = 'outputs/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/'
WORK = 'outputs/tree/.cache/xnu-work/'
BUILDER = 'scripts/nabu_peripheral_build.py'
SOURCES = {
    'cpufreq': ('platform/NabuCpuFreq.cpp', 'platform/NabuCpuFreq.h',
                'scripts/nabu_cpufreq_source.py', 'gpu/a640/NabuPerfConfig.h'),
    'sensor_clock': tuple('sensors/clock/' + name for name in
                          ('NabuSensorClock.cpp', 'NabuSensorClock.h', 'NabuSscClockCore.h',
                           'LICENSE', 'prepare_sensor_clock.py')),
    'usb_abi_reference': ('usb/NativeUsb21H16Reference.cpp', 'usb/NabuUsb21H16Contract.h',
                          'usb/extract_nabu_usb_21h16.py'),
}
GATES = {'cpufreq': ('NABU_CPUFREQ', 'XNU_NABU_CPUFREQ'),
         'sensor_clock': ('NABU_SENSOR_CLOCK', 'NABU_SENSOR_CLOCK_CORE'),
         'usb_abi_reference': ('NABU_USB_ABI_REFERENCE', 'NABU_USB_21H16_COMPILE_REFERENCE')}
OBJECTS = {'cpufreq': 'NabuCpuFreq.o', 'sensor_clock': 'NabuSensorClock.o',
           'usb_abi_reference': 'NativeUsb21H16Reference.o'}
PREFIXES = {'cpufreq': ('_nabu_cpufreq_',), 'sensor_clock': ('_nabu_sensor_clock_',),
            'usb_abi_reference': ('_nabu_usb_21h16_',)}
USB_INPUT = 'f7d1952c7a21633d212eccf288e77444fff0cc86976db5de292f6819c2ffa1b2'
APPLE_CC1_OPTIONS = [
    '-clang-vendor-feature=+disableNonDependentMemberExprInCurrentInstantiation',
    '-fno-odr-hash-protocols', '-clang-vendor-feature=+enableAggressiveVLAFolding',
    '-clang-vendor-feature=+revert09abecef7bbf', '-clang-vendor-feature=+thisNoAlignAttr',
    '-clang-vendor-feature=+thisNoNullAttr',
    '-clang-vendor-feature=+disableAtImportPrivateFrameworkInImplementationError',
    '-Wno-error=allocator-wrappers',
]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def record_sources(read, report, intent):
    expected = {BUILDER}
    for key in SOURCES:
        if intent[key]:
            expected.update(SOURCES[key])
    require(set(intent['sources']) == expected, 'Peripheral source intent is incomplete or unexpected')
    data = {}
    for path, digest in intent['sources'].items():
        value = PurePosixPath(path)
        require(not value.is_absolute() and '..' not in value.parts and '\\' not in path,
                'Peripheral source path is not relative')
        data[path] = read(BASE + 'source/' + path)
        require(sha(data[path]) == digest, 'Peripheral staged source differs from intent: ' + path)
    require(report.get('builder_sha256') == sha(data[BUILDER]), 'Peripheral builder source drift')
    for key in SOURCES:
        unit = report['units'][key]
        require(unit.get('enabled') is intent[key], 'Peripheral unit gate differs from request: ' + key)
        if not intent[key]:
            continue
        require(set(unit['sources']) == set(SOURCES[key]), 'Peripheral unit source list mismatch: ' + key)
        for path in SOURCES[key]:
            item = unit['sources'][path]
            require(item.get('sha256') == sha(data[path]) and
                    item.get('lf_sha256') == sha(data[path].replace(b'\r\n', b'\n')) and
                    item.get('bytes') == len(data[path]), 'Peripheral unit source receipt mismatch: ' + path)
    return data


def unit_identity(data, key):
    if key == 'sensor_clock':
        contract = {'sources': {Path(path).name: sha(data[path].replace(b'\r\n', b'\n'))
                                for path in SOURCES[key]},
                    'enabled': 1, 'version': 1, 'hardware_tested': False}
        return sha(json.dumps(contract, sort_keys=True, separators=(',', ':')).encode())
    digest = hashlib.sha256()
    for path in sorted(SOURCES[key]):
        digest.update(path.encode() + b'\0' + data[path].replace(b'\r\n', b'\n'))
    return digest.hexdigest()


def constant(image, name, size):
    unique(image, name)
    rows = [row for row in image['symbols'] if row[0] == name and row[2] & 0xe == 0xe and not row[2] & 0xe0]
    require(rows[0][2] & 1, 'Peripheral marker is not an external definition: ' + name)
    address = image['names'][name]
    sections = [s for s in image['sections'] if s[0] <= address and address + size <= s[0] + s[1]]
    require(len(sections) == 1, 'Peripheral constant extent is missing or ambiguous: ' + name)
    section = sections[0]
    require(not section[5] & 0x80000000 and (section[5] & 255) not in (1, 12, 18),
            'Peripheral constant is executable or zero-fill: ' + name)
    offset = section[2] + address - section[0]
    require(offset + size <= len(image['content']), 'Peripheral constant is unbacked: ' + name)
    # Another definition cannot subdivide the expected marker extent.
    require(not any(not kind & 0xe0 and kind & 0xe == 0xe and address < va < address + size
                    for _, va, kind in image['symbols']), 'Peripheral marker is truncated: ' + name)
    return image['content'][offset:offset + size]


def marker_pair(obj, final, name, value):
    for image in (obj, final):
        require(constant(image, name, len(value)) == value, 'Peripheral marker mismatch: ' + name)


def check_integer_code(image, *, object_all=False):
    for section in image['sections']:
        if not section[5] & 0x80000000 or not object_all:
            continue
        require(section[2] + section[1] <= len(image['content']) and section[1] % 4 == 0,
                'Peripheral instruction section extent')
        reject_simd(struct.unpack('<%dI' % (section[1] // 4), image['content'][section[2]:section[2] + section[1]]))
    if not object_all:
        for name in image['names']:
            if name.startswith('_nabu_sensor_clock_') and name not in (
                    '_nabu_sensor_clock_build_identity', '_nabu_sensor_clock_build_policy'):
                reject_simd(_function(image, name)[1])


def reject_simd(words):
    for word in words:
        require((word & 0x1e000000) not in (0x0e000000, 0x1e000000) and
                not ((word & 0x0a000000) == 0x08000000 and word & 0x04000000),
                'Sensor clock contains floating-point/SIMD instructions')


def recorded_path(path):
    """Convert recorded runner paths to the preserved output tree."""
    require(isinstance(path, str), 'Peripheral record path is missing')
    value = path.replace('\\', '/')
    if value.startswith(BASE):
        suffix = value[len(BASE):]
    elif '/build/armdt-platform/peripherals/' in value:
        suffix = value.split('/build/armdt-platform/peripherals/', 1)[1]
    else:
        raise ValueError('Peripheral record path is outside its output directory')
    require(suffix and not PurePosixPath(suffix).is_absolute() and '..' not in PurePosixPath(suffix).parts,
            'Peripheral record path escapes its output directory')
    return BASE + suffix


def read_object(read, unit, key, archive):
    path = recorded_path(unit['object'])
    require(PurePosixPath(path).name == OBJECTS[key], 'Peripheral object filename mismatch: ' + key)
    obj = read(path)
    at, deployment = 32, []
    count = struct.unpack_from('<I', obj, 16)[0] if len(obj) >= 32 else 0
    for _ in range(count):
        require(at + 8 <= len(obj), 'Peripheral deployment command truncated')
        cmd, length = struct.unpack_from('<II', obj, at)
        require(length >= 8 and at + length <= len(obj), 'Peripheral deployment command bounds')
        if cmd == 0x32:
            require(length >= 24, 'Peripheral deployment command size')
            deployment.append(struct.unpack_from('<II', obj, at + 8))
        at += length
    require(deployment == [(2, 0x110000)], 'Peripheral object does not target iOS 17')
    require(sha(obj) == unit.get('object_sha256'), 'Peripheral object hash mismatch: ' + key)
    member = archive_member(archive, OBJECTS[key])
    padding = (-len(obj)) % 8
    require(member == obj or (padding and member == obj + b'\n' * padding),
            'Peripheral archive member differs from object: ' + key)
    command = unit['compile_command']
    require(isinstance(command, list) and command and all(isinstance(x, str) for x in command),
            'Peripheral compilation requires recorded argv')
    require('-mkernel' in command and not any(arg.startswith(('-Xpreprocessor', '-Xclang=')) for arg in command),
            'Peripheral command lacks kernel mode or forwards preprocessing')
    forwarded = []
    for index, arg in enumerate(command):
        if arg == '-Xclang':
            require(index + 1 < len(command), 'Peripheral cc1 forwarding is truncated')
            forwarded.append(command[index + 1])
    require(not forwarded or forwarded == APPLE_CC1_OPTIONS,
            'Peripheral cc1 forwarding differs from the pinned Apple compiler options')
    require(json.loads(read(recorded_path(unit['compile_command_path']))) == command,
            'Peripheral command record differs from report')
    compile_gate(command, GATES[key][1], True)
    source = SOURCES[key][0]
    require(sum(arg.replace('\\', '/').endswith('/source/' + source) for arg in command) == 1,
            'Peripheral command does not compile its staged source')
    require(command.count('-o') == 1 and command.index('-o') + 1 < len(command) and
            recorded_path(command[command.index('-o') + 1]) == path,
            'Peripheral command output differs from bound object')
    require('-c' in command, 'Peripheral command is not compilation')
    return _image(obj, 1), command


def sensor_generated(read, unit, data, identity):
    generated = unit['generated']
    header = read(recorded_path(generated['header']))
    inputs = read(recorded_path(generated['inputs']))
    require(sha(header) == generated.get('header_sha256') and sha(inputs) == generated.get('inputs_sha256'),
            'Sensor generated files changed')
    receipt = json.loads(inputs)
    contract = {'sources': {Path(p).name: sha(data[p].replace(b'\r\n', b'\n'))
                            for p in SOURCES['sensor_clock']},
                'enabled': 1, 'version': 1, 'hardware_tested': False, 'source_identity': identity}
    require(receipt == contract, 'Sensor generated source/mode contract differs')
    expected = ('#define NABU_SENSOR_CLOCK_SOURCE_ID "' + identity + '"\n'
                '#define NABU_SENSOR_CLOCK_PREPARED_ENABLED 1\n').encode()
    require(header.replace(b'\r\n', b'\n') == expected, 'Sensor generated header contract differs')


def xnu_recipe(read, name, target, *, gate=False):
    content = read(XNU + name)
    recipe = json.loads(read(XNU + name + '.json').decode().strip().rstrip(','))
    args = recipe['arguments']
    require(recipe['file'].endswith('/.cache/xnu-work/' + target) and recipe['file'] in args and
            Path(recipe['output']).name == Path(name).name and '-c' in args and
            args.count('-o') == 1 and args.index('-o') + 1 < len(args) and
            args[args.index('-o') + 1] == recipe['output'],
            'Peripheral native XNU recipe target mismatch')
    if gate:
        compile_gate(args, 'XNU_NABU_CPUFREQ', True)
    return _image(content, 1)


def cpu_generator(data):
    path = Path(__file__).resolve().parents[1] / 'nabu-device/scripts/nabu_cpufreq_source.py'
    require(path.read_bytes().replace(b'\r\n', b'\n') == data['scripts/nabu_cpufreq_source.py'].replace(b'\r\n', b'\n'),
            'CPU native helper generator differs from qualifier')
    spec = importlib.util.spec_from_file_location('nabu_peripheral_cpu_source', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def cpu_native(read, obj, final, data):
    module = cpu_generator(data)
    actual = read(WORK + module.TARGET).decode()
    restored = module.source(actual, restore=True)
    require(module.source(restored, enabled=True) == actual, 'CPU native helper source is not the bound transform')
    startup = read(WORK + module.STARTUP_TARGET).decode()
    require(module.patch_startup(module.patch_startup(startup, restore=True), enabled=True) == startup,
            'CPU BSD startup source is not the bound transform')
    report = json.loads(read('outputs/tree/build/nabu-cpufreq/source-report.json'))
    require(report.get('enabled') is True and report.get('runtime_qualified') is False and
            report.get('generator_sha256') == module.identity() and
            report.get('after_sha256') == sha(actual.encode()) and
            report.get('bsd_after_sha256') == sha(startup.encode()), 'CPU native source report mismatch')
    processor = xnu_recipe(read, 'osfmk/DEVELOPMENT/processor.o', module.TARGET, gate=True)
    bsd = xnu_recipe(read, 'bsd/DEVELOPMENT/bsd_init.o', module.STARTUP_TARGET)
    marker_pair(processor, final, '_nabu_cpufreq_recount_identity',
                ('NABUCPUFREQ_RECOUNT_SHA256=' + module.identity() + '\0').encode())
    marker_pair(processor, final, '_nabu_cpufreq_recount_policy', struct.pack('<3I', 1, 1, 8))
    require(bl_calls(processor, '_nabu_cpufreq_sample_idle', '_recount_processor_usage') and
            bl_calls(final, '_nabu_cpufreq_sample_idle', '_recount_processor_usage'),
            'CPU helper does not call native recount')
    workers = [name for name in obj['names'] if name.startswith(('__ZL3run', '__ZL6worker',
                                                                '__ZL18nabuCpuFreqWorker', '__ZL14nabuCpuFreqRun'))]
    require(any(name in final['names'] and bl_calls(obj, name, '_nabu_cpufreq_sample_idle') and
                bl_calls(final, name, '_nabu_cpufreq_sample_idle') for name in workers),
            'CPU worker does not call native idle helper')
    require(bl_calls(bsd, '_bsd_init', '_nabu_cpufreq_start') and
            bl_calls(final, '_bsd_init', '_nabu_cpufreq_start'), 'BSD startup does not call CPU governor')


def qualify(read, candidate, intent):
    final = _image(candidate, 2)
    if intent is None:
        for prefixes in PREFIXES.values():
            require(not any(name.startswith(prefixes) for name in final['names']),
                    'Disabled peripheral unit appears in final kernel')
        return {'compiled': False, 'runtime_qualified': False, 'units': {}}
    require(isinstance(intent, dict) and all(type(intent.get(key)) is bool for key in SOURCES) and
            intent.get('runtime_qualified') is False and isinstance(intent.get('sources'), dict),
            'Peripheral intent must have explicit Boolean gates and sources')
    report = json.loads(read(BASE + 'build-report.json'))
    require(report.get('schema_version') == 1 and report.get('hardware_tested') is False and
            report.get('runtime_qualified') is False and set(report.get('units', {})) == set(SOURCES),
            'Peripheral build report schema/qualification boundary differs')
    require(report.get('gates') == {GATES[key][0]: str(int(intent[key])) for key in SOURCES},
            'Peripheral build gates differ from request')
    data = record_sources(read, report, intent)
    archive = read(ARCHIVE)
    results = {}
    for key in SOURCES:
        if not intent[key]:
            require(not any(name.startswith(PREFIXES[key]) for name in final['names']),
                    'Disabled peripheral has final definitions: ' + key)
            results[key] = {'compiled': False, 'runtime_qualified': False}
            continue
        unit = report['units'][key]
        identity = unit_identity(data, key)
        require(unit.get('source_identity') == identity, 'Peripheral source identity mismatch: ' + key)
        obj, command = read_object(read, unit, key, archive)
        if key == 'sensor_clock':
            sensor_generated(read, unit, data, identity)
            require('-mgeneral-regs-only' in command, 'Sensor compilation permits FP/SIMD')
            marker_pair(obj, final, '_nabu_sensor_clock_build_identity', (identity + '\0').encode())
            marker_pair(obj, final, '_nabu_sensor_clock_build_policy', struct.pack('<2Q', 1, 1))
            for operation in ('source_id', 'enabled', 'init', 'generation', 'observe', 'confirm_epoch',
                              'invalidate', 'convert', 'ticks_to_ns', 'ns_to_ticks'):
                name = '_nabu_sensor_clock_' + operation
                unique(obj, name); unique(final, name)
                _function(obj, name); _function(final, name)
            check_integer_code(obj, object_all=True); check_integer_code(final)
        elif key == 'cpufreq':
            require([arg for arg in command if arg.startswith('-DNABU_CPUFREQ_SOURCE_ID=')] ==
                    ['-DNABU_CPUFREQ_SOURCE_ID="' + identity + '"'], 'CPU source identity compile flag mismatch')
            marker_pair(obj, final, '_nabu_cpufreq_identity', ('NABUCPUFREQ_SOURCE_SHA256=' + identity + '\0').encode())
            marker_pair(obj, final, '_nabu_cpufreq_build_policy', struct.pack('<4I', 1, 1, 100, 8))
            cpu_native(read, obj, final, data)
        else:
            compile_gate(command, 'NABU_USB_NATIVE_21H16', False)
            require([arg for arg in command if arg.startswith('-DNABU_USB_21H16_SOURCE_ID=')] ==
                    ['-DNABU_USB_21H16_SOURCE_ID="' + identity + '"'], 'USB source identity compile flag mismatch')
            generated = unit['generated']
            header = read(recorded_path(generated['header']))
            inputs = read(recorded_path(generated['inputs']))
            receipt = json.loads(inputs)
            require(sha(header) == generated.get('header_sha256') and sha(inputs) == generated.get('inputs_sha256'),
                    'USB generated observations changed')
            require(unit.get('compile_reference_only') is True and unit.get('kernelcache_sha256') == USB_INPUT and
                    receipt.get('compile_reference_only') is True and receipt.get('runtime_qualified') is False and
                    receipt.get('hardware_tested') is False and receipt.get('kernelcache_sha256') == USB_INPUT and
                    receipt.get('class_size') == 552 and receipt.get('slot_count') == 234 and
                    receipt.get('pure_slots') == [175,177,179,180,181,182,183,184,185,186,187,188,189,192,193,199,202,227] and
                    receipt.get('extractor_sha256') == sha(data['usb/extract_nabu_usb_21h16.py']),
                    'USB original input and scalar observation contract differs')
            expected_header = ('/* Generated compile-only observations; no runtime bindings. */\n'
                '#pragma once\n#define NABU_USB_21H16_OBSERVED_CLASS_SIZE 552u\n'
                '#define NABU_USB_21H16_OBSERVED_SLOT_COUNT 234u\n'
                '#define NABU_USB_21H16_OBSERVED_PURE_COUNT 18u\n'
                '#define NABU_USB_21H16_INPUT_SHA256 "' + USB_INPUT + '"\n').encode()
            require(header.replace(b'\r\n', b'\n') == expected_header, 'USB generated header differs from scalar receipt')
            marker_pair(obj, final, '_nabu_usb_21h16_reference_policy', struct.pack('<5I', 1, 0, 552, 234, 18))
            marker_pair(obj, final, '_nabu_usb_21h16_reference_input', (USB_INPUT + '\0').encode())
            marker_pair(obj, final, '_nabu_usb_21h16_reference_source', (identity + '\0').encode())
            for image in (obj, final):
                require(not any(name.startswith('_nabu_usb_21h16_') and name not in
                                ('_nabu_usb_21h16_reference_policy', '_nabu_usb_21h16_reference_input',
                                 '_nabu_usb_21h16_reference_source') for name in image['names']),
                        'USB compile reference contains runtime entrypoints')
        results[key] = {'compiled': True, 'source_identity': identity, 'runtime_qualified': False}
        if key == 'usb_abi_reference':
            results[key]['compile_reference_only'] = True
    return {'compiled': True, 'units': results, 'candidate_sha256': sha(candidate),
            'runtime_qualified': False, 'hardware_tested': False}


def qualify_candidate(upstream, device, candidate, prepared):
    tree = Path(upstream) / 'out/nabu'
    return qualify(lambda name: (tree / name.removeprefix('outputs/')).read_bytes(),
                   Path(candidate).read_bytes(), prepared.get('peripherals'))

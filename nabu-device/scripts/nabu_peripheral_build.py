"""Compile independent nabu peripheral units with the actual captured XNU recipe."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import subprocess

GATES = {'cpufreq': 'NABU_CPUFREQ', 'sensor_clock': 'NABU_SENSOR_CLOCK',
         'usb_abi_reference': 'NABU_USB_ABI_REFERENCE'}
SOURCES = {
    'cpufreq': ('platform/NabuCpuFreq.cpp', 'platform/NabuCpuFreq.h',
                'scripts/nabu_cpufreq_source.py', 'gpu/a640/NabuPerfConfig.h'),
    'sensor_clock': tuple('sensors/clock/' + name for name in
                          ('NabuSensorClock.cpp', 'NabuSensorClock.h', 'NabuSscClockCore.h',
                           'LICENSE', 'prepare_sensor_clock.py')),
    'usb_abi_reference': ('usb/NativeUsb21H16Reference.cpp', 'usb/NabuUsb21H16Contract.h',
                          'usb/extract_nabu_usb_21h16.py'),
}
BUILDER = 'scripts/nabu_peripheral_build.py'

def source_paths(device, *, cpufreq=False, sensor_clock=False, usb_abi_reference=False):
    enabled = {'cpufreq': cpufreq, 'sensor_clock': sensor_clock,
               'usb_abi_reference': usb_abi_reference}
    if any(type(value) is not bool for value in enabled.values()):
        raise ValueError('Peripheral source flags must be boolean')
    names = {BUILDER}
    for unit, value in enabled.items():
        if value:
            names.update(SOURCES[unit])
    return [(Path(device).resolve() / name) for name in sorted(names)]

def source_identity(files):
    digest = hashlib.sha256()
    for name, data in sorted(files.items()):
        digest.update(name.encode('utf-8') + b'\0' + data.replace(b'\r\n', b'\n'))
    return digest.hexdigest()

def _sha(data):
    return hashlib.sha256(data).hexdigest()

def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def _object(data):
    if len(data) < 32 or struct.unpack_from('<4I', data) != (0xfeedfacf, 0x100000c, 0, 1):
        raise ValueError('Peripheral object must be ordinary ARM64 relocatable Mach-O')
    count, size = struct.unpack_from('<II', data, 16)
    if size > len(data) - 32:
        raise ValueError('Peripheral object load commands exceed file')
    at, deployment = 32, None
    for _ in range(count):
        if at + 8 > 32 + size:
            raise ValueError('Truncated peripheral object load command')
        cmd, length = struct.unpack_from('<II', data, at)
        if length < 8 or length % 8 or at + length > 32 + size:
            raise ValueError('Invalid peripheral object load command')
        if cmd == 0x32:
            if length < 24 or deployment is not None:
                raise ValueError('Invalid peripheral deployment command')
            deployment = struct.unpack_from('<II', data, at + 8)
        at += length
    if at != 32 + size or deployment != (2, 0x110000):
        raise ValueError('Peripheral object lacks exact iOS 17 deployment')

def build(root, device, record, out, compile_command):
    root, device, out = Path(root).resolve(), Path(device).resolve(), Path(out).resolve()
    gates = {name: os.environ.get(name, '0') for name in GATES.values()}
    if any(value not in ('0', '1') for value in gates.values()):
        raise ValueError('Peripheral environment gates must be zero or one')
    enabled = {unit: gates[gate] == '1' for unit, gate in GATES.items()}
    report = {'schema_version': 1, 'gates': gates, 'hardware_tested': False,
              'runtime_qualified': False, 'units': {unit: {'enabled': value}
                                                  for unit, value in enabled.items()}}
    if not any(enabled.values()):
        return [], report
    if os.environ.get('NABU_CAPTURE_LOG') != '1':
        raise ValueError('Enabled peripherals require NABU_CAPTURE_LOG=1')
    files = {}
    for path in source_paths(device, **enabled):
        files[path.relative_to(device).as_posix()] = path.read_bytes()
    out.mkdir(parents=True, exist_ok=False)
    stage = out / 'source'
    for name, data in files.items():
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    report.update({'builder': str(stage / BUILDER), 'builder_source': BUILDER,
                   'builder_sha256': _sha(files[BUILDER]),
                   'builder_lf_sha256': _sha(files[BUILDER].replace(b'\r\n', b'\n'))})
    objects = []
    def compile_unit(unit, filename, flags):
        source = stage / filename
        name = source.stem
        obj = out / (name + '.o')
        command = compile_command(record, source, obj)
        command += ['-I', str(root / '.cache/xnu-work/osfmk'), '-mgeneral-regs-only', *flags]
        command_path = out / (name + '-compile-command.json')
        log_path = out / (name + '-compile.log')
        command_path.write_text(json.dumps(command, indent=2) + '\n', encoding='utf-8')
        result = subprocess.run(command, cwd=record['directory'], capture_output=True)
        log_path.write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f'{name} compilation failed: ' + result.stderr.decode('utf-8', errors='replace')[-4000:])
        data = obj.read_bytes()
        _object(data)
        report['units'][unit].update({'object': str(obj), 'object_sha256': _sha(data),
                                     'compile_command': command, 'compile_command_path': str(command_path),
                                     'compile_log': str(log_path)})
        objects.append(obj)
    for unit, value in enabled.items():
        if not value:
            continue
        selected = {name: files[name] for name in SOURCES[unit]}
        report['units'][unit].update({'build_gate': GATES[unit] + '=1',
            'sources': {name: {'sha256': _sha(data), 'lf_sha256': _sha(data.replace(b'\r\n', b'\n')),
                              'bytes': len(data), 'staged_path': str(stage / name)}
                        for name, data in sorted(selected.items())}})
        if unit == 'cpufreq':
            identity = source_identity(selected)
            report['units'][unit]['source_identity'] = identity
            compile_unit(unit, 'platform/NabuCpuFreq.cpp', ['-DXNU_NABU_CPUFREQ=1',
                         '-DNABU_CPUFREQ_SOURCE_ID="' + identity + '"',
                         '-I', str(stage / 'platform'), '-I', str(stage / 'gpu/a640')])
        elif unit == 'sensor_clock':
            prepare = _load(stage / 'sensors/clock/prepare_sensor_clock.py', '_nabu_sensor_clock_prepare')
            generated = out / 'sensor-generated'
            identity = prepare.prepare(stage / 'sensors/clock', generated, 1)
            header, inputs = generated / 'NabuSensorClockBuild.h', generated / 'build-inputs.json'
            report['units'][unit].update({'source_identity': identity,
                'generated': {'header': str(header), 'header_sha256': _sha(header.read_bytes()),
                              'inputs': str(inputs), 'inputs_sha256': _sha(inputs.read_bytes())}})
            compile_unit(unit, 'sensors/clock/NabuSensorClock.cpp', ['-DNABU_SENSOR_CLOCK_CORE=1',
                         '-I', str(stage / 'sensors/clock'), '-I', str(generated)])
        else:
            extractor = _load(stage / 'usb/extract_nabu_usb_21h16.py', '_nabu_usb_reference_extract')
            parser_path = next((p / 'native_prelink.py' for p in
                               (root / 'kits/NativePrelink', root / 'drivers/NativePrelink')
                               if (p / 'native_prelink.py').is_file()), None)
            if parser_path is None:
                raise ValueError('The assembled NativePrelink parser is missing')
            sys.path.insert(0, str(parser_path.parent))
            parser = _load(parser_path, '_nabu_usb_reference_parser')
            kernel = Path(os.environ['UNIXNU_KERNELCACHE']).resolve()
            data = kernel.read_bytes()
            extractor.qualify_identity(data)
            receipt = extractor.derive(extractor.Image(data, parser))
            receipt.update({'kernelcache_sha256': _sha(data), 'kernelcache_path': str(kernel),
                            'parser_path': str(parser_path), 'parser_sha256': _sha(parser_path.read_bytes()),
                            'extractor_sha256': _sha(files['usb/extract_nabu_usb_21h16.py'])})
            generated = out / 'usb-generated'
            generated.mkdir()
            inputs = generated / 'controller-abi.json'
            inputs.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
            header = generated / 'NabuUsb21H16Observed.h'
            header.write_text(extractor.header(receipt), encoding='utf-8')
            identity = source_identity(selected)
            report['units'][unit].update({'source_identity': identity, 'compile_reference_only': True,
                'kernelcache_sha256': receipt['kernelcache_sha256'],
                'generated': {'header': str(header), 'header_sha256': _sha(header.read_bytes()),
                              'inputs': str(inputs), 'inputs_sha256': _sha(inputs.read_bytes())}})
            compile_unit(unit, 'usb/NativeUsb21H16Reference.cpp', ['-DNABU_USB_21H16_COMPILE_REFERENCE=1',
                         '-DNABU_USB_NATIVE_21H16=0', '-DNABU_USB_21H16_SOURCE_ID="' + identity + '"',
                         '-I', str(stage / 'usb'), '-I', str(generated)])
    for name, data in files.items():
        if (device / name).read_bytes() != data or (stage / name).read_bytes() != data:
            raise ValueError('Peripheral source changed while compiling: ' + name)
    (out / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return objects, report

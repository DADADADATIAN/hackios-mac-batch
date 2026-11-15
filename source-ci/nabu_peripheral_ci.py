"""Wire explicitly selected peripheral port units into the full XNU build."""
import hashlib
import importlib.util
from pathlib import Path
import shutil


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Peripheral build anchor missing or ambiguous: ' + before)
    return text.replace(before, after, 1)


def wire_cpufreq(text):
    if 'scripts/nabu_cpufreq_source.py' in text or 'processor.o_CFLAGS_ADD' in text:
        raise ValueError('CPU frequency build already wired or processor flags conflict')
    # Run before any existing inverse transforms; apply after capture has
    # inserted its hooks. This preserves the other transformations on rebuild.
    anchor = 'if [ "${NABU_CAPTURE_LOG:-0}" = 1 ]; then\n  python3 scripts/nabu_capture_source.py --root "$ROOT" --device "$UNIXNU_DEVICE" --restore-data-progress\nfi\n'
    text = replace_once(text, anchor,
        'python3 scripts/nabu_cpufreq_source.py --root "$ROOT" --restore\n' + anchor)
    anchor = '  python3 scripts/nabu_capture_source.py --root "$ROOT" --device "$UNIXNU_DEVICE"\nfi\n'
    text = replace_once(text, anchor, anchor +
        'python3 scripts/nabu_cpufreq_source.py --root "$ROOT" --enable\n')
    anchor = 'EXTRA_MAKE_ARGS=()\n'
    return replace_once(text, anchor, anchor +
        'EXTRA_MAKE_ARGS+=("processor.o_CFLAGS_ADD=-DXNU_NABU_CPUFREQ=1")\n'
        'rm -f "$ROOT/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/processor.o"\n'
        'rm -f "$ROOT/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/bsd_init.o"\n')


def prepare(upstream, device, *, cpufreq=False, sensor_clock=False, usb_abi_reference=False):
    flags = dict(cpufreq=cpufreq, sensor_clock=sensor_clock, usb_abi_reference=usb_abi_reference)
    if any(type(value) is not bool for value in flags.values()):
        raise ValueError('Peripheral intent must use Boolean flags')
    if not any(flags.values()):
        return None
    upstream, device = Path(upstream), Path(device)
    spec = importlib.util.spec_from_file_location('nabu_bound_peripheral_build', device / 'scripts/nabu_peripheral_build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sources = {p.relative_to(device).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in module.source_paths(device, **flags)}
    kernel = upstream / 'scripts/unixnu/kernel.py'
    text = kernel.read_text(encoding='utf-8')
    settings = ''.join(f"    env['{name}'] = '{int(flags[key])}'\n" for key, name in
                       [('cpufreq', 'NABU_CPUFREQ'), ('sensor_clock', 'NABU_SENSOR_CLOCK'),
                        ('usb_abi_reference', 'NABU_USB_ABI_REFERENCE')])
    if cpufreq:
        build = upstream / 'scripts/build-xnu-4k-baseline.sh'
        build.write_text(wire_cpufreq(build.read_text(encoding='utf-8')), encoding='utf-8')
        shutil.copyfile(device / 'scripts/nabu_cpufreq_source.py', upstream / 'scripts/nabu_cpufreq_source.py')
    kernel.write_text(replace_once(text, '    env = dict(os.environ)\n',
                                  '    env = dict(os.environ)\n' + settings), encoding='utf-8')
    return {**flags, 'sources': sources, 'runtime_qualified': False}

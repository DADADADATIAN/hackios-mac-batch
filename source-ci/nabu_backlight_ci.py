"""Bind probe and optional Legacy PM calls to sources, archives and final code."""
import hashlib
import json
from pathlib import Path

START = '_nabu_backlight_start'
POWER = '_nabu_backlight_set_power'
HOOK = '_nabu_backlight_power_transition'
PM = '__ZN19NativeLegacyDisplay13setPowerStateEmP9IOService'
UFS = 'outputs/tree/build/native-ufs/'
DISPLAY = 'outputs/tree/build/native-legacy-display/'


def enable_backlight(upstream, device, power=False):
    if type(power) is not bool:
        raise ValueError('Backlight power intent must be Boolean')
    kernel = Path(upstream) / 'scripts/unixnu/kernel.py'
    text = kernel.read_text(encoding='utf-8')
    marker = '    env = dict(os.environ)\n'
    if text.count(marker) != 1:
        raise ValueError('Expected the pinned kernel build environment')
    settings = "    env['NABU_BACKLIGHT'] = '1'\n" + f"    env['NABU_BACKLIGHT_POWER'] = '{int(power)}'\n"
    kernel.write_text(text.replace(marker, marker + settings, 1), encoding='utf-8')
    sources = sorted((Path(device) / 'platform').glob('NabuBacklight*'))
    return dict(build_gate='NABU_BACKLIGHT=1', boot_arg='nabu-backlight=1',
                power_build_gate='NABU_BACKLIGHT_POWER=1', power_boot_arg='nabu-backlight-power=1',
                default='off', runtime_qualified=False, power_hook_compiled=power,
                power_hook_wired=False,
                display_source_sha256=(hashlib.sha256((Path(device) / 'platform/NabuLegacyCpuPresent.cpp').read_bytes()).hexdigest()
                                       if power else None),
                sources={path.relative_to(device).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in sources})


def compile_gate(command, macro, enabled):
    from nabu_idle_ci import require
    # Bound recipes use explicit argv. Do not accept opaque preprocessor
    # forwarding or expand a response file outside the recorded command.
    require(not any(arg.startswith(('@', '-Wp,')) for arg in command),
            'Backlight compile command contains unsupported response/forwarding arguments')
    values, index = [], 0
    while index < len(command):
        arg = command[index]
        index += 1
        if arg in ('-D', '-U'):
            require(index < len(command) and command[index] and not command[index].startswith('-'),
                    'Backlight compile option lacks a value: ' + arg)
            option, value = arg, command[index]
            index += 1
        elif arg.startswith(('-D', '-U')):
            option, value = arg[:2], arg[2:]
        else:
            continue
        if value.split('=', 1)[0] == macro:
            values.append((option, value))
    require(values == [('-D', f'{macro}={int(enabled)}')], 'Backlight compile gate mismatch: ' + macro)


def unique(image, name):
    from nabu_idle_ci import require
    definitions = [row for row in image['symbols'] if row[0] == name and not row[2] & 0xe0 and row[2] & 0xe == 0xe]
    require(len(definitions) == 1, 'Backlight definition is missing or duplicated: ' + name)


def bl_calls(image, caller, callee):
    from nabu_idle_ci import _branches
    start, words, branches = _branches(image, caller)
    result = []
    for pc, name in branches:
        if words[(pc - start) // 4] & 0xfc000000 != 0x94000000:
            continue
        # A compiler's ltmp alias at the callee's address must not hide a real BL.
        if name == callee or (name in image['names'] and callee in image['names'] and
                              image['names'][name] == image['names'][callee]):
            result.append(hex(pc))
    return result


def archive_member(archive, wanted):
    """Read one ar member, including BSD and GNU extended names, without extraction."""
    from nabu_idle_ci import require
    require(archive.startswith(b'!<arch>\n'), 'Backlight archive is not ordinary ar')
    cursor, longnames, matches = 8, b'', []
    while cursor < len(archive):
        require(cursor + 60 <= len(archive), 'Backlight ar header is truncated')
        header = archive[cursor:cursor + 60]
        require(header[58:60] == b'`\n', 'Backlight ar header trailer')
        try:
            size = int(header[48:58])
        except ValueError as error:
            raise ValueError('Backlight ar member size') from error
        require(size >= 0 and cursor + 60 + size <= len(archive), 'Backlight ar member bounds')
        name = header[:16].decode('ascii').strip()
        data = archive[cursor + 60:cursor + 60 + size]
        if name.startswith('#1/'):
            length = int(name[3:])
            require(0 < length <= len(data), 'Backlight BSD ar name bounds')
            name, data = data[:length].rstrip(b'\0').decode(), data[length:]
        elif name == '//':
            longnames = data
        elif name.startswith('/') and name[1:].isdigit():
            offset = int(name[1:])
            end = longnames.find(b'/\n', offset)
            require(0 <= offset < len(longnames) and end > offset, 'Backlight GNU ar name bounds')
            name = longnames[offset:end].decode()
        else:
            name = name.removesuffix('/')
        if name == wanted:
            matches.append(data)
        cursor += 60 + size + (size & 1)
    require(cursor == len(archive) and len(matches) == 1, 'Backlight ar member is missing or duplicated: ' + wanted)
    return matches[0]


def qualify_display_power(read, image, enabled, expected_source=None, expected_header=None):
    from nabu_idle_ci import _image, require
    from nabu_display_ci import HARDWARE_CPU_PROFILES
    report = json.loads(read(DISPLAY + 'build-report.json'))
    require(report.get('backlight_power_hook_compiled', False) is enabled,
            'Backlight display build intent mismatch')
    if not enabled:
        require(HOOK not in image['names'], 'Disabled backlight power hook is defined in final kernel')
        return dict(compiled=False, runtime_qualified=False)
    require(report.get('cpu_present') is True and report.get('profile') in HARDWARE_CPU_PROFILES,
            'Backlight hook requires hardware CPU display archive')
    for name, expected in (('NativeLegacyDisplay.cpp', expected_source), ('NabuBacklightPower.h', expected_header)):
        data = read(DISPLAY + 'source/' + name)
        digest = hashlib.sha256(data).hexdigest()
        require(expected is not None and digest == expected ==
                report['source_sha256'].get('build/native-legacy-display/source/' + name),
                'Backlight display source/header mismatch: ' + name)
    command = json.loads(read(DISPLAY + 'NativeLegacyDisplay.command.json'))
    compile_gate(command, 'XNU_NABU_BACKLIGHT', True)
    compile_gate(command, 'XNU_NABU_BACKLIGHT_POWER', True)
    require('-DNABU_LEGACY_CPU_PRESENT=1' in command and '-DNABU_LEGACY_CPU_ABI_VERIFIED=1' in command and
            any(arg.endswith('/source/NativeLegacyDisplay.cpp') for arg in command),
            'Backlight display command lacks qualified CPU source')
    obj = read(DISPLAY + 'NativeLegacyDisplay.late.o')
    require(hashlib.sha256(obj).hexdigest() == report['objects'].get('NativeLegacyDisplay.late.o'),
            'Backlight display object binding mismatch')
    archive = read(DISPLAY + 'libNativeLegacyDisplay.late.a')
    require(hashlib.sha256(archive).hexdigest() == report.get('archive_sha256') and
            archive_member(archive, 'NativeLegacyDisplay.late.o') == obj,
            'Backlight display archive does not contain bound object')
    object_image = _image(obj, 1)
    require(HOOK not in object_image['names'] and POWER not in object_image['names'],
            'Display object must import the single UFS power implementation')
    unique(object_image, PM)
    unique(image, PM)
    unique(image, HOOK)
    calls = bl_calls(image, PM, HOOK)
    require(bl_calls(object_image, PM, HOOK) and calls, 'Legacy PM does not BL to the bound backlight hook')
    return dict(compiled=True, pm_call_sites=calls, original_ack_preserved_host_contract=True,
                asynchronous_parent_ack='skip', runtime_qualified=False)


def qualify(read, candidate, intent):
    from nabu_idle_ci import _image, require
    whole_report = json.loads(read(UFS + 'build-report.json'))
    report = whole_report.get('backlight', {})
    image = _image(candidate, 2)
    if intent is None:
        require(not report.get('enabled', False) and START not in image['names'] and HOOK not in image['names'],
                'Backlight probe differs from the disabled build intent')
        return dict(compiled=False, runtime_qualified=False, power_hook_wired=False)
    power = intent.get('power_hook_compiled', False)
    require(type(power) is bool and report.get('power_hook_compiled', False) is power and
            report.get('enabled') is True and report.get('default') == 'off' and
            report.get('power_hook') == ('legacy_sync_ack' if power else 'not_wired') and
            intent.get('runtime_qualified') is False, 'Missing explicit compiled backlight contract')
    unique(image, START)
    required_sources = {'platform/NabuBacklight.cpp', 'platform/NabuBacklight.h',
                        'platform/NabuBacklightResources.h', 'platform/NabuBacklightPower.h'}
    require(required_sources <= intent['sources'].keys(), 'Backlight intent lacks bound source/resource headers')
    for path, expected in intent['sources'].items():
        actual = hashlib.sha256(read(UFS + 'source/' + path)).hexdigest()
        require(actual == expected == report['sources'].get(path), 'Backlight compiled source differs from intent')
    metadata = report['objects'].get('NabuBacklight.cpp', {})
    obj = read(UFS + 'NabuBacklight.o')
    require(set(report['objects']) == {'NabuBacklight.cpp'} and
            hashlib.sha256(obj).hexdigest() == metadata.get('object_sha256') and
            metadata.get('source_sha256') == report['sources']['platform/NabuBacklight.cpp'],
            'Backlight object/source binding mismatch')
    object_image = _image(obj, 1)
    unique(object_image, START)
    unique(object_image, POWER)
    command = json.loads(read(UFS + 'NabuBacklight-compile-command.json'))
    compile_gate(command, 'XNU_NABU_BACKLIGHT', True)
    compile_gate(command, 'XNU_NABU_BACKLIGHT_POWER', power)
    require(any(arg.endswith('/source/platform/NabuBacklight.cpp') for arg in command),
            'Backlight command does not compile the enabled bound source')
    require(bl_calls(image, '__ZN9NativeUfs9startRootEP9IOService', START),
            'UFS entry does not start the backlight probe')
    hook_result = dict(compiled=False, runtime_qualified=False)
    if power:
        unique(object_image, HOOK)
        unique(image, POWER)
        unique(image, HOOK)
        archive = read(UFS + 'libNativeUfs.a')
        require(hashlib.sha256(archive).hexdigest() == whole_report.get('archive_sha256') and
                archive_member(archive, 'NabuBacklight.o') == obj,
                'Backlight UFS archive does not contain bound object')
        require(bl_calls(object_image, HOOK, POWER) and bl_calls(image, HOOK, POWER),
                'Backlight hook does not BL to the power primitive')
        hook_result = qualify_display_power(read, image, True, intent.get('display_source_sha256'),
                                           intent['sources']['platform/NabuBacklightPower.h'])
    else:
        require(HOOK not in object_image['names'] and HOOK not in image['names'],
                'Probe-only build contains a power hook')
    return dict(compiled=True, sources=report['sources'], objects=report['objects'],
                final_definition=hex(image['names'][START]),
                candidate_sha256=hashlib.sha256(candidate).hexdigest(),
                runtime_qualified=False, power_hook_wired=power, power_hook=hook_result)


def qualify_candidate(upstream, device, candidate, prepared):
    tree = Path(upstream) / 'out/nabu'
    return qualify(lambda name: (tree / name.removeprefix('outputs/')).read_bytes(),
                   Path(candidate).read_bytes(), prepared.get('backlight'))

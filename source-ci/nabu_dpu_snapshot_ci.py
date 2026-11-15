"""Bind the optional read-only DPU snapshot diagnostic to its compiled A640 entry."""
import hashlib
import json
from pathlib import Path


def enable_dpu_snapshot(upstream, device):
    kernel = Path(upstream) / 'scripts/unixnu/kernel.py'
    text = kernel.read_text(encoding='utf-8')
    marker = '    env = dict(os.environ)\n'
    if text.count(marker) != 1:
        raise ValueError('Expected the pinned kernel build environment')
    kernel.write_text(text.replace(marker, marker + "    env['NABU_DPU_SNAPSHOT'] = '1'\n", 1),
                      encoding='utf-8')
    names = ('platform/NabuDpuSnapshot.h', 'platform/NabuDpuSnapshot.cpp')
    return dict(build_gate='NABU_DPU_SNAPSHOT=1', boot_arg='nabu-dpu-snapshot=1',
                depth_arg='nabu-dpu-smmu-depth', default='off', smmu_depth_max=0,
                read_only=True, runtime_qualified=False,
                sources={name: hashlib.sha256((Path(device) / name).read_bytes()).hexdigest()
                         for name in names})


def qualify(read, candidate, intent):
    from nabu_idle_ci import _branches, _image, require
    base = 'outputs/tree/build/native-adreno/'
    block = json.loads(read(base + 'build-report.json')).get('dpu_snapshot') or {}
    image = _image(candidate, 2)
    name = '_nabu_dpu_snapshot_once'
    if intent is None:
        require(not block.get('enabled', False) and name not in image['names'],
                'DPU snapshot differs from the disabled build intent')
        return dict(compiled=False, runtime_qualified=False)
    require(block.get('enabled') is True and block.get('default') == 'off' and
            block.get('boot_arg') == 'nabu-dpu-snapshot=1' and
            block.get('smmu_depth_max') == 0 and block.get('read_only') is True and
            block.get('runtime_qualified') is False and
            intent.get('runtime_qualified') is False and name in image['names'],
            'Missing explicit compiled DPU snapshot contract')
    required_sources = {'platform/NabuDpuSnapshot.cpp', 'platform/NabuDpuSnapshot.h'}
    require(required_sources <= intent['sources'].keys(), 'DPU snapshot intent lacks bound sources')
    for path, expected in intent['sources'].items():
        staged = 'source/' + Path(path).name
        actual = hashlib.sha256(read(base + staged)).hexdigest()
        require(actual == expected == block['sources'].get(path),
                'DPU snapshot staged source differs from intent')
    metadata = block.get('objects', {}).get('NabuDpuSnapshot', {})
    obj = read(base + 'NabuDpuSnapshot.o')
    require(set(block.get('objects', {})) == {'NabuDpuSnapshot'} and
            hashlib.sha256(obj).hexdigest() == metadata.get('object_sha256') and
            metadata.get('source_sha256') == block['sources']['platform/NabuDpuSnapshot.cpp'],
            'DPU snapshot object/source binding mismatch')
    _image(obj, 1)
    command = json.loads(read(base + 'NabuDpuSnapshot-compile-command.json'))
    require('-DXNU_NABU_DPU_SNAPSHOT=1' in command and
            any(value.endswith('/source/NabuDpuSnapshot.cpp') for value in command),
            'DPU snapshot command does not compile the enabled bound source')
    context_obj = _image(read(base + 'NativeAdrenoGpuContext.o'), 1)
    object_callers = [label for label in context_obj['names']
                      if 'GpuContext' in label and 'bringUp' in label]
    require(object_callers, 'GPU bring-up entry is missing from its archive object')
    object_calls = [target for caller in object_callers
                    for _, target in _branches(context_obj, caller)[2] if target == name]
    require(object_calls, 'GPU context object does not call the DPU snapshot entry')
    callers = [label for label in image['names']
               if 'GpuContext' in label and 'bringUp' in label]
    require(callers, 'GPU bring-up entry is missing from the final kernel')
    calls = [target for caller in callers
             for _, target in _branches(image, caller)[2] if target == name]
    require(calls, 'GPU bring-up does not call the DPU snapshot entry')
    return dict(compiled=True, sources=block['sources'], objects=block['objects'],
                final_definition=hex(image['names'][name]),
                candidate_sha256=hashlib.sha256(candidate).hexdigest(),
                runtime_qualified=False)


def qualify_candidate(upstream, device, candidate, prepared):
    tree = Path(upstream) / 'out/nabu'
    return qualify(lambda name: (tree / name.removeprefix('outputs/')).read_bytes(),
                   Path(candidate).read_bytes(), prepared.get('dpu_snapshot'))

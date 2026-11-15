"""Bind nabu CPU-load sampling to the fixed 10063 native recount implementation."""
import argparse
import hashlib
import json
from pathlib import Path

TARGET = 'osfmk/kern/processor.c'
PINNED_PROCESSOR_SHA256 = '4a7ee2f14535c834adf27795d36ddfa5eb99198527aeb8e9c04cd0d0dc5ecb20'
STARTUP_TARGET = 'bsd/kern/bsd_init.c'
STARTUP_ANCHOR = '\tbsd_autoconf();\n'
STARTUP_MARKER = '\t/* NABU_CPUFREQ_START_V1 */\n'
STARTUP = STARTUP_MARKER + '''\t{
\t\textern void nabu_cpufreq_start(void);
\t\tnabu_cpufreq_start();
\t}
\t/* NABU_CPUFREQ_START_END_V1 */
'''
MACRO = 'XNU_NABU_CPUFREQ'
MARKER = '/* NABU_CPUFREQ_RECOUNT_V1 */\n'
ANCHOR = 'void\nprocessor_cpu_load_info(processor_t processor,\n'
CONTRACT = {'version': 1, 'macro': MACRO, 'default': 0, 'max_cpus': 8,
            'symbol': '_nabu_cpufreq_sample_idle', 'units': 'mach_absolute_time ticks',
            'logical_cpu': 'processor_array index', 'runtime_qualified': False,
            'pinned_processor_sha256': PINNED_PROCESSOR_SHA256}
HELPER = r'''/* NABU_CPUFREQ_RECOUNT_V1 */
#ifndef XNU_NABU_CPUFREQ
#define XNU_NABU_CPUFREQ 0
#endif
#if XNU_NABU_CPUFREQ != 0 && XNU_NABU_CPUFREQ != 1
#error XNU_NABU_CPUFREQ_must_be_zero_or_one
#endif
#if XNU_NABU_CPUFREQ
const char nabu_cpufreq_recount_identity[] __attribute__((used)) = "NABUCPUFREQ_RECOUNT_SHA256=__SOURCE_ID__";
const uint32_t nabu_cpufreq_recount_policy[3] __attribute__((used)) = {1, 1, 8};
int nabu_cpufreq_sample_idle(uint64_t *values, unsigned capacity, unsigned *count)
{
    if (!count) return 1;
    *count = 0;
    const unsigned number = __atomic_load_n(&processor_count, __ATOMIC_ACQUIRE);
    if (!values || !number || number > 8 || capacity < number || capacity > 8) return 1;
    uint64_t sampled[8];
    for (unsigned cpu = 0; cpu < number; ++cpu) {
        processor_t p = processor_array[cpu];
        if (p == PROCESSOR_NULL || p->cpu_id != (int)cpu) return 2;
        const int before = __atomic_load_n(&p->state, __ATOMIC_ACQUIRE);
        if (before < PROCESSOR_IDLE || before > PROCESSOR_RUNNING) return 2;
        struct recount_usage usage = {0};
        recount_processor_usage(&p->pr_recount, &usage, &sampled[cpu]);
        const int after = __atomic_load_n(&p->state, __ATOMIC_ACQUIRE);
        if (after < PROCESSOR_IDLE || after > PROCESSOR_RUNNING) return 2;
    }
    if (__atomic_load_n(&processor_count, __ATOMIC_ACQUIRE) != number) return 2;
    for (unsigned cpu = 0; cpu < number; ++cpu) values[cpu] = sampled[cpu];
    *count = number;
    return 0;
}
#endif
'''

def identity():
    return hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest()

def source(text, *, enabled=False, restore=False):
    if type(enabled) is not bool or type(restore) is not bool:
        raise ValueError('CPU frequency flags must be boolean')
    if MARKER in text:
        if text.count(MARKER) != 1:
            raise ValueError('Duplicate CPU frequency helper')
        old = text[text.index(MARKER):]
        if old != HELPER.replace('__SOURCE_ID__', identity()):
            raise ValueError('CPU frequency helper source changed')
        text = text[:text.index(MARKER)]
    if hashlib.sha256(text.encode()).hexdigest() != PINNED_PROCESSOR_SHA256:
        raise ValueError('Pinned processor source SHA256 changed')
    if text.count(ANCHOR) != 1 or 'recount_processor_usage(&processor->pr_recount, &usage, &idle_time);' not in text:
        raise ValueError('Pinned processor recount anchor changed')
    if restore or not enabled:
        return text
    if not text.endswith('\n'):
        raise ValueError('Processor source final newline changed')
    return text + HELPER.replace('__SOURCE_ID__', identity())

def patch_startup(text, *, enabled=True, restore=False):
    if type(enabled) is not bool or type(restore) is not bool:
        raise ValueError('CPU frequency flags must be boolean')
    if 'NABU_CPUFREQ_START' in text:
        if text.count(STARTUP) != 1 or text.count('NABU_CPUFREQ_START') != 2:
            raise ValueError('CPU frequency startup patch changed')
        text = text.replace(STARTUP, '', 1)
    if text.count(STARTUP_ANCHOR) != 1 or 'nabu_cpufreq_start' in text:
        raise ValueError('Pinned BSD startup anchor changed')
    if restore or not enabled:
        return text
    return text.replace(STARTUP_ANCHOR, STARTUP_ANCHOR + STARTUP, 1)

def apply(root, *, enabled=False, restore=False):
    root = Path(root)
    target = root / '.cache/xnu-work' / TARGET
    before = target.read_text(encoding='utf-8')
    after = source(before, enabled=enabled, restore=restore)
    startup = root / '.cache/xnu-work' / STARTUP_TARGET
    bsd_before = startup.read_text(encoding='utf-8')
    bsd_after = patch_startup(bsd_before, enabled=enabled, restore=restore)
    if before != after:
        target.write_text(after, encoding='utf-8')
    if bsd_before != bsd_after:
        startup.write_text(bsd_after, encoding='utf-8')
    metadata = {'enabled': enabled and not restore, 'contract': CONTRACT,
                'generator_sha256': identity(), 'before_sha256': hashlib.sha256(before.encode()).hexdigest(),
                'after_sha256': hashlib.sha256(after.encode()).hexdigest(),
                'bsd_before_sha256': hashlib.sha256(bsd_before.encode()).hexdigest(),
                'bsd_after_sha256': hashlib.sha256(bsd_after.encode()).hexdigest(),
                'runtime_qualified': False}
    if not restore:
        out=root/'build/nabu-cpufreq';out.mkdir(parents=True,exist_ok=True)
        (out/'source-report.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
    return metadata

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    group=p.add_mutually_exclusive_group()
    group.add_argument('--enable',action='store_true');group.add_argument('--restore',action='store_true')
    a=p.parse_args();print(json.dumps(apply(a.root,enabled=a.enable,restore=a.restore)))

"""Generate a source/compile-mode identity for the integer sensor clock unit."""
from pathlib import Path
import argparse
import hashlib
import json

NAMES = ('NabuSensorClock.h', 'NabuSensorClock.cpp', 'NabuSscClockCore.h', 'LICENSE', 'prepare_sensor_clock.py')

def prepare(source, output, enabled):
    source, output = Path(source).resolve(), Path(output).resolve()
    if enabled not in (0, 1):
        raise ValueError('Sensor clock build value must be zero or one')
    records = {name: hashlib.sha256((source/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for name in NAMES}
    contract = {'sources': records, 'enabled': enabled, 'version': 1, 'hardware_tested': False}
    identity = hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(',',':')).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=False)
    (output/'NabuSensorClockBuild.h').write_text(
        '#define NABU_SENSOR_CLOCK_SOURCE_ID "'+identity+'"\n'
        '#define NABU_SENSOR_CLOCK_PREPARED_ENABLED '+str(enabled)+'\n',encoding='utf-8')
    (output/'build-inputs.json').write_text(json.dumps({**contract,'source_identity':identity},indent=2)+'\n',encoding='utf-8')
    return identity

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=Path(__file__).parent)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--enabled',type=int,choices=(0,1),default=0)
    a=p.parse_args()
    print(prepare(a.source,a.output,a.enabled))

"""Qualify captured nabu KTZ8866 backlight resources offline and generate the header.

The FDT decoder is reused from tools/nabu_wifi_board.py. This helper binds the
fixed vendor TLMM register description and the captured i2c@880000/ktz8866@11
binding. It emits a source candidate and an ADT digest requirement for the
optional, default-off bit-banged I2C self-test and the reserved power primitive.

Constants come only from the pinned vendor TLMM source and the captured FDT;
the donor's comments are not trusted (its tile base already failed once in
another module, and its ctl value is re-derived here from pull/drv field
positions in the same pinned vendor file the Hall module qualified).
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[4]
# Donor NabuBacklight.{h,cpp} as published in the reviewed source snapshot.
DONOR_COMMIT = 'cb45dcf71cd7381c1d39966e411c3ed69a512fbf'
TLMM_COMMIT = '71b6effd2dfa8f703dd1ea17acd1323b2fd3cf6c'
TLMM_SHA256 = '7c32df2250e26f6678f22e6b4f1e9dd791a6b4da796a3b326b7f0837192c92a4'
MSM_SHA256 = '9b65a7aeaa1b1299207afc06ff32704d2513c5296ae8edf6b5bd759050a5a614'
FDT_PROPERTY = 'nabu-backlight-source-fdt-sha256'

TILE_BASE = 0x03000000
SOUTH_OFFSET = 0x00D00000
REG_SIZE = 0x1000
SDA_PIN = 0
SCL_PIN = 1
I2C_ADDRESS = 0x11
ENABLE_REGISTER = 0x08
ENABLE_BIT = 0x40
# Vendor msm_pingroup field order for SM8150 GPIOs: pull bits[1:0], mux
# (FUNC_SEL) bits[5:2], drv bits[8:6], oe bit 9, in bit 0 of the io word, out
# bit 1. pinctrl-msm.c encodes pull as MSM_NO_PULL=0, MSM_PULL_DOWN=1,
# MSM_KEEPER=2 (alias MSM_PULL_UP_NO_KEEPER), MSM_PULL_UP=3.
#
# The captured FDT selects bias-disable (MSM_NO_PULL) in the i2c ACTIVE state
# and bias-pull-up in the SLEEP state. The donor drove ctl=3|oe, i.e.
# FUNC_SEL=0(gpio) + PULL_UP(3): correct function, but its pull matches the
# Android SLEEP state, not the ACTIVE state. An open-drain I2C bus with the
# board's external pull-ups needs no internal pull; this port deliberately
# emits PULL=0 (bias-disable) matching Android's active configuration, and
# records the donor discrepancy in the unit report.
PULL_FIELD = 0
CTL_RELEASE = (0 << 0) | (0 << 2) | (0 << 6) | (0 << 9)
CTL_DRIVE_LOW = CTL_RELEASE | (1 << 9)

UNRESOLVED = [
    'no in-tree XNU driver owns the GENI I2C0 engine at 0x880000 today; firmware '
    'residual qup0 mux ownership is assumed absent, not verified',
    'the bus-idle check runs after take()+release, so it detects an electrically '
    'held bus but cannot distinguish a previously idle bus from a just-released one',
    'the optional synchronous Legacy PM hook has no lock-screen/wake runtime '
    'qualification on 21H16; nonzero parent ACKs deliberately skip the hook',
]


POWER_PROFILES = ('nabu_ufs_cpu_present_defconfig', 'nabu_ufs_gpu_defconfig',
                  'nabu_ufs_gpu_metal_defconfig')


def power_build_enabled(env, profile):
    """Builders reject incomplete power requests before copying or compiling."""
    value = env.get('NABU_BACKLIGHT_POWER', '0')
    if value not in ('0', '1'):
        raise ValueError('NABU_BACKLIGHT_POWER must be 0 or 1')
    enabled = value == '1'
    if enabled and (env.get('NABU_BACKLIGHT') != '1' or env.get('NABU_CAPTURE_LOG') != '1' or
                    env.get('NABU_LEGACY_CPU_PRESENT') != '1' or profile not in POWER_PROFILES):
        raise ValueError('Backlight power requires probe, capture and a hardware CPU display profile')
    return enabled


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fdt_decoder():
    path = ROOT / 'tools/nabu_wifi_board.py'
    spec = importlib.util.spec_from_file_location('nabu_backlight_fdt_decoder', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_tlmm_source(source):
    text = source.decode('utf-8')
    for expression in (r'#define NORTH\s+0x00900000', r'#define SOUTH\s+0x00D00000',
                       r'#define REG_SIZE\s+0x1000',
                       r'\.ctl_reg = base \+ REG_SIZE \* id',
                       r'\.io_reg = base \+ 0x4 \+ REG_SIZE \* id',
                       r'\.mux_bit = 2', r'\.pull_bit = 0', r'\.drv_bit = 6',
                       r'\.oe_bit = 9', r'\.in_bit = 0', r'\.out_bit = 1',
                       r'\[0\]\s*=\s*PINGROUP\(0,\s*SOUTH,\s*qup0,',
                       r'\[1\]\s*=\s*PINGROUP\(1,\s*SOUTH,\s*qup0,'):
        require(re.search(expression, text), 'Backlight TLMM register description drift: ' + expression)


def check_msm_source(source):
    text = source.decode('utf-8')
    for expression in (r'#define MSM_NO_PULL\s+0', r'#define MSM_PULL_DOWN\s+1',
                       r'#define MSM_KEEPER\s+2', r'#define MSM_PULL_UP\s+3'):
        require(re.search(expression, text), 'Backlight MSM pull encoding drift: ' + expression)


def board_contract(data, tlmm_source, msm_source):
    require(hashlib.sha256(tlmm_source).hexdigest() == TLMM_SHA256,
            'Backlight TLMM source differs from the fixed nabu vendor input')
    require(hashlib.sha256(msm_source).hexdigest() == MSM_SHA256,
            'Backlight MSM pull-encoding source differs from the fixed vendor input')
    check_tlmm_source(tlmm_source)
    check_msm_source(msm_source)

    decoder = fdt_decoder()
    tree = decoder.Fdt(data)
    require(tree.nodes['/'].get('model') == b'NABU\0', 'Backlight input is not the captured NABU model')

    chips = [path for path in tree.compatible('ktz,ktz8866')
             if decoder.parent(path) == '/soc/i2c@880000']
    chip = tree.one(chips, 'KTZ8866 node on the i2c@880000 bus')
    require(tree.scalar(chip, 'reg') == I2C_ADDRESS, 'KTZ8866 is not at I2C address 0x11')
    props = tree.nodes[chip]
    require(props.get('status', b'okay\0') in (b'okay\0', b'ok\0'), 'KTZ8866 node is disabled')

    # The three hardware-enable GPIO tuples must point at the TLMM controller
    # and keep the captured pin numbers. They are recorded for the report only;
    # this unit never drives them (GPIO27 HWEN and GPIO114/115 LCD bias are
    # left exactly as firmware configured them).
    enables = {}
    for key, pin in (('ktz8866,hwen-gpio', 27), ('ktz8866,enp-gpio', 114), ('ktz8866,enn-gpio', 115)):
        gpio = decoder.cells(props.get(key, b''))
        require(len(gpio) == 3 and gpio[1] == pin and gpio[2] == 0,
                'KTZ8866 ' + key + ' tuple drifted from the captured enable pin')
        require(gpio[0] in tree.phandles, 'KTZ8866 ' + key + ' controller phandle is unresolved')
        require(tree.phandles[gpio[0]] == '/soc/pinctrl@03000000',
                'KTZ8866 ' + key + ' does not target the nabu TLMM controller')
        enables[key] = dict(controller=tree.phandles[gpio[0]], pin=pin, flags=gpio[2], driven=False)

    bus = tree.nodes['/soc/i2c@880000']
    require(bus.get('status', b'okay\0') in (b'okay\0', b'ok\0'), 'i2c@880000 bus is disabled')
    require(tree.scalar('/soc/i2c@880000', 'pinctrl-0') in tree.phandles,
            'i2c@880000 pinctrl-0 phandle is unresolved')
    active = tree.reference('/soc/i2c@880000', 'pinctrl-0')
    mux = active + '/mux'
    config = active + '/config'
    require('qupv3_se0_i2c' in active, 'i2c@880000 pinctrl-0 is not the captured SE0 I2C state')
    require(decoder.strings(tree.nodes[mux].get('pins', b'')) == ['gpio0', 'gpio1'] and
            decoder.strings(tree.nodes[mux].get('function', b'')) == ['qup0'],
            'i2c@880000 active mux does not bind gpio0/gpio1 to qup0')
    config_props = tree.nodes[config]
    require('bias-disable' in config_props and 'bias-pull-up' not in config_props and
            'bias-pull-down' not in config_props and
            decoder.strings(config_props.get('pins', b'')) == ['gpio0', 'gpio1'] and
            decoder.cells(config_props.get('drive-strength', b'')) == [2],
            'i2c@880000 active config is not the captured bias-disable state')

    tlmm_path = '/soc/pinctrl@03000000'
    tlmm = tree.nodes[tlmm_path]
    require('qcom,sm8150-pinctrl' in decoder.strings(tlmm.get('compatible', b'')) and
            'gpio-controller' in tlmm and tree.scalar(tlmm_path, '#gpio-cells') == 2,
            'Backlight controller is not the nabu TLMM GPIO domain')
    require(tlmm.get('status', b'okay\0') in (b'okay\0', b'ok\0'), 'Backlight TLMM is disabled')
    names = decoder.strings(tlmm.get('reg-names', b''))
    regs = tree.regs(tlmm_path)
    require(len(names) == len(regs) and names.count('pinctrl') == 1,
            'Backlight TLMM register names are missing or ambiguous')
    bank = regs[names.index('pinctrl')]
    tile_base = bank['base'] + SOUTH_OFFSET
    map_base = tile_base + SDA_PIN * REG_SIZE
    require(bank['base'] == TILE_BASE and
            map_base + 2 * REG_SIZE <= bank['base'] + bank['bytes'],
            'Backlight GPIO0/GPIO1 fall outside the qualified TLMM resource')

    return dict(
        schema_version=1, result='backlight_resources_qualified_host_only',
        source_fdt_sha256=tree.sha256, donor_commit=DONOR_COMMIT,
        tlmm_commit=TLMM_COMMIT, tlmm_source_sha256=TLMM_SHA256,
        msm_source_sha256=MSM_SHA256,
        chip_node=chip, bus_node='/soc/i2c@880000', i2c_address=I2C_ADDRESS,
        sda_pin=SDA_PIN, scl_pin=SCL_PIN, pinctrl_state=active,
        tile_base=tile_base, map_base=map_base, map_bytes=2 * REG_SIZE,
        ctl_release=CTL_RELEASE, ctl_drive_low=CTL_DRIVE_LOW,
        ctl_fields=dict(pull_bit=0, pull_value=PULL_FIELD, mux_bit=2, mux_value=0,
                        drv_bit=6, drv_value=0, oe_bit=9, in_bit=0, out_bit=1),
        pull_decision=('MSM_NO_PULL=0 matches the captured i2c ACTIVE state; '
                       'donor ctl=3 used MSM_PULL_UP=3 which matches Android SLEEP '
                       'instead; external board pull-ups make either work, but the '
                       'active-state encoding is deliberate here'),
        enable_register=ENABLE_REGISTER, enable_bit=ENABLE_BIT,
        enable_gpios=enables, tlmm_resource=bank,
        register_writes='only KTZ8866 reg 0x08 bit 6 through the reserved set_power primitive',
        self_test='read-only: take/release/idle-check/read reg 0x08/restore; never writes',
        adt_requirement=dict(path='/chosen', property=FDT_PROPERTY, bytes=32,
                             value_hex=tree.sha256),
        compiled_default=0, boot_argument='nabu-backlight=1',
        power_boot_argument='nabu-backlight-power=1',
        power_build_gate='XNU_NABU_BACKLIGHT_POWER=1', power_compiled_default=0,
        power_hook='optional synchronous Legacy PM hook; runtime unqualified',
        unresolved_assumptions=UNRESOLVED,
        hardware_tested=False, runtime_qualified=False,
    )


def resources_header(contract):
    require(contract.get('result') == 'backlight_resources_qualified_host_only' and
            contract.get('tile_base') == 0x03d00000 and contract.get('map_bytes') == 0x2000 and
            contract.get('i2c_address') == 0x11 and
            contract.get('ctl_release') == CTL_RELEASE and
            contract.get('ctl_drive_low') == CTL_DRIVE_LOW and
            re.fullmatch('[0-9a-f]{64}', contract.get('source_fdt_sha256', '')),
            'Backlight resource header requires a qualified contract')
    digest = bytes.fromhex(contract['source_fdt_sha256'])
    rows = ['    ' + ', '.join(f'0x{byte:02x}' for byte in digest[at:at + 8]) + ','
            for at in range(0, len(digest), 8)]
    return '\n'.join([
        '// SPDX-License-Identifier: BSD-2-Clause-Patent',
        '// Generated by nabu_backlight_source.py from a captured FDT and fixed vendor source.',
        '// Donor NabuBacklight source commit ' + DONOR_COMMIT,
        '// Vendor TLMM source commit ' + TLMM_COMMIT,
        '// Vendor TLMM SHA-256 ' + TLMM_SHA256,
        '// Vendor pinctrl-msm SHA-256 ' + MSM_SHA256,
        '// Do not add the HWEN (GPIO27) or ENP/ENN (GPIO114/115) enables here:',
        '// driving them can hard-kill the panel and its LCD bias rails.',
        '#pragma once', '#include <stdint.h>', '', 'namespace nabu_backlight {',
        'constexpr uint64_t GpioTileBase = 0x03d00000ULL;',
        'constexpr unsigned MapBytes = 0x2000;',
        'constexpr unsigned SdaPin = 0;', 'constexpr unsigned SclPin = 1;',
        'constexpr uint8_t I2cAddress = 0x11;',
        'constexpr uint8_t AddressWrite = I2cAddress << 1;',
        'constexpr uint8_t AddressRead = (I2cAddress << 1) | 1;',
        'constexpr uint8_t EnableRegister = 0x08;',
        'constexpr uint8_t EnableBitMask = 0x40;',
        '// SM8150 TLMM word layout: ctl = pin*0x1000, io = pin*0x1000 + 0x4.',
        '// ctl fields: pull[1:0] mux[5:2] drv[8:6] oe[9]; io bits: in[0] out[1].',
        '// pull=0 (bias-disable) matches the captured i2c ACTIVE state.',
        'constexpr uint32_t CtlRelease = 0x000;',
        'constexpr uint32_t CtlDriveLow = 0x200;',
        'constexpr uint32_t IoOutBit = 0x2;',
        'constexpr uint32_t IoInBit = 0x1;',
        'constexpr const char *FdtProperty = "nabu-backlight-source-fdt-sha256";',
        'constexpr uint8_t SourceFdtSha256[32] = {', *rows, '};',
        '} // namespace nabu_backlight', '',
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fdt', type=Path)
    parser.add_argument('--tlmm-source', type=Path)
    parser.add_argument('--msm-source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(args.fdt and args.tlmm_source and args.msm_source,
            'Backlight resources require FDT/TLMM/MSM inputs')
    contract = board_contract(args.fdt.read_bytes(), args.tlmm_source.read_bytes(),
                              args.msm_source.read_bytes())
    header = resources_header(contract)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'NabuBacklightResources.h').write_text(header, encoding='utf-8', newline='\n')
    (args.output / 'backlight-contract.json').write_text(json.dumps(contract, indent=2) + '\n',
                                                        encoding='utf-8')
    print(json.dumps(contract, indent=2))


if __name__ == '__main__':
    main()

"""Select the original racer LOD path for traffic, preserving its flags/clamps."""


def traffic_detail_lod(addr, code):
    if addr != 0x0074E160:
        return code
    racer = '    /* 0074E4E9: fild dword ptr [esp + 0x10] */'
    traffic = 'L_0074E51D:;'
    assert code.count(racer) == code.count(traffic) == 1, 'traffic LOD instruction changed'
    code = code.replace(racer, 'traffic_racer_lod:\n' + racer, 1)
    return code.replace(traffic, '    if (traffic_detail_on()) goto traffic_racer_lod;\n' + traffic, 1)

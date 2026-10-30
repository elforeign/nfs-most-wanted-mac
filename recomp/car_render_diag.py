"""Observe original Render locals and taken false edges, without replacing decisions."""


def car_render_diagnostics(addr, code):
    if addr != 0x0074E160:
        return code
    # Before each instruction, all its input registers/locals still hold the
    # original values. Match the decoded instruction and branch body exactly.
    observations = {
        0x0074E213: 'cmp ebx, dword ptr [edi + 0x24]',
        0x0074E34B: 'test eax, eax',
        0x0074E5A2: 'mov ecx, dword ptr [esp + 0x14]',
        0x0074E694: 'test eax, eax',
    }
    rejections = {
        0x0074E223: ('jne', '!zf'),       # pixel size (after view exemptions)
        0x0074E2D4: ('je', 'zf'),         # frame light context allocation
        0x0074E2DC: ('je', 'zf'),         # frame matrix allocations
        0x0074E2E8: ('je', 'zf'),
        0x0074E2F0: ('je', 'zf'),
        0x0074E34D: ('je', 'zf'),         # visibility
        0x0074E5B1: ('je', 'zf'),         # both selected LODs == -1
        0x0074E696: ('jne', '!zf'),       # camera inside bounding box
    }
    for site, instruction in observations.items():
        marker = f'    /* {site:08X}: {instruction} */'
        assert code.count(marker) == 1, f'car diagnostic instruction changed: {site:08X}'
        code = code.replace(marker, f'    if (diag_on) diag_car_observe(c, 0x{site:08X}u);\n' + marker, 1)
    for site, (jump, condition) in rejections.items():
        old = (f'    /* {site:08X}: {jump} 0x75052a */\n'
               f'    if ({condition}) goto L_0075052A;')
        assert code.count(old) == 1, f'car diagnostic rejection changed: {site:08X}'
        new = (f'    /* {site:08X}: {jump} 0x75052a */\n'
               f'    if ({condition}) {{ if (diag_on) diag_car_reject(0x{site:08X}u); goto L_0075052A; }}')
        code = code.replace(old, new, 1)
    return code

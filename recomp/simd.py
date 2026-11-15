"""Address-independent SSE emission for private images.

Copy bit patterns through local unions: no host alignment requirements, no
read beyond the guest operand, and sources are snapshotted before aliasing writes.
The default MW emitter does not select this module or advertise SSE through CPUID.
"""
from capstone import CS_OP_REG, CS_OP_MEM


def emit(rc, insn):
    m, ops = insn.mnemonic, insn.operands
    def xmm(op):
        return op.type == CS_OP_REG and insn.reg_name(op.reg).startswith('xmm')
    def address(op):
        if xmm(op):return f'c->xmm[{int(insn.reg_name(op.reg)[3:])}]'
        if op.type == CS_OP_MEM:return f'(G + {rc.ea(insn,op)})'
        raise ValueError('not an SSE vector operand')
    def load(op, var, n=16):return f'memcpy(&{var}, {address(op)}, {n}); '
    def store(op, var, n=16):return f'memcpy({address(op)}, &{var}, {n}); '
    def result(code):
        return ['{ union { float f[4]; uint32_t u[4]; int32_t i[4]; uint64_t q[2]; } a = {0}, b = {0}, r = {0}; '+code+' }']
    rounding_begin = ('int old_round = fegetround(); '
                      'int modes[] = {FE_TONEAREST,FE_DOWNWARD,FE_UPWARD,FE_TOWARDZERO}; '
                      'fesetround(modes[(c->mxcsr >> 13) & 3]); ')
    rounding_end = 'fesetround(old_round); '
    if m == 'bsr':
        v=rc.rd(insn,ops[1]);bits=ops[1].size*8
        return [f'{{ uint32_t v = {v}; zf = v == 0; if (v) {{ '+rc.wr(insn,ops[0],f'(uint32_t)({bits-1} - (__builtin_clz(v) - {32-bits}))')+' } }']
    moves={'movaps','movups','movdqa','movdqu','movntps'}
    if m in moves:return result(load(ops[1],'a')+store(ops[0],'a'))
    if m == 'movss':
        if ops[0].type == CS_OP_MEM:return result(load(ops[1],'a',4)+store(ops[0],'a',4))
        keep=load(ops[0],'r') if xmm(ops[1]) else ''
        return result(keep+load(ops[1],'a',4)+'r.u[0] = a.u[0]; '+store(ops[0],'r'))
    if m in ('movlps','movhps'):
        half=0 if m=='movlps' else 1
        if ops[0].type == CS_OP_MEM:
            return result(load(ops[1],'a')+f'r.q[0] = a.q[{half}]; '+store(ops[0],'r',8))
        return result(load(ops[0],'r')+load(ops[1],'a',8)+f'r.q[{half}] = a.q[0]; '+store(ops[0],'r'))
    if m in ('movlhps','movhlps'):
        d,s=(1,0) if m=='movlhps' else (0,1)
        return result(load(ops[0],'r')+load(ops[1],'a')+f'r.q[{d}] = a.q[{s}]; '+store(ops[0],'r'))
    arith={name+suffix:op for name,op in [('add','+'),('sub','-'),('mul','*'),('div','/')] for suffix in ('ss','ps')}
    if m in arith:
        lanes=1 if m.endswith('ss') else 4
        return result(load(ops[0],'a')+load(ops[1],'b',lanes*4)+'r = a; '+rounding_begin+
                      f'for (int k = 0; k < {lanes}; ++k) r.f[k] = a.f[k] {arith[m]} b.f[k]; '
                      +rounding_end+store(ops[0],'r'))
    logical={'andps':'a.u[k] & b.u[k]','andnps':'~a.u[k] & b.u[k]','orps':'a.u[k] | b.u[k]','xorps':'a.u[k] ^ b.u[k]'}
    if m in logical:
        return result(load(ops[0],'a')+load(ops[1],'b')+f'for (int k = 0; k < 4; ++k) r.u[k] = {logical[m]}; '+store(ops[0],'r'))
    if m == 'shufps':
        v=ops[2].imm
        return result(load(ops[0],'a')+load(ops[1],'b')+f'r.u[0]=a.u[{v&3}]; r.u[1]=a.u[{v>>2&3}]; r.u[2]=b.u[{v>>4&3}]; r.u[3]=b.u[{v>>6&3}]; '+store(ops[0],'r'))
    if m in ('unpcklps','unpckhps'):
        k=0 if m=='unpcklps' else 2
        return result(load(ops[0],'a')+load(ops[1],'b')+f'r.u[0]=a.u[{k}]; r.u[1]=b.u[{k}]; r.u[2]=a.u[{k+1}]; r.u[3]=b.u[{k+1}]; '+store(ops[0],'r'))
    predicates={'eq':'a.f[k] == b.f[k]','lt':'a.f[k] < b.f[k]','le':'a.f[k] <= b.f[k]',
                'unord':'isnan(a.f[k]) || isnan(b.f[k])','neq':'a.f[k] != b.f[k]',
                'nlt':'!(a.f[k] < b.f[k])','nle':'!(a.f[k] <= b.f[k])','ord':'!isnan(a.f[k]) && !isnan(b.f[k])'}
    if m.startswith('cmp') and m[3:-2] in predicates and m[-2:] in ('ss','ps'):
        lanes=1 if m.endswith('ss') else 4
        return result(load(ops[0],'a')+load(ops[1],'b',lanes*4)+'r = a; '+f'for(int k=0;k<{lanes};++k) r.u[k]=({predicates[m[3:-2]]}) ? UINT32_MAX : 0; '+store(ops[0],'r'))
    if m in ('comiss','ucomiss'):
        return result(load(ops[0],'a',4)+load(ops[1],'b',4)+'int un=isnan(a.f[0])||isnan(b.f[0]); zf=un||a.f[0]==b.f[0]; cf=un||a.f[0]<b.f[0]; pf=un; of=sf=af=0;')
    if m == 'cvtsi2ss':
        return result(load(ops[0],'r')+rounding_begin+f'r.f[0] = (float)(int32_t)({rc.rd(insn,ops[1])}); '+rounding_end+store(ops[0],'r'))
    if m == 'cvtpi2ps':
        return result(load(ops[0],'r')+rounding_begin+f'a.q[0] = {rc.mmx_op(insn,ops[1])}; r.f[0]=(float)a.i[0]; r.f[1]=(float)a.i[1]; '+rounding_end+store(ops[0],'r'))
    if m in ('cvtps2pi','cvttps2pi'):
        # SSE conversion uses MXCSR rounding, not the guest x87 control word.
        rounding='trunc(v)' if m.startswith('cvtt') else '(mode==1 ? floor(v) : mode==2 ? ceil(v) : mode==3 ? trunc(v) : nearbyint(v))'
        return result(load(ops[1],'a',8)+'int old_round=fegetround(); fesetround(FE_TONEAREST); int mode=(c->mxcsr>>13)&3; '
                      f'for(int k=0;k<2;++k) {{ double v=a.f[k]; double t={rounding}; r.u[k]=(!isfinite(t)||t < -2147483648.0||t>=2147483648.0) ? 0x80000000u : (uint32_t)(int32_t)t; }} '
                      'fesetround(old_round); '+f'{rc.mmx_op(insn,ops[0])}=r.q[0];')
    return None

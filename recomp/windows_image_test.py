"""HLSL/D3D12 image proof, autodiscovered by runtime/offline_tests.sh.

Retail fe golden: intact emitter 2/2 (exit 0); DP4->MUL mutant 1/2 (exit 1).
Image root/layout vs both shader pairs: fixed 2/2 (exit 0); original 1/2
(exit 1), missing t1 / s1 / TEXCOORD1 controls each 1/2.
Mask wiring/readback: fixed exit 0; original opaque SRV + no blend exit 1;
alias/no-blend/wrong-upload controls exit 1; 3 pixel negatives rejected.
Ordinary fake-readback BMP: 1,228,854 bytes identical to original fragments.
IR checks reject unsupported operations/interfaces and a coissue read hazard.
Mac COFF: 3/3 new units; native dependencies classified, unknown 0. Injected
native dependency: unknown 1 and coverage rejects it. Missing bytecode invalidates
stale response/HLSL/executable files. No Windows binary or game is launched.
Exit 3 if the private retail fixtures or required compiler tools are absent.
"""
import hashlib
import importlib.util
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
SHADERS = ROOT / 'host/shaders'
IMAGE = ROOT / 'runtime/win/image'
DATA = SHADERS / 'testdata/fe'
PINS = {
    'vs': 'd052fd907941ee4a7f5194ef6fac3e402ea4c6cbbc34dece044fd7eae6e08019',
    'ps': 'adf1eb6cc3716d24759139d3af433f2d177f423bdb0649a814cae70aee07b326',
}
GOLDEN_PINS = {
    'vs': 'ad7daf80290ecbcbfad450f4c197c8bc74d35061163b3e6d7e963018efc7d11e',
    'ps': 'a2bcaa73420848289e4d97bcb6cde092ef42569dedc091f43a68511efbbe9a2d',
}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def run(argv, expected=0):
    result = subprocess.run(list(map(str, argv)), capture_output=True, text=True)
    assert result.returncode == expected, (argv[0], result.returncode, result.stdout[-1500:], result.stderr[-1500:])
    return result.stdout


def golden(tool, output):
    good = 0
    for stage in PINS:
        target = output / f'check_{stage}.hlsl'
        run([tool, DATA / f't0_world_p0_{stage}_1_1.bin', target])
        good += target.read_bytes() == (DATA / f'fe_{stage}.hlsl.golden').read_bytes()
    print(f'HLSL retail fe golden {good}/2')
    return int(good != 2)


IR_CHECK = r'''
#include "hlsl.h"
#include <cstdio>
#include <vector>
static sm::Shader read(const char *path) {
    FILE *f=fopen(path,"rb"); if (!f) std::abort();
    fseek(f,0,SEEK_END); long n=ftell(f); rewind(f);
    std::vector<uint32_t> t(n/4); if (fread(t.data(),1,n,f)!=size_t(n)) std::abort(); fclose(f);
    sm::Shader s; if (!sm::parse(t.data(),t.size(),s)) std::abort(); return s;
}
int main(int argc,char **argv) {
    if (argc!=3) return 2;
    sm::Shader v=read(argv[1]), p=read(argv[2]); std::string source,error;
    if (!sm::emit_hlsl(v,source,error) || source.empty() || !error.empty()) return 1;
    unsigned math=0; while (math<v.code.size() && v.code[math].op!=sm::OP_DP4) math++;
    if (math==v.code.size()) return 2;
    for (int k=0;k<5;k++) {
        sm::Shader bad=v;
        if (k==0) bad.code[math].op=sm::OP_RSQ;
        if (k==1) bad.code[math].src[0].rel=true;
        if (k==2) bad.code[0].usage=sm::R_LABEL;
        if (k==3) bad.major=3;
        if (k==4) for (auto &i:bad.code) if (i.dst.type==sm::R_RASTOUT) i.dst.mask=1;
        source="stale"; error.clear();
        if (sm::emit_hlsl(bad,source,error) || !source.empty() || error.empty()) return 10+k;
    }
    // The retail alpha coissue is safe. Redirect its read into the preceding
    // RGB write; a sequential emitter must reject this read-before-write hazard.
    bool found=false;
    for (auto &i:p.code) if (i.coissue) { i.src[0].swz[3]=0; found=true; }
    if (!found || sm::emit_hlsl(p,source,error) || !source.empty()) return 20;
    puts("IR checks: 6 unsupported/hazard cases rejected"); return 0;
}
'''



# Minimal original descriptor fragments, pinned independently of repository history.
ORIGINAL_INTERFACE = '    D3D12_DESCRIPTOR_RANGE range={0}; range.RangeType=D3D12_DESCRIPTOR_RANGE_TYPE_SRV; range.NumDescriptors=1;\n    D3D12_ROOT_PARAMETER parameters[2]={0};\n    parameters[0].ParameterType=D3D12_ROOT_PARAMETER_TYPE_CBV;\n    parameters[0].ShaderVisibility=D3D12_SHADER_VISIBILITY_VERTEX;\n    parameters[1].ParameterType=D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;\n    parameters[1].ShaderVisibility=D3D12_SHADER_VISIBILITY_PIXEL;\n    parameters[1].DescriptorTable.NumDescriptorRanges=1; parameters[1].DescriptorTable.pDescriptorRanges=&range;\n    D3D12_STATIC_SAMPLER_DESC sampler={0}; sampler.Filter=D3D12_FILTER_MIN_MAG_MIP_POINT;\n    sampler.AddressU=sampler.AddressV=sampler.AddressW=D3D12_TEXTURE_ADDRESS_MODE_CLAMP;\n    sampler.ComparisonFunc=D3D12_COMPARISON_FUNC_ALWAYS; sampler.MaxLOD=D3D12_FLOAT32_MAX;\n    sampler.ShaderVisibility=D3D12_SHADER_VISIBILITY_PIXEL; sampler.MaxAnisotropy=1;\n    D3D12_ROOT_SIGNATURE_DESC rd={0}; rd.NumParameters=2; rd.pParameters=parameters;\n    rd.NumStaticSamplers=1; rd.pStaticSamplers=&sampler;\n    rd.Flags=D3D12_ROOT_SIGNATURE_FLAG_ALLOW_INPUT_ASSEMBLER_INPUT_LAYOUT;\n    D3D12_INPUT_ELEMENT_DESC elements[3]={\n        {"POSITION",0,DXGI_FORMAT_R32G32B32A32_FLOAT,0,0,D3D12_INPUT_CLASSIFICATION_PER_VERTEX_DATA,0},\n        {"COLOR",0,DXGI_FORMAT_R32G32B32A32_FLOAT,0,16,D3D12_INPUT_CLASSIFICATION_PER_VERTEX_DATA,0},\n        {"TEXCOORD",0,DXGI_FORMAT_R32G32B32A32_FLOAT,0,32,D3D12_INPUT_CLASSIFICATION_PER_VERTEX_DATA,0}\n    };\n'
ORIGINAL_INTERFACE_SHA256 = 'f3221bae81ebdf51c8028e82fba8220b59e833deeb1eb767015ad64d037c0185'


def pipeline_interface(compiler, folder, text, label):
    """Execute the C descriptor initializers; no D3D calls or SDK ABI execution."""
    root = text[text.index('    D3D12_DESCRIPTOR_RANGE range='):text.index('    CHECK(D3D12SerializeRootSignature')]
    layout = text[text.index('    D3D12_INPUT_ELEMENT_DESC elements['):text.index('    D3D12_GRAPHICS_PIPELINE_STATE_DESC pd=')]
    vertex = re.search(r'    struct Vertex \{[^}]+\};', text).group()
    count = re.search(r'pd.InputLayout.NumElements=([^;]+);', text).group(1)
    code = '#define RECOMP_WINDOWS_COMPILE_ONLY\n#include "sdk_compile.h"\nint main(void) {\n' + vertex + root + layout
    code += r"""
    D3D12_GRAPHICS_PIPELINE_STATE_DESC pd={0};
    pd.InputLayout.pInputElementDescs=elements;
    pd.InputLayout.NumElements=COUNT;
    for (unsigned p=0;p<rd.NumParameters;p++) {
        const D3D12_ROOT_PARAMETER *parameter=&rd.pParameters[p];
        if (parameter->ParameterType==D3D12_ROOT_PARAMETER_TYPE_CBV)
            printf("b %u %u %d\n",parameter->Descriptor.ShaderRegister,
                parameter->Descriptor.RegisterSpace,parameter->ShaderVisibility);
        else if (parameter->ParameterType==D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE)
            for (unsigned r=0;r<parameter->DescriptorTable.NumDescriptorRanges;r++) {
                const D3D12_DESCRIPTOR_RANGE *range=&parameter->DescriptorTable.pDescriptorRanges[r];
                if (range->RangeType!=D3D12_DESCRIPTOR_RANGE_TYPE_SRV) continue;
                for (unsigned t=0;t<range->NumDescriptors;t++)
                    printf("t %u %u %d\n",range->BaseShaderRegister+t,
                        range->RegisterSpace,parameter->ShaderVisibility);
            }
    }
    for (unsigned s=0;s<rd.NumStaticSamplers;s++)
        printf("s %u %u %d\n",rd.pStaticSamplers[s].ShaderRegister,
            rd.pStaticSamplers[s].RegisterSpace,rd.pStaticSamplers[s].ShaderVisibility);
    for (unsigned i=0;i<pd.InputLayout.NumElements;i++) {
        const D3D12_INPUT_ELEMENT_DESC *e=&pd.InputLayout.pInputElementDescs[i];
        if (e->Format!=DXGI_FORMAT_R32G32B32A32_FLOAT || e->InputSlot ||
            e->AlignedByteOffset+16>sizeof(struct Vertex) ||
            e->InputSlotClass!=D3D12_INPUT_CLASSIFICATION_PER_VERTEX_DATA || e->InstanceDataStepRate) return 1;
        printf("i %s%u\n",e->SemanticName,e->SemanticIndex);
    }
    return 0;
}
""".replace('COUNT', count)
    source = folder / f'{label}.c'
    source.write_text(code)
    executable = folder / label
    run([compiler, '-std=c11', '-fdeclspec', '-Wno-ignored-attributes', '-I', IMAGE, source, '-o', executable])
    return set(run([executable]).splitlines())


def pipeline_contract(compiler, folder, tool):
    source = (IMAGE / 'image.c').read_text()
    assert hashlib.sha256(ORIGINAL_INTERFACE.encode()).hexdigest() == ORIGINAL_INTERFACE_SHA256
    root_start = source.index('    D3D12_DESCRIPTOR_RANGE range=')
    root_end = source.index('    CHECK(D3D12SerializeRootSignature')
    layout_start = source.index('    D3D12_INPUT_ELEMENT_DESC elements[')
    layout_end = source.index('    D3D12_GRAPHICS_PIPELINE_STATE_DESC pd=')
    old_root, old_layout = ORIGINAL_INTERFACE.split('    D3D12_INPUT_ELEMENT_DESC', 1)
    old_layout = '    D3D12_INPUT_ELEMENT_DESC' + old_layout
    legacy = source[:root_start] + old_root + source[root_end:layout_start] + old_layout + source[layout_end:]
    legacy = re.sub(r'pd.InputLayout.NumElements=[^;]+;', 'pd.InputLayout.NumElements=3;', legacy)
    contracts = {}
    # Reuse the four independently pinned fixtures and golden digests.
    fixtures = load('image_fe_fixtures', ROOT / 'recomp/windows_hlsl_fe_test.py')
    for pair, stage, binary_pin, golden_pin in fixtures.PINS:
        data = SHADERS / 'testdata' / pair
        binary = data / f't0_world_p0_{stage}_1_1.bin'
        assert hashlib.sha256(binary.read_bytes()).hexdigest() == binary_pin
        target = folder / f'contract_{pair}_{stage}.hlsl'
        run([tool, binary, target])
        assert hashlib.sha256(target.read_bytes()).hexdigest() == golden_pin
        hlsl = target.read_text()
        needed = contracts.setdefault(pair, set())
        visibility = 1 if stage == 'vs' else 5
        needed.update(f'{kind} {index} 0 {visibility}' for kind, index in re.findall(r'register\(([bts])(\d+)\)', hlsl))
        if stage == 'vs':
            inputs = re.search(r'struct VSInput \{([^}]+)\}', hlsl).group(1)
            needed.update('i ' + semantic for semantic in re.findall(r':\s*(\w+)', inputs))
    current = pipeline_interface(compiler, folder, source, 'current-interface')
    original = pipeline_interface(compiler, folder, legacy, 'original-interface')
    good = sum(required <= current for required in contracts.values())
    old = sum(required <= original for required in contracts.values())
    print(f'image pipeline contracts: current {good}/2; original {old}/2')
    assert old == 1 and contracts['fe'] <= original
    assert {'t 1 0 5', 's 1 0 5', 'i TEXCOORD1'} <= contracts['fe_mask'] - original
    assert good == 2, f'missing image bindings/inputs: {contracts["fe_mask"] - current}'
    mutants = (
        ('srv', 'range.NumDescriptors=2;', 'range.NumDescriptors=1;'),
        ('sampler', 'samplers[1].ShaderRegister=1;', 'samplers[1].ShaderRegister=0;'),
        ('input', 'pd.InputLayout.NumElements=4;', 'pd.InputLayout.NumElements=3;'),
    )
    for label, intact, broken in mutants:
        assert source.count(intact) == 1
        interface = pipeline_interface(compiler, folder, source.replace(intact, broken), label + '-interface')
        assert contracts['fe'] <= interface and not contracts['fe_mask'] <= interface
    print('image pipeline negative controls: t1 / s1 / TEXCOORD1 each 1/2')



# Original non-discriminating SRV and blend fragments, independent of git history.
OPAQUE_SRV = '    // Reuse the opaque texture as the mask; its alpha is one at every texel.\n    srv_handle.ptr+=CALL(device,GetDescriptorHandleIncrementSize,D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV);\n    CALL(device,CreateShaderResourceView,texture,&view,srv_handle);\n'
OPAQUE_BLEND = '    pd.BlendState.RenderTarget[0].SrcBlend=pd.BlendState.RenderTarget[0].SrcBlendAlpha=D3D12_BLEND_ONE;\n    pd.BlendState.RenderTarget[0].DestBlend=pd.BlendState.RenderTarget[0].DestBlendAlpha=D3D12_BLEND_ZERO;\n'
OPAQUE_SHA256 = '3e27426cc5a1660cfac6429dbe9a16003565802532097fe8d908283d280aa71d'


def image_pixels(compiler, folder):
    """Execute production upload, SRV, copy, blend and capture C on fake resources.

    Software fixture shading follows the pinned retail output (RGB = texture,
    alpha = colour alpha * mask alpha). This checks wiring and the pixel oracle;
    native D3D sampling/rasterization remains a desktop check.
    """
    source = (IMAGE / 'image.c').read_text()
    assert 'int masked=argc==5 && !strcmp(argv[4],"--mask");' in source
    assert 'if (!screenshot(readback,argv[3],masked))' in source
    assert hashlib.sha256((OPAQUE_SRV + OPAQUE_BLEND).encode()).hexdigest() == OPAQUE_SHA256

    def section(text, start, end):
        return text[text.index(start):text.index(end, text.index(start))]

    srv_start = '    srv_handle.ptr+='
    srv_end = '    heap.Type=D3D12_HEAP_TYPE_READBACK;'
    blend_start = '    pd.BlendState.RenderTarget[0].SrcBlend='
    blend_end = '    pd.BlendState.RenderTarget[0].BlendOp='
    original = source.replace(section(source, srv_start, srv_end), OPAQUE_SRV)
    original = original.replace(section(original, blend_start, blend_end), OPAQUE_BLEND)
    prelude = r"""
#include <string.h>
#undef memcpy
#define NFS_WIN_TEST
#include "sdk_compile.h"
#include <assert.h>
#define CALL(p, method, ...) fake_##method(p, __VA_ARGS__)
#define CHECK(expr) assert((expr)>=0)
#define fake_Unmap(...) ((void)0)
#define fake_Map(p,sub,range,out) (*(out)=frame,0)
#define fake_CreateCommittedResource(d,h,f,desc,state,clear,iid,out) (*(out)=&resources[allocated++],0)
#define fake_GetCPUDescriptorHandleForHeapStart(heap,out) ((out)->ptr=0)
#define fake_GetDescriptorHandleIncrementSize(...) 1
#define fake_CreateShaderResourceView(d,r,v,handle) (slots[(handle).ptr]=(r))
#define fake_CopyTextureRegion(list,d,x,y,z,s,box) \
    memcpy((d)->pResource==texture?colour:mask_data,mapped+(s)->PlacedFootprint.Offset,512)
#define transition(...) ((void)0)
static unsigned char frame[640*480*4];
"""
    driver = r"""
int main(int argc,char **argv) {
    assert(argc==3); int masked=atoi(argv[1]);
    unsigned char mapped[8192]={0},colour[512]={0},mask_data[512]={0};
    ID3D12Resource resources[2]={0},*texture=NULL,*mask=NULL,*upload=NULL,*slots[2]={0};
    unsigned allocated=0; D3D12_HEAP_PROPERTIES heap={0};
    INIT
    DESCRIPTORS
    COPIES
    D3D12_GRAPHICS_PIPELINE_STATE_DESC pd={0};
    BLEND
    assert(allocated==2 && slots[0]==texture && (slots[1]==texture || slots[1]==mask));
    assert(MASK_OFFSET%512==0 && MASK_OFFSET>=TEX_OFFSET+512 && MASK_OFFSET+512<=sizeof mapped);
    unsigned char *mask_bytes=slots[1]==texture?colour:mask_data;
    const D3D12_RENDER_TARGET_BLEND_DESC *blend=&pd.BlendState.RenderTarget[0];
    for (unsigned y=0;y<HEIGHT;y++) for (unsigned x=0;x<WIDTH;x++) {
        unsigned char *p=frame+y*PITCH+x*4;
        p[0]=5; p[1]=10; p[2]=20; p[3]=255;
        if (x<150 || x>=490 || y<180 || y>=400) continue;
        unsigned texel=(y>=HEIGHT/2?256:0)+(x>=WIDTH/2?4:0);
        double alpha=colour[texel+3]/255.0;
        if (masked) alpha*=mask_bytes[texel+3]/255.0;
        for (unsigned k=0;k<3;k++) {
            double value=colour[texel+k]/255.0;
            if (blend->BlendEnable) {
                assert(blend->SrcBlend==D3D12_BLEND_SRC_ALPHA &&
                    blend->DestBlend==D3D12_BLEND_INV_SRC_ALPHA);
                const double background[]={.02,.04,.08};
                value=value*alpha+background[k]*(1-alpha);
            }
            p[k]=(unsigned char)(value*255+.5);
        }
        assert(blend->SrcBlendAlpha==D3D12_BLEND_ONE && blend->DestBlendAlpha==D3D12_BLEND_ZERO);
        p[3]=(unsigned char)(alpha*255+.5);
    }
    if (!screenshot(NULL,argv[2],masked)) return 1;
    // Pixel-oracle negative controls, independent of the wiring controls below.
    if (masked) {
        unsigned char *left=frame+300*PITCH+200*4;
        unsigned char saved[4]; memcpy(saved,left,4);
        left[3]=255; assert(!mask_pixels(frame)); memcpy(left,saved,4);
        left[2]=255; assert(!mask_pixels(frame)); memcpy(left,saved,4);
        memset(left,0,4); assert(!mask_pixels(frame));
    }
    return 0;
}
"""

    def execute(text, label, masked, expected=0):
        fragments = {
            'INIT': section(text, '    // A tiny original test texture:', '    CALL(upload,Unmap'),
            'DESCRIPTORS': section(text, '    D3D12_RESOURCE_DESC td=', srv_end),
            'COPIES': section(text, '    D3D12_TEXTURE_COPY_LOCATION dest=', '    transition(list,back,'),
            'BLEND': section(text, blend_start, '    pd.DepthStencilState.DepthWriteMask='),
        }
        code = driver
        for token, fragment in fragments.items():
            code = code.replace('    ' + token + '\n', fragment)
        helpers = section(text, 'static void put32(', 'int main(')
        enums = re.search(r'enum \{ WIDTH=[^;]+;', text).group()
        c = folder / f'{label}-pixels.c'
        c.write_text(prelude + enums + '\n' + helpers + code)
        executable = c.with_suffix('')
        run([compiler, '-std=c11', '-I', IMAGE, c, '-o', executable])
        bmp = folder / f'{label}-{masked}.bmp'
        output = run([executable, masked, bmp], expected)
        return bmp.read_bytes(), output

    old_bmp, _ = execute(original, 'opaque-ordinary', 0)
    new_bmp, _ = execute(source, 'current-ordinary', 0)
    assert old_bmp == new_bmp and len(new_bmp) == 1228854
    _, output = execute(source, 'current-masked', 1)
    assert 'IMAGE PASS: masked pair, mask sampled (left/right alpha ratio 0.251)' in output
    assert output.count('mask sampling/blending check failed') == 3
    execute(original, 'opaque-masked', 1, 1)
    alias = source.replace('CreateShaderResourceView,mask,&view', 'CreateShaderResourceView,texture,&view')
    execute(alias, 'aliased-masked', 1, 1)
    no_blend = source.replace('BlendEnable=TRUE;', 'BlendEnable=FALSE;')
    execute(no_blend, 'unblended-masked', 1, 1)
    no_copy = source.replace('src.PlacedFootprint.Offset=MASK_OFFSET;', 'src.PlacedFootprint.Offset=TEX_OFFSET;')
    execute(no_copy, 'opaque-upload-masked', 1, 1)
    print('image mask pixels: fixed exit 0, original exit 1; alias/no-blend/wrong-upload exit 1; '
          '3 readback negatives rejected; ordinary BMP 1228854 bytes identical')


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--golden-tool':
        return golden(Path(sys.argv[2]), Path(sys.argv[3]))
    compiler = shutil.which('clang')
    if not compiler or not all((SHADERS / 'testdata' / pair / f't0_world_p0_{stage}_1_1.bin').is_file()
                               for pair in ('fe', 'fe_mask') for stage in PINS):
        print('SKIP windows image: compiler or private retail bytecode absent')
        return 3
    symbols = load('image_symbols', ROOT / 'recomp/windows_symbols.py')
    try:
        symbols.find_nm(compiler)
    except RuntimeError:
        print('SKIP windows image: llvm-nm absent')
        return 3
    for stage, digest in PINS.items():
        assert hashlib.sha256((DATA / f't0_world_p0_{stage}_1_1.bin').read_bytes()).hexdigest() == digest
        assert hashlib.sha256((DATA / f'fe_{stage}.hlsl.golden').read_bytes()).hexdigest() == GOLDEN_PINS[stage]
    builder = load('build_image', IMAGE / 'build_image.py')
    coverage = load('image_coverage', ROOT / 'recomp/hook_coverage.py')
    with tempfile.TemporaryDirectory(prefix='windows-image-') as temp:
        folder = Path(temp)
        args = SimpleNamespace(compiler=compiler, sdk_headers=False, image_shader_dir=DATA)
        assert builder.build(args, folder) == 0
        report = json.loads((folder / 'report.json').read_text())
        assert report['compiled'] == report['total'] == 3
        rows = json.loads((folder / 'hook-coverage.json').read_text())
        closure = json.loads((folder / 'symbol-closure.json').read_text())
        assert {r['symbol'] for r in rows} == set(closure['desktop'])
        assert not closure['unknown']
        assert {'user32.lib', 'd3d12.lib', 'd3dcompiler.lib', 'dxgi.lib', 'dxguid.lib'} <= set(closure['desktop'].values())
        tool = folder / 'shader-hlsl'
        pipeline_contract(compiler, folder, tool)
        image_pixels(compiler, folder)
        print(run([sys.executable, '-I', __file__, '--golden-tool', tool, folder]).strip())
        mutant = folder / 'sm_hlsl_broken.cpp'
        text = (SHADERS / 'sm_hlsl.cpp').read_text()
        original = 'i.op == OP_DP4 ? "sm_dp4(" : "sm_mul("'
        assert text.count(original) == 1
        mutant.write_text(text.replace(original, 'i.op == OP_DP4 ? "sm_mul(" : "sm_mul("'))
        broken = folder / 'broken-hlsl'
        common = [compiler, '--driver-mode=g++', '-std=c++17', '-O1', '-I', SHADERS, '-I', IMAGE]
        run([*common, SHADERS / 'sm_parse.cpp', mutant, IMAGE / 'translate.cpp', '-o', broken])
        print('broken emitter: ' + run([sys.executable, '-I', __file__, '--golden-tool', broken, folder], 1).strip())
        check = folder / 'ir_check.cpp'; check.write_text(IR_CHECK)
        run([*common, check, SHADERS / 'sm_parse.cpp', SHADERS / 'sm_hlsl.cpp', '-o', folder / 'ir_check'])
        print(run([folder / 'ir_check', *[DATA / f't0_world_p0_{s}_1_1.bin' for s in PINS]]).strip())
        missing = folder / 'missing.c'
        missing.write_text('void image_dependency_missing(void); void injection(void) { image_dependency_missing(); }\n')
        run([compiler, '-target', builder.TARGET, '-c', missing, '-o', folder / 'missing.obj'])
        assert not symbols.audit(compiler, [], [folder / 'image.obj', folder / 'missing.obj'], folder, sdk_headers=False)
        try:
            coverage.image_dependencies([*closure['desktop'], 'image_dependency_missing'], symbols.DESKTOP)
        except ValueError as error:
            assert 'image_dependency_missing' in str(error)
        else:
            raise AssertionError('unclassified image dependency accepted')
        (folder / 'nfsmw-image.exe').write_bytes(b'stale')
        args.image_shader_dir = folder / 'absent'
        assert builder.build(args, folder) == 1
        assert not any((folder / n).exists() for n in ('objects.rsp', 'fe_vs.hlsl', 'fe_ps.hlsl', 'nfsmw-image.exe'))
    print('windows_image_test PASS: golden 2/2, broken 1/2; COFF 3/3; missing dependency rejected')
    return 0


if __name__ == '__main__':
    sys.exit(main())

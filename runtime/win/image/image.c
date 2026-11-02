// Standalone D3D12 image proof. No guest boot or Mac renderer entry points.
#if defined(RECOMP_WINDOWS_COMPILE_ONLY)
#include "sdk_compile.h"
#else
#define WIN32_LEAN_AND_MEAN
#define CINTERFACE
#define COBJMACROS
#include <windows.h>
#include <d3d12.h>
#include <dxgi1_4.h>
#include <d3dcompiler.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#endif
#define CALL(p, method, ...) ((p)->lpVtbl->method((p), __VA_ARGS__))
#define CALL0(p, method) ((p)->lpVtbl->method((p)))
#define DROP(p) do { if (p) CALL0(p, Release); } while (0)
#define CHECK(expr) do { HRESULT check_hr = (expr); if (check_hr < 0) { \
    printf("image: %s failed 0x%08lx\n", #expr, (unsigned long)check_hr); goto done; } } while (0)
enum { WIDTH=640, HEIGHT=480, PITCH=WIDTH*4, TEX_OFFSET=4608, MASK_OFFSET=5120 };
static LRESULT CALLBACK window_proc(HWND w, UINT m, WPARAM a, LPARAM b) {
    if (m == WM_CLOSE || (m == WM_KEYDOWN && a == VK_ESCAPE)) { DestroyWindow(w); return 0; }
    if (m == WM_DESTROY) { PostQuitMessage(0); return 0; }
    return DefWindowProcA(w,m,a,b);
}
static int compile_shader(const char *path, const char *profile, ID3DBlob **code) {
    FILE *f=fopen(path,"rb"); if (!f) { printf("image: cannot read %s\n",path); return 0; }
    fseek(f,0,SEEK_END); long n=ftell(f); rewind(f);
    if (n<=0 || n>1048576) { fclose(f); return 0; }
    char *text=malloc((size_t)n); if (!text) { fclose(f); return 0; }
    int ok=fread(text,1,(size_t)n,f)==(size_t)n; fclose(f);
    ID3DBlob *errors=NULL;
    HRESULT hr=ok ? D3DCompile(text,(size_t)n,path,NULL,NULL,"sm_main",profile,
        D3DCOMPILE_ENABLE_STRICTNESS|D3DCOMPILE_IEEE_STRICTNESS,0,code,&errors) : (HRESULT)-1;
    if (errors) { printf("%.*s\n",(int)CALL0(errors,GetBufferSize),(char *)CALL0(errors,GetBufferPointer)); DROP(errors); }
    free(text); return hr>=0;
}
static D3D12_RESOURCE_DESC buffer_desc(UINT64 bytes) {
    D3D12_RESOURCE_DESC d={0}; d.Dimension=D3D12_RESOURCE_DIMENSION_BUFFER;
    d.Width=bytes; d.Height=1; d.DepthOrArraySize=1; d.MipLevels=1;
    d.SampleDesc.Count=1; d.Layout=D3D12_TEXTURE_LAYOUT_ROW_MAJOR; return d;
}
static void transition(ID3D12GraphicsCommandList *list, ID3D12Resource *r,
                       D3D12_RESOURCE_STATES before, D3D12_RESOURCE_STATES after) {
    D3D12_RESOURCE_BARRIER b={0}; b.Type=D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b.Transition.pResource=r; b.Transition.Subresource=D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    b.Transition.StateBefore=before; b.Transition.StateAfter=after;
    CALL(list,ResourceBarrier,1,&b);
}
static void put32(unsigned char *p, unsigned v) { for (unsigned k=0;k<4;k++) p[k]=(unsigned char)(v>>(k*8)); }
static int mask_pixels(const unsigned char *rgba) {
    // Interior bottom texels: blue on the left, yellow on the right. Check both
    // shader alpha and RGB blending, so ignoring t1 or alpha cannot pass.
    const unsigned char *left=rgba+300*PITCH+200*4, *right=rgba+300*PITCH+440*4;
    int ok=left[3]>=63 && left[3]<=65 && right[3]==255 &&
        left[0]>=3 && left[0]<=5 && left[1]>=7 && left[1]<=9 &&
        left[2]>=78 && left[2]<=80 &&
        right[0]==255 && right[1]==255 && right[2]==0;
    printf("image: mask pixels left RGBA %u,%u,%u,%u; right %u,%u,%u,%u\n",
        left[0],left[1],left[2],left[3],right[0],right[1],right[2],right[3]);
    if (ok) printf("IMAGE PASS: masked pair, mask sampled (left/right alpha ratio %.3f)\n",
        (double)left[3]/right[3]);
    else printf("image: mask sampling/blending check failed\n");
    return ok;
}
static int screenshot(ID3D12Resource *readback, const char *path, int masked) {
    unsigned char *rgba=NULL; D3D12_RANGE range={0,PITCH*HEIGHT};
    if (CALL(readback,Map,0,&range,(void **)&rgba)<0) return 0;
    unsigned char header[54]={0}; header[0]='B'; header[1]='M';
    put32(header+2,54+PITCH*HEIGHT); put32(header+10,54); put32(header+14,40);
    put32(header+18,WIDTH); put32(header+22,(unsigned)-HEIGHT); header[26]=1; header[28]=32;
    FILE *f=fopen(path,"wb"); int ok=f!=NULL; unsigned foreground=0;
    if (f) {
        ok=fwrite(header,1,sizeof header,f)==sizeof header;
        for (unsigned y=0;y<HEIGHT;y++) {
            unsigned char row[PITCH];
            for (unsigned x=0;x<WIDTH;x++) {
                const unsigned char *p=rgba+y*PITCH+x*4;
                row[x*4]=p[2]; row[x*4+1]=p[1]; row[x*4+2]=p[0]; row[x*4+3]=255;
                if (p[0]>80 || p[1]>80) foreground++;
            }
            if (fwrite(row,1,sizeof row,f)!=sizeof row) ok=0;
        }
        if (fclose(f)) ok=0;
    }
    if (masked && !mask_pixels(rgba)) ok=0;
    D3D12_RANGE written={0,0}; CALL(readback,Unmap,0,&written);
    printf("image: foreground pixels %u; screenshot %s\n",foreground,path);
    return ok && foreground>20000 && foreground<WIDTH*HEIGHT/2;
}
int main(int argc, char **argv) {
    int result=1, submitted=0, completed=0;
    HWND window=NULL; HANDLE event=NULL;
    IDXGIFactory4 *factory=NULL; IDXGISwapChain1 *swap=NULL;
    ID3D12Device *device=NULL; ID3D12CommandQueue *queue=NULL;
    ID3D12CommandAllocator *allocator=NULL; ID3D12GraphicsCommandList *list=NULL;
    ID3D12DescriptorHeap *rtv=NULL,*srv=NULL;
    ID3D12RootSignature *root=NULL; ID3D12PipelineState *pipeline=NULL;
    ID3D12Resource *back=NULL,*upload=NULL,*texture=NULL,*mask=NULL,*readback=NULL;
    ID3D12Fence *fence=NULL; ID3DBlob *vs=NULL,*ps=NULL,*signature=NULL,*errors=NULL;
    int masked=argc==5 && !strcmp(argv[4],"--mask");
    if (argc!=4 && !masked) { printf("usage: nfsmw-image vs.hlsl ps.hlsl screenshot.bmp [--mask]\n"); return 2; }
    HINSTANCE instance=GetModuleHandleA(NULL);
    WNDCLASSA wc={0}; wc.lpfnWndProc=window_proc; wc.hInstance=instance;
    wc.lpszClassName="NfsD3D12Image"; wc.hCursor=LoadCursorA(NULL,IDC_ARROW);
    if (!RegisterClassA(&wc)) goto done;
    DWORD style=WS_OVERLAPPED|WS_CAPTION|WS_SYSMENU|WS_MINIMIZEBOX;
    RECT rect={0,0,WIDTH,HEIGHT}; if (!AdjustWindowRect(&rect,style,FALSE)) goto done;
    window=CreateWindowExA(0,wc.lpszClassName,"D3D12 - translated retail fe shader",style,
        CW_USEDEFAULT,CW_USEDEFAULT,rect.right-rect.left,rect.bottom-rect.top,NULL,NULL,instance,NULL);
    if (!window) goto done;
    ShowWindow(window,SW_SHOW);
    CHECK(CreateDXGIFactory2(0,&IID_IDXGIFactory4,(void **)&factory));
    CHECK(D3D12CreateDevice(NULL,D3D_FEATURE_LEVEL_11_0,&IID_ID3D12Device,(void **)&device));
    D3D12_COMMAND_QUEUE_DESC qd={0}; qd.Type=D3D12_COMMAND_LIST_TYPE_DIRECT;
    CHECK(CALL(device,CreateCommandQueue,&qd,&IID_ID3D12CommandQueue,(void **)&queue));
    DXGI_SWAP_CHAIN_DESC1 sd={0}; sd.Width=WIDTH; sd.Height=HEIGHT;
    sd.Format=DXGI_FORMAT_R8G8B8A8_UNORM; sd.SampleDesc.Count=1;
    sd.BufferUsage=DXGI_USAGE_RENDER_TARGET_OUTPUT; sd.BufferCount=2;
    sd.SwapEffect=DXGI_SWAP_EFFECT_FLIP_DISCARD;
    CHECK(CALL(factory,CreateSwapChainForHwnd,(IUnknown *)queue,window,&sd,NULL,NULL,&swap));
    CHECK(CALL(factory,MakeWindowAssociation,window,DXGI_MWA_NO_ALT_ENTER));
    CHECK(CALL(swap,GetBuffer,0,&IID_ID3D12Resource,(void **)&back));
    D3D12_DESCRIPTOR_HEAP_DESC hd={0}; hd.NumDescriptors=1; hd.Type=D3D12_DESCRIPTOR_HEAP_TYPE_RTV;
    CHECK(CALL(device,CreateDescriptorHeap,&hd,&IID_ID3D12DescriptorHeap,(void **)&rtv));
    D3D12_CPU_DESCRIPTOR_HANDLE rtv_handle;
    CALL(rtv,GetCPUDescriptorHandleForHeapStart,&rtv_handle);
    CALL(device,CreateRenderTargetView,back,NULL,rtv_handle);
    hd.Type=D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV; hd.Flags=D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE;
    hd.NumDescriptors=2;
    CHECK(CALL(device,CreateDescriptorHeap,&hd,&IID_ID3D12DescriptorHeap,(void **)&srv));
    D3D12_HEAP_PROPERTIES heap={0}; heap.Type=D3D12_HEAP_TYPE_UPLOAD;
    heap.CreationNodeMask=heap.VisibleNodeMask=1;
    D3D12_RESOURCE_DESC bd=buffer_desc(8192);
    CHECK(CALL(device,CreateCommittedResource,&heap,D3D12_HEAP_FLAG_NONE,&bd,
        D3D12_RESOURCE_STATE_GENERIC_READ,NULL,&IID_ID3D12Resource,(void **)&upload));
    // Shared superset: ordinary shaders ignore TEXCOORD1 and the t1/s1 bindings.
    struct Vertex { float p[4],color[4],uv[4],uv1[4]; };
    const struct Vertex vertices[]={
        {{-.85f,-.8f,0,1},{.5f,.5f,.5f,1},{0,1,0,1},{0,1,0,1}},
        {{0,.85f,0,1},{.5f,.5f,.5f,1},{.5f,0,0,1},{.5f,0,0,1}},
        {{.85f,-.8f,0,1},{.5f,.5f,.5f,1},{1,1,0,1},{1,1,0,1}}
    };
    unsigned char *mapped=NULL; D3D12_RANGE no_read={0,0};
    CHECK(CALL(upload,Map,0,&no_read,(void **)&mapped));
    memset(mapped,0,8192); memcpy(mapped,vertices,sizeof vertices);
    float *constants=(float *)(mapped+256); for (unsigned k=0;k<4;k++) constants[k*4+k]=1;
    // A tiny original test texture: red/green over blue/yellow, two 256-byte rows.
    unsigned char *tex=mapped+TEX_OFFSET;
    tex[0]=255; tex[3]=255; tex[5]=255; tex[7]=255;
    tex[256+2]=255; tex[256+3]=255; tex[256+4]=255; tex[256+5]=255; tex[256+7]=255;
    // Independent mask rows, aligned for D3D12 texture uploads. RGB is unused.
    unsigned char *mask_tex=mapped+MASK_OFFSET;
    mask_tex[3]=mask_tex[256+3]=64;
    mask_tex[7]=mask_tex[256+7]=255;
    CALL(upload,Unmap,0,NULL);
    D3D12_RESOURCE_DESC td={0}; td.Dimension=D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    td.Width=2; td.Height=2; td.DepthOrArraySize=1; td.MipLevels=1;
    td.Format=DXGI_FORMAT_R8G8B8A8_UNORM; td.SampleDesc.Count=1;
    heap.Type=D3D12_HEAP_TYPE_DEFAULT;
    CHECK(CALL(device,CreateCommittedResource,&heap,D3D12_HEAP_FLAG_NONE,&td,
        D3D12_RESOURCE_STATE_COPY_DEST,NULL,&IID_ID3D12Resource,(void **)&texture));
    CHECK(CALL(device,CreateCommittedResource,&heap,D3D12_HEAP_FLAG_NONE,&td,
        D3D12_RESOURCE_STATE_COPY_DEST,NULL,&IID_ID3D12Resource,(void **)&mask));
    D3D12_SHADER_RESOURCE_VIEW_DESC view={0}; view.Format=td.Format;
    view.ViewDimension=D3D12_SRV_DIMENSION_TEXTURE2D; view.Shader4ComponentMapping=D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
    view.Texture2D.MipLevels=1;
    D3D12_CPU_DESCRIPTOR_HANDLE srv_handle;
    CALL(srv,GetCPUDescriptorHandleForHeapStart,&srv_handle);
    CALL(device,CreateShaderResourceView,texture,&view,srv_handle);
    srv_handle.ptr+=CALL(device,GetDescriptorHandleIncrementSize,D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV);
    CALL(device,CreateShaderResourceView,mask,&view,srv_handle);
    heap.Type=D3D12_HEAP_TYPE_READBACK; bd=buffer_desc(PITCH*HEIGHT);
    CHECK(CALL(device,CreateCommittedResource,&heap,D3D12_HEAP_FLAG_NONE,&bd,
        D3D12_RESOURCE_STATE_COPY_DEST,NULL,&IID_ID3D12Resource,(void **)&readback));
    D3D12_DESCRIPTOR_RANGE range={0}; range.RangeType=D3D12_DESCRIPTOR_RANGE_TYPE_SRV; range.NumDescriptors=2;
    D3D12_ROOT_PARAMETER parameters[2]={0};
    parameters[0].ParameterType=D3D12_ROOT_PARAMETER_TYPE_CBV;
    parameters[0].ShaderVisibility=D3D12_SHADER_VISIBILITY_VERTEX;
    parameters[1].ParameterType=D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
    parameters[1].ShaderVisibility=D3D12_SHADER_VISIBILITY_PIXEL;
    parameters[1].DescriptorTable.NumDescriptorRanges=1; parameters[1].DescriptorTable.pDescriptorRanges=&range;
    D3D12_STATIC_SAMPLER_DESC sampler={0}; sampler.Filter=D3D12_FILTER_MIN_MAG_MIP_POINT;
    sampler.AddressU=sampler.AddressV=sampler.AddressW=D3D12_TEXTURE_ADDRESS_MODE_CLAMP;
    sampler.ComparisonFunc=D3D12_COMPARISON_FUNC_ALWAYS; sampler.MaxLOD=D3D12_FLOAT32_MAX;
    sampler.ShaderVisibility=D3D12_SHADER_VISIBILITY_PIXEL; sampler.MaxAnisotropy=1;
    D3D12_STATIC_SAMPLER_DESC samplers[2]={sampler,sampler};
    samplers[1].ShaderRegister=1;
    D3D12_ROOT_SIGNATURE_DESC rd={0}; rd.NumParameters=2; rd.pParameters=parameters;
    rd.NumStaticSamplers=2; rd.pStaticSamplers=samplers;
    rd.Flags=D3D12_ROOT_SIGNATURE_FLAG_ALLOW_INPUT_ASSEMBLER_INPUT_LAYOUT;
    CHECK(D3D12SerializeRootSignature(&rd,D3D_ROOT_SIGNATURE_VERSION_1,&signature,&errors));
    CHECK(CALL(device,CreateRootSignature,0,CALL0(signature,GetBufferPointer),CALL0(signature,GetBufferSize),
        &IID_ID3D12RootSignature,(void **)&root));
    if (!compile_shader(argv[1],"vs_5_0",&vs) || !compile_shader(argv[2],"ps_5_0",&ps)) goto done;
    D3D12_INPUT_ELEMENT_DESC elements[4]={
        {"POSITION",0,DXGI_FORMAT_R32G32B32A32_FLOAT,0,0,D3D12_INPUT_CLASSIFICATION_PER_VERTEX_DATA,0},
        {"COLOR",0,DXGI_FORMAT_R32G32B32A32_FLOAT,0,16,D3D12_INPUT_CLASSIFICATION_PER_VERTEX_DATA,0},
        {"TEXCOORD",0,DXGI_FORMAT_R32G32B32A32_FLOAT,0,32,D3D12_INPUT_CLASSIFICATION_PER_VERTEX_DATA,0},
        {"TEXCOORD",1,DXGI_FORMAT_R32G32B32A32_FLOAT,0,48,D3D12_INPUT_CLASSIFICATION_PER_VERTEX_DATA,0}
    };
    D3D12_GRAPHICS_PIPELINE_STATE_DESC pd={0}; pd.pRootSignature=root;
    pd.VS.pShaderBytecode=CALL0(vs,GetBufferPointer); pd.VS.BytecodeLength=CALL0(vs,GetBufferSize);
    pd.PS.pShaderBytecode=CALL0(ps,GetBufferPointer); pd.PS.BytecodeLength=CALL0(ps,GetBufferSize);
    pd.InputLayout.pInputElementDescs=elements; pd.InputLayout.NumElements=4;
    pd.SampleMask=~0u; pd.RasterizerState.FillMode=D3D12_FILL_MODE_SOLID;
    pd.RasterizerState.CullMode=D3D12_CULL_MODE_NONE; pd.RasterizerState.DepthClipEnable=TRUE;
    pd.BlendState.RenderTarget[0].SrcBlend=pd.BlendState.RenderTarget[0].SrcBlendAlpha=D3D12_BLEND_ONE;
    pd.BlendState.RenderTarget[0].DestBlend=pd.BlendState.RenderTarget[0].DestBlendAlpha=D3D12_BLEND_ZERO;
    if (masked) {
        pd.BlendState.RenderTarget[0].BlendEnable=TRUE;
        pd.BlendState.RenderTarget[0].SrcBlend=D3D12_BLEND_SRC_ALPHA;
        pd.BlendState.RenderTarget[0].DestBlend=D3D12_BLEND_INV_SRC_ALPHA;
    }
    pd.BlendState.RenderTarget[0].BlendOp=pd.BlendState.RenderTarget[0].BlendOpAlpha=D3D12_BLEND_OP_ADD;
    pd.BlendState.RenderTarget[0].LogicOp=D3D12_LOGIC_OP_NOOP;
    pd.BlendState.RenderTarget[0].RenderTargetWriteMask=D3D12_COLOR_WRITE_ENABLE_ALL;
    pd.DepthStencilState.DepthWriteMask=D3D12_DEPTH_WRITE_MASK_ZERO;
    pd.DepthStencilState.DepthFunc=D3D12_COMPARISON_FUNC_ALWAYS;
    pd.DepthStencilState.FrontFace.StencilFailOp=pd.DepthStencilState.FrontFace.StencilDepthFailOp=
        pd.DepthStencilState.FrontFace.StencilPassOp=D3D12_STENCIL_OP_KEEP;
    pd.DepthStencilState.FrontFace.StencilFunc=D3D12_COMPARISON_FUNC_ALWAYS;
    pd.DepthStencilState.BackFace=pd.DepthStencilState.FrontFace;
    pd.PrimitiveTopologyType=D3D12_PRIMITIVE_TOPOLOGY_TYPE_TRIANGLE;
    pd.NumRenderTargets=1; pd.RTVFormats[0]=sd.Format; pd.SampleDesc.Count=1;
    CHECK(CALL(device,CreateGraphicsPipelineState,&pd,&IID_ID3D12PipelineState,(void **)&pipeline));
    CHECK(CALL(device,CreateCommandAllocator,D3D12_COMMAND_LIST_TYPE_DIRECT,&IID_ID3D12CommandAllocator,(void **)&allocator));
    CHECK(CALL(device,CreateCommandList,0,D3D12_COMMAND_LIST_TYPE_DIRECT,allocator,pipeline,&IID_ID3D12GraphicsCommandList,(void **)&list));
    D3D12_TEXTURE_COPY_LOCATION dest={0},src={0}; dest.pResource=texture;
    dest.Type=D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    src.pResource=upload; src.Type=D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
    src.PlacedFootprint.Offset=TEX_OFFSET; src.PlacedFootprint.Footprint.Format=td.Format;
    src.PlacedFootprint.Footprint.Width=2; src.PlacedFootprint.Footprint.Height=2;
    src.PlacedFootprint.Footprint.Depth=1; src.PlacedFootprint.Footprint.RowPitch=256;
    CALL(list,CopyTextureRegion,&dest,0,0,0,&src,NULL);
    transition(list,texture,D3D12_RESOURCE_STATE_COPY_DEST,D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
    dest.pResource=mask; src.PlacedFootprint.Offset=MASK_OFFSET;
    CALL(list,CopyTextureRegion,&dest,0,0,0,&src,NULL);
    transition(list,mask,D3D12_RESOURCE_STATE_COPY_DEST,D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
    transition(list,back,D3D12_RESOURCE_STATE_PRESENT,D3D12_RESOURCE_STATE_RENDER_TARGET);
    const float clear[]={.02f,.04f,.08f,1}; CALL(list,ClearRenderTargetView,rtv_handle,clear,0,NULL);
    CALL(list,OMSetRenderTargets,1,&rtv_handle,FALSE,NULL);
    CALL(list,SetGraphicsRootSignature,root); CALL(list,SetDescriptorHeaps,1,&srv);
    CALL(list,SetGraphicsRootConstantBufferView,0,CALL0(upload,GetGPUVirtualAddress)+256);
    D3D12_GPU_DESCRIPTOR_HANDLE srv_gpu_handle;
    CALL(srv,GetGPUDescriptorHandleForHeapStart,&srv_gpu_handle);
    CALL(list,SetGraphicsRootDescriptorTable,1,srv_gpu_handle);
    D3D12_VIEWPORT viewport={0,0,WIDTH,HEIGHT,0,1}; D3D12_RECT scissor={0,0,WIDTH,HEIGHT};
    CALL(list,RSSetViewports,1,&viewport); CALL(list,RSSetScissorRects,1,&scissor);
    D3D12_VERTEX_BUFFER_VIEW vb={CALL0(upload,GetGPUVirtualAddress),sizeof vertices,sizeof(struct Vertex)};
    CALL(list,IASetVertexBuffers,0,1,&vb); CALL(list,IASetPrimitiveTopology,D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    CALL(list,DrawInstanced,3,1,0,0);
    transition(list,back,D3D12_RESOURCE_STATE_RENDER_TARGET,D3D12_RESOURCE_STATE_COPY_SOURCE);
    dest.pResource=readback; dest.Type=D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
    dest.PlacedFootprint.Offset=0; dest.PlacedFootprint.Footprint.Format=sd.Format;
    dest.PlacedFootprint.Footprint.Width=WIDTH; dest.PlacedFootprint.Footprint.Height=HEIGHT;
    dest.PlacedFootprint.Footprint.Depth=1; dest.PlacedFootprint.Footprint.RowPitch=PITCH;
    src.pResource=back; src.Type=D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX; src.SubresourceIndex=0;
    CALL(list,CopyTextureRegion,&dest,0,0,0,&src,NULL);
    transition(list,back,D3D12_RESOURCE_STATE_COPY_SOURCE,D3D12_RESOURCE_STATE_PRESENT);
    CHECK(CALL0(list,Close));
    CHECK(CALL(device,CreateFence,0,D3D12_FENCE_FLAG_NONE,&IID_ID3D12Fence,(void **)&fence));
    event=CreateEventA(NULL,FALSE,FALSE,NULL); if (!event) goto done;
    ID3D12CommandList *commands[]={(ID3D12CommandList *)list};
    CALL(queue,ExecuteCommandLists,1,commands); submitted=1;
    CHECK(CALL(queue,Signal,fence,1));
    CHECK(CALL(fence,SetEventOnCompletion,1,event));
    if (WaitForSingleObject(event,10000)!=WAIT_OBJECT_0) { printf("image: GPU fence timeout\n"); goto done; }
    completed=1;
    if (!screenshot(readback,argv[3],masked)) { printf("image: capture/foreground check failed\n"); goto done; }
    CHECK(CALL(swap,Present,1,0));
    printf("IMAGE PASS: D3D12 retail fe VS+PS; window 640x480; capture complete\n"); fflush(NULL);
    result=0;
    ULONGLONG end=GetTickCount64()+8000; MSG message;
    while (GetTickCount64()<end) {
        if (PeekMessageA(&message,NULL,0,0,PM_REMOVE)) {
            if (message.message==WM_QUIT) break;
            TranslateMessage(&message); DispatchMessageA(&message);
        } else Sleep(10);
    }
done:
    // Do not destroy resources still in flight after a timeout/device failure.
    // Process termination releases them; the failure path never reports IMAGE PASS.
    if (submitted && !completed) return result;
    DROP(errors); DROP(signature); DROP(vs); DROP(ps); DROP(list); DROP(allocator);
    DROP(pipeline); DROP(root); DROP(readback); DROP(mask); DROP(texture); DROP(upload); DROP(back);
    DROP(srv); DROP(rtv); DROP(fence); DROP(swap); DROP(queue); DROP(device); DROP(factory);
    if (event) CloseHandle(event);
    if (window && IsWindow(window)) DestroyWindow(window);
    return result;
}

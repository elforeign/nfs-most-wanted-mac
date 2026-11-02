// Declarations-only SDK surface for Mac COFF checks. Never link or run this ABI.
// Real desktop builds include windows.h/d3d12.h/dxgi1_4.h/d3dcompiler.h instead.
#pragma once
#include "../api.h"
typedef int HRESULT; typedef unsigned UINT, ULONG; typedef uint64_t UINT64, ULONGLONG;
typedef size_t SIZE_T; typedef void *HWND, *HINSTANCE, *HCURSOR;
typedef unsigned long long WPARAM; typedef long long LPARAM, LRESULT;
#define CALLBACK
#define TRUE 1
#define FALSE 0
#define WM_CLOSE 16
#define WM_DESTROY 2
#define WM_KEYDOWN 256
#define WM_QUIT 18
#define VK_ESCAPE 27
#define WS_OVERLAPPED 0
#define WS_CAPTION 0xc00000u
#define WS_SYSMENU 0x80000u
#define WS_MINIMIZEBOX 0x20000u
#define CW_USEDEFAULT ((int)0x80000000u)
#define SW_SHOW 5
#define PM_REMOVE 1
#define IDC_ARROW ((const char *)32512)
typedef struct { long left,top,right,bottom; } RECT, D3D12_RECT;
typedef struct { UINT style; LRESULT (*lpfnWndProc)(HWND,UINT,WPARAM,LPARAM); int cbClsExtra,cbWndExtra;
    HINSTANCE hInstance; void *hIcon; HCURSOR hCursor; void *hbrBackground;
    const char *lpszMenuName,*lpszClassName; } WNDCLASSA;
typedef struct { HWND hwnd; UINT message; WPARAM wParam; LPARAM lParam; DWORD time; long point[2]; } MSG;
WIN_API HINSTANCE GetModuleHandleA(const char *);
WIN_API HCURSOR LoadCursorA(HINSTANCE,const char *);
WIN_API unsigned short RegisterClassA(const WNDCLASSA *);
WIN_API BOOL AdjustWindowRect(RECT *,DWORD,BOOL);
WIN_API HWND CreateWindowExA(DWORD,const char *,const char *,DWORD,int,int,int,int,HWND,void *,HINSTANCE,void *);
WIN_API BOOL ShowWindow(HWND,int);
WIN_API LRESULT DefWindowProcA(HWND,UINT,WPARAM,LPARAM);
WIN_API BOOL DestroyWindow(HWND);
WIN_API void PostQuitMessage(int);
WIN_API BOOL IsWindow(HWND);
WIN_API BOOL PeekMessageA(MSG *,HWND,UINT,UINT,UINT);
WIN_API BOOL TranslateMessage(const MSG *);
WIN_API LRESULT DispatchMessageA(const MSG *);
WIN_API HANDLE CreateEventA(void *,BOOL,BOOL,const char *);
WIN_API ULONGLONG GetTickCount64(void);
size_t fwrite(const void *,size_t,size_t,FILE *);
void rewind(FILE *);
void *memcpy(void *,const void *,size_t);

typedef struct { uint32_t a; uint16_t b,c; unsigned char d[8]; } GUID;
#define IID(name) extern const GUID IID_##name
IID(IDXGIFactory4); IID(ID3D12Device); IID(ID3D12CommandQueue); IID(ID3D12Resource);
IID(ID3D12DescriptorHeap); IID(ID3D12RootSignature); IID(ID3D12PipelineState);
IID(ID3D12CommandAllocator); IID(ID3D12GraphicsCommandList); IID(ID3D12Fence);
#undef IID
#define INTERFACE(name) typedef struct name name
INTERFACE(IUnknown); INTERFACE(IDXGIFactory4); INTERFACE(IDXGISwapChain1);
INTERFACE(ID3D12Device); INTERFACE(ID3D12CommandQueue); INTERFACE(ID3D12CommandList);
INTERFACE(ID3D12CommandAllocator); INTERFACE(ID3D12GraphicsCommandList);
INTERFACE(ID3D12DescriptorHeap); INTERFACE(ID3D12RootSignature); INTERFACE(ID3D12PipelineState);
INTERFACE(ID3D12Resource); INTERFACE(ID3D12Fence); INTERFACE(ID3DBlob);
INTERFACE(IDXGIOutput); INTERFACE(ID3DInclude);
#undef INTERFACE
// Opaque descriptors used only through pointers by the image harness.
typedef struct DXGI_SWAP_CHAIN_FULLSCREEN_DESC DXGI_SWAP_CHAIN_FULLSCREEN_DESC;
typedef struct D3D12_RENDER_TARGET_VIEW_DESC D3D12_RENDER_TARGET_VIEW_DESC;
typedef struct D3D12_CLEAR_VALUE D3D12_CLEAR_VALUE;
typedef struct D3D12_BOX D3D12_BOX;
typedef struct D3D_SHADER_MACRO D3D_SHADER_MACRO;
// SDK enums are represented as ints in this declarations-only compile surface.
typedef int D3D12_COMMAND_LIST_TYPE, D3D12_HEAP_FLAGS, D3D12_FENCE_FLAGS;
typedef int D3D12_PRIMITIVE_TOPOLOGY, D3D_FEATURE_LEVEL, D3D_ROOT_SIGNATURE_VERSION;
typedef UINT64 D3D12_GPU_VIRTUAL_ADDRESS;
// Public enum constants used by the harness. Values are not an execution contract.
enum { D3D_FEATURE_LEVEL_11_0=0xb000, D3D12_COMMAND_LIST_TYPE_DIRECT=0,
    DXGI_FORMAT_R8G8B8A8_UNORM=28, DXGI_FORMAT_R32G32B32A32_FLOAT=2,
    DXGI_USAGE_RENDER_TARGET_OUTPUT=32, DXGI_SWAP_EFFECT_FLIP_DISCARD=4,
    DXGI_MWA_NO_ALT_ENTER=2, D3D12_DESCRIPTOR_HEAP_TYPE_RTV=2,
    D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV=0, D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE=1,
    D3D12_HEAP_TYPE_DEFAULT=1, D3D12_HEAP_TYPE_UPLOAD=2, D3D12_HEAP_TYPE_READBACK=3,
    D3D12_HEAP_FLAG_NONE=0, D3D12_RESOURCE_DIMENSION_BUFFER=1, D3D12_RESOURCE_DIMENSION_TEXTURE2D=3,
    D3D12_TEXTURE_LAYOUT_ROW_MAJOR=1, D3D12_RESOURCE_STATE_GENERIC_READ=0xac3,
    D3D12_RESOURCE_STATE_COPY_DEST=1024, D3D12_RESOURCE_STATE_COPY_SOURCE=2048,
    D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE=128, D3D12_RESOURCE_STATE_PRESENT=0,
    D3D12_RESOURCE_STATE_RENDER_TARGET=4, D3D12_RESOURCE_BARRIER_TYPE_TRANSITION=0,
    D3D12_SRV_DIMENSION_TEXTURE2D=4, D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING=5768,
    D3D12_DESCRIPTOR_RANGE_TYPE_SRV=0, D3D12_ROOT_PARAMETER_TYPE_CBV=2,
    D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE=0, D3D12_SHADER_VISIBILITY_VERTEX=1,
    D3D12_SHADER_VISIBILITY_PIXEL=5, D3D12_FILTER_MIN_MAG_MIP_POINT=0,
    D3D12_TEXTURE_ADDRESS_MODE_CLAMP=3, D3D12_COMPARISON_FUNC_ALWAYS=8,
    D3D12_ROOT_SIGNATURE_FLAG_ALLOW_INPUT_ASSEMBLER_INPUT_LAYOUT=1,
    D3D_ROOT_SIGNATURE_VERSION_1=1, D3D12_INPUT_CLASSIFICATION_PER_VERTEX_DATA=0,
    D3D12_FILL_MODE_SOLID=3, D3D12_CULL_MODE_NONE=1,
    D3D12_BLEND_ONE=2, D3D12_BLEND_ZERO=1, D3D12_BLEND_OP_ADD=1,
    D3D12_BLEND_SRC_ALPHA=5, D3D12_BLEND_INV_SRC_ALPHA=6,
    D3D12_LOGIC_OP_NOOP=5, D3D12_COLOR_WRITE_ENABLE_ALL=15,
    D3D12_DEPTH_WRITE_MASK_ZERO=0, D3D12_STENCIL_OP_KEEP=1,
    D3D12_PRIMITIVE_TOPOLOGY_TYPE_TRIANGLE=3, D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST=4,
    D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX=0, D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT=1,
    D3D12_FENCE_FLAG_NONE=0, D3DCOMPILE_ENABLE_STRICTNESS=2048, D3DCOMPILE_IEEE_STRICTNESS=8192 };
#define D3D12_FLOAT32_MAX 3.402823466e+38F
#define D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES 0xffffffffu
typedef int D3D12_RESOURCE_STATES;
typedef struct { UINT Count,Quality; } DXGI_SAMPLE_DESC;
typedef struct { int Type,Priority; UINT Flags,NodeMask; } D3D12_COMMAND_QUEUE_DESC;
typedef struct { UINT Width,Height; int Format; BOOL Stereo; DXGI_SAMPLE_DESC SampleDesc;
    UINT BufferUsage,BufferCount; int Scaling,SwapEffect,AlphaMode; UINT Flags; } DXGI_SWAP_CHAIN_DESC1;
typedef struct { int Type; UINT NumDescriptors,Flags,NodeMask; } D3D12_DESCRIPTOR_HEAP_DESC;
typedef struct { SIZE_T ptr; } D3D12_CPU_DESCRIPTOR_HANDLE;
typedef struct { UINT64 ptr; } D3D12_GPU_DESCRIPTOR_HANDLE;
typedef struct { int Type,CPUPageProperty,MemoryPoolPreference; UINT CreationNodeMask,VisibleNodeMask; } D3D12_HEAP_PROPERTIES;
typedef struct { int Dimension; UINT64 Alignment,Width; UINT Height; uint16_t DepthOrArraySize,MipLevels;
    int Format; DXGI_SAMPLE_DESC SampleDesc; int Layout; UINT Flags; } D3D12_RESOURCE_DESC;
typedef struct { SIZE_T Begin,End; } D3D12_RANGE;
typedef struct { int Type,Flags; struct { ID3D12Resource *pResource; UINT Subresource;
    D3D12_RESOURCE_STATES StateBefore,StateAfter; } Transition; } D3D12_RESOURCE_BARRIER;
typedef struct { int Format,ViewDimension; UINT Shader4ComponentMapping;
    struct { UINT MostDetailedMip,MipLevels,PlaneSlice; float ResourceMinLODClamp; } Texture2D; } D3D12_SHADER_RESOURCE_VIEW_DESC;
typedef struct { int RangeType; UINT NumDescriptors,BaseShaderRegister,RegisterSpace,OffsetInDescriptorsFromTableStart; } D3D12_DESCRIPTOR_RANGE;
typedef struct { int ParameterType; union { struct { UINT ShaderRegister,RegisterSpace; } Descriptor;
    struct { UINT NumDescriptorRanges; const D3D12_DESCRIPTOR_RANGE *pDescriptorRanges; } DescriptorTable; };
    int ShaderVisibility; } D3D12_ROOT_PARAMETER;
typedef struct { int Filter,AddressU,AddressV,AddressW; float MipLODBias; UINT MaxAnisotropy;
    int ComparisonFunc,BorderColor; float MinLOD,MaxLOD; UINT ShaderRegister,RegisterSpace; int ShaderVisibility; } D3D12_STATIC_SAMPLER_DESC;
typedef struct { UINT NumParameters; const D3D12_ROOT_PARAMETER *pParameters;
    UINT NumStaticSamplers; const D3D12_STATIC_SAMPLER_DESC *pStaticSamplers; UINT Flags; } D3D12_ROOT_SIGNATURE_DESC;
typedef struct { const char *SemanticName; UINT SemanticIndex; int Format; UINT InputSlot,AlignedByteOffset;
    int InputSlotClass; UINT InstanceDataStepRate; } D3D12_INPUT_ELEMENT_DESC;
typedef struct { const void *pShaderBytecode; SIZE_T BytecodeLength; } D3D12_SHADER_BYTECODE;
typedef struct { BOOL BlendEnable,LogicOpEnable; int SrcBlend,DestBlend,BlendOp,SrcBlendAlpha,DestBlendAlpha,BlendOpAlpha,LogicOp;
    unsigned char RenderTargetWriteMask; } D3D12_RENDER_TARGET_BLEND_DESC;
typedef struct { int StencilFailOp,StencilDepthFailOp,StencilPassOp,StencilFunc; } D3D12_DEPTH_STENCILOP_DESC;
typedef struct { ID3D12RootSignature *pRootSignature; D3D12_SHADER_BYTECODE VS,PS;
    struct { const D3D12_INPUT_ELEMENT_DESC *pInputElementDescs; UINT NumElements; } InputLayout;
    UINT SampleMask; struct { int FillMode,CullMode; BOOL DepthClipEnable; } RasterizerState;
    struct { D3D12_RENDER_TARGET_BLEND_DESC RenderTarget[8]; } BlendState;
    struct { int DepthWriteMask,DepthFunc; D3D12_DEPTH_STENCILOP_DESC FrontFace,BackFace; } DepthStencilState;
    int PrimitiveTopologyType; UINT NumRenderTargets; int RTVFormats[8]; DXGI_SAMPLE_DESC SampleDesc; } D3D12_GRAPHICS_PIPELINE_STATE_DESC;
typedef struct { int Format; UINT Width,Height,Depth,RowPitch; } D3D12_SUBRESOURCE_FOOTPRINT;
typedef struct { ID3D12Resource *pResource; int Type; union { UINT SubresourceIndex;
    struct { UINT64 Offset; D3D12_SUBRESOURCE_FOOTPRINT Footprint; } PlacedFootprint; }; } D3D12_TEXTURE_COPY_LOCATION;
typedef struct { float TopLeftX,TopLeftY,Width,Height,MinDepth,MaxDepth; } D3D12_VIEWPORT;
typedef struct { UINT64 BufferLocation; UINT SizeInBytes,StrideInBytes; } D3D12_VERTEX_BUFFER_VIEW;
// Only named members are modeled: no vtable slot/structure layout claim is made.
#define VTABLE(name, ...) typedef struct { ULONG (*Release)(name *); __VA_ARGS__ } name##Vtbl; struct name { const name##Vtbl *lpVtbl; }
VTABLE(ID3DBlob, void *(*GetBufferPointer)(ID3DBlob *); SIZE_T (*GetBufferSize)(ID3DBlob *););
VTABLE(IDXGIFactory4,
    HRESULT (*CreateSwapChainForHwnd)(IDXGIFactory4 *,IUnknown *,HWND,const DXGI_SWAP_CHAIN_DESC1 *,const DXGI_SWAP_CHAIN_FULLSCREEN_DESC *,IDXGIOutput *,IDXGISwapChain1 **);
    HRESULT (*MakeWindowAssociation)(IDXGIFactory4 *,HWND,UINT););
VTABLE(IDXGISwapChain1, HRESULT (*GetBuffer)(IDXGISwapChain1 *,UINT,const GUID *,void **);
    HRESULT (*Present)(IDXGISwapChain1 *,UINT,UINT););
VTABLE(ID3D12Device,
    HRESULT (*CreateCommandQueue)(ID3D12Device *,const D3D12_COMMAND_QUEUE_DESC *,const GUID *,void **);
    HRESULT (*CreateDescriptorHeap)(ID3D12Device *,const D3D12_DESCRIPTOR_HEAP_DESC *,const GUID *,void **);
    UINT (*GetDescriptorHandleIncrementSize)(ID3D12Device *,int);
    void (*CreateRenderTargetView)(ID3D12Device *,ID3D12Resource *,const D3D12_RENDER_TARGET_VIEW_DESC *,D3D12_CPU_DESCRIPTOR_HANDLE);
    HRESULT (*CreateCommittedResource)(ID3D12Device *,const D3D12_HEAP_PROPERTIES *,D3D12_HEAP_FLAGS,const D3D12_RESOURCE_DESC *,D3D12_RESOURCE_STATES,const D3D12_CLEAR_VALUE *,const GUID *,void **);
    void (*CreateShaderResourceView)(ID3D12Device *,ID3D12Resource *,const D3D12_SHADER_RESOURCE_VIEW_DESC *,D3D12_CPU_DESCRIPTOR_HANDLE);
    HRESULT (*CreateRootSignature)(ID3D12Device *,UINT,const void *,SIZE_T,const GUID *,void **);
    HRESULT (*CreateGraphicsPipelineState)(ID3D12Device *,const D3D12_GRAPHICS_PIPELINE_STATE_DESC *,const GUID *,void **);
    HRESULT (*CreateCommandAllocator)(ID3D12Device *,D3D12_COMMAND_LIST_TYPE,const GUID *,void **);
    HRESULT (*CreateCommandList)(ID3D12Device *,UINT,D3D12_COMMAND_LIST_TYPE,ID3D12CommandAllocator *,ID3D12PipelineState *,const GUID *,void **);
    HRESULT (*CreateFence)(ID3D12Device *,UINT64,D3D12_FENCE_FLAGS,const GUID *,void **););
VTABLE(ID3D12CommandQueue, void (*ExecuteCommandLists)(ID3D12CommandQueue *,UINT,ID3D12CommandList *const *);
    HRESULT (*Signal)(ID3D12CommandQueue *,ID3D12Fence *,UINT64););
VTABLE(ID3D12CommandAllocator, ); VTABLE(ID3D12RootSignature, ); VTABLE(ID3D12PipelineState, );
VTABLE(ID3D12DescriptorHeap, void (*GetCPUDescriptorHandleForHeapStart)(ID3D12DescriptorHeap *,D3D12_CPU_DESCRIPTOR_HANDLE *);
    void (*GetGPUDescriptorHandleForHeapStart)(ID3D12DescriptorHeap *,D3D12_GPU_DESCRIPTOR_HANDLE *););
VTABLE(ID3D12Resource, HRESULT (*Map)(ID3D12Resource *,UINT,const D3D12_RANGE *,void **);
    void (*Unmap)(ID3D12Resource *,UINT,const D3D12_RANGE *); D3D12_GPU_VIRTUAL_ADDRESS (*GetGPUVirtualAddress)(ID3D12Resource *););
VTABLE(ID3D12Fence, HRESULT (*SetEventOnCompletion)(ID3D12Fence *,UINT64,HANDLE););
VTABLE(ID3D12GraphicsCommandList,
    void (*ResourceBarrier)(ID3D12GraphicsCommandList *,UINT,const D3D12_RESOURCE_BARRIER *);
    void (*CopyTextureRegion)(ID3D12GraphicsCommandList *,const D3D12_TEXTURE_COPY_LOCATION *,UINT,UINT,UINT,const D3D12_TEXTURE_COPY_LOCATION *,const D3D12_BOX *);
    void (*ClearRenderTargetView)(ID3D12GraphicsCommandList *,D3D12_CPU_DESCRIPTOR_HANDLE,const float *,UINT,const D3D12_RECT *);
    void (*OMSetRenderTargets)(ID3D12GraphicsCommandList *,UINT,const D3D12_CPU_DESCRIPTOR_HANDLE *,BOOL,const D3D12_CPU_DESCRIPTOR_HANDLE *);
    void (*SetGraphicsRootSignature)(ID3D12GraphicsCommandList *,ID3D12RootSignature *);
    void (*SetDescriptorHeaps)(ID3D12GraphicsCommandList *,UINT,ID3D12DescriptorHeap *const *);
    void (*SetGraphicsRootConstantBufferView)(ID3D12GraphicsCommandList *,UINT,D3D12_GPU_VIRTUAL_ADDRESS);
    void (*SetGraphicsRootDescriptorTable)(ID3D12GraphicsCommandList *,UINT,D3D12_GPU_DESCRIPTOR_HANDLE);
    void (*RSSetViewports)(ID3D12GraphicsCommandList *,UINT,const D3D12_VIEWPORT *);
    void (*RSSetScissorRects)(ID3D12GraphicsCommandList *,UINT,const D3D12_RECT *);
    void (*IASetVertexBuffers)(ID3D12GraphicsCommandList *,UINT,UINT,const D3D12_VERTEX_BUFFER_VIEW *);
    void (*IASetPrimitiveTopology)(ID3D12GraphicsCommandList *,D3D12_PRIMITIVE_TOPOLOGY);
    void (*DrawInstanced)(ID3D12GraphicsCommandList *,UINT,UINT,UINT,UINT);
    HRESULT (*Close)(ID3D12GraphicsCommandList *););
#undef VTABLE
WIN_API HRESULT CreateDXGIFactory2(UINT,const GUID *,void **);
WIN_API HRESULT D3D12CreateDevice(IUnknown *,D3D_FEATURE_LEVEL,const GUID *,void **);
WIN_API HRESULT D3D12SerializeRootSignature(const D3D12_ROOT_SIGNATURE_DESC *,D3D_ROOT_SIGNATURE_VERSION,ID3DBlob **,ID3DBlob **);
WIN_API HRESULT D3DCompile(const void *,SIZE_T,const char *,const D3D_SHADER_MACRO *,ID3DInclude *,const char *,const char *,UINT,UINT,ID3DBlob **,ID3DBlob **);

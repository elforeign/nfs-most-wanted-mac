"""C interface signature regression, autodiscovered by offline_tests.sh.

Original shim: signature oracle exit 1; corrected shim: exit 0. The original
one-argument handle call compiles with the old shim and is rejected after repair.
Every COM method used by image.c is checked, including pointer qualifiers.
No private fixtures, linking, or execution of Windows code is required.
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

# Windows SDK C interfaces: d3d12.h, dxgi1_2.h, dxgi.h and d3dcommon.h.
# Struct-returning descriptor accessors use an explicit return-value pointer.
SIGNATURES = r'''
#include "sdk_compile.h"
#define EXPECT(I, M, R, ...) \
    _Static_assert(_Generic(((I##Vtbl *)0)->M, R (*)(I *, ##__VA_ARGS__): 1, default: 0), #I "." #M)
EXPECT(ID3D12DescriptorHeap, GetCPUDescriptorHandleForHeapStart, void, D3D12_CPU_DESCRIPTOR_HANDLE *);
EXPECT(ID3D12DescriptorHeap, GetGPUDescriptorHandleForHeapStart, void, D3D12_GPU_DESCRIPTOR_HANDLE *);
EXPECT(ID3DBlob, GetBufferPointer, void *);
EXPECT(ID3DBlob, GetBufferSize, SIZE_T);
EXPECT(IDXGIFactory4, CreateSwapChainForHwnd, HRESULT, IUnknown *, HWND, const DXGI_SWAP_CHAIN_DESC1 *, const DXGI_SWAP_CHAIN_FULLSCREEN_DESC *, IDXGIOutput *, IDXGISwapChain1 **);
EXPECT(IDXGIFactory4, MakeWindowAssociation, HRESULT, HWND, UINT);
EXPECT(IDXGISwapChain1, GetBuffer, HRESULT, UINT, const GUID *, void **);
EXPECT(IDXGISwapChain1, Present, HRESULT, UINT, UINT);
EXPECT(ID3D12Device, CreateCommandQueue, HRESULT, const D3D12_COMMAND_QUEUE_DESC *, const GUID *, void **);
EXPECT(ID3D12Device, CreateDescriptorHeap, HRESULT, const D3D12_DESCRIPTOR_HEAP_DESC *, const GUID *, void **);
EXPECT(ID3D12Device, CreateRenderTargetView, void, ID3D12Resource *, const D3D12_RENDER_TARGET_VIEW_DESC *, D3D12_CPU_DESCRIPTOR_HANDLE);
EXPECT(ID3D12Device, CreateCommittedResource, HRESULT, const D3D12_HEAP_PROPERTIES *, D3D12_HEAP_FLAGS, const D3D12_RESOURCE_DESC *, D3D12_RESOURCE_STATES, const D3D12_CLEAR_VALUE *, const GUID *, void **);
EXPECT(ID3D12Device, CreateShaderResourceView, void, ID3D12Resource *, const D3D12_SHADER_RESOURCE_VIEW_DESC *, D3D12_CPU_DESCRIPTOR_HANDLE);
EXPECT(ID3D12Device, CreateRootSignature, HRESULT, UINT, const void *, SIZE_T, const GUID *, void **);
EXPECT(ID3D12Device, CreateGraphicsPipelineState, HRESULT, const D3D12_GRAPHICS_PIPELINE_STATE_DESC *, const GUID *, void **);
EXPECT(ID3D12Device, CreateCommandAllocator, HRESULT, D3D12_COMMAND_LIST_TYPE, const GUID *, void **);
EXPECT(ID3D12Device, CreateCommandList, HRESULT, UINT, D3D12_COMMAND_LIST_TYPE, ID3D12CommandAllocator *, ID3D12PipelineState *, const GUID *, void **);
EXPECT(ID3D12Device, CreateFence, HRESULT, UINT64, D3D12_FENCE_FLAGS, const GUID *, void **);
EXPECT(ID3D12CommandQueue, ExecuteCommandLists, void, UINT, ID3D12CommandList *const *);
EXPECT(ID3D12CommandQueue, Signal, HRESULT, ID3D12Fence *, UINT64);
EXPECT(ID3D12Resource, Map, HRESULT, UINT, const D3D12_RANGE *, void **);
EXPECT(ID3D12Resource, Unmap, void, UINT, const D3D12_RANGE *);
EXPECT(ID3D12Resource, GetGPUVirtualAddress, D3D12_GPU_VIRTUAL_ADDRESS);
EXPECT(ID3D12Fence, SetEventOnCompletion, HRESULT, UINT64, HANDLE);
EXPECT(ID3D12GraphicsCommandList, ResourceBarrier, void, UINT, const D3D12_RESOURCE_BARRIER *);
EXPECT(ID3D12GraphicsCommandList, CopyTextureRegion, void, const D3D12_TEXTURE_COPY_LOCATION *, UINT, UINT, UINT, const D3D12_TEXTURE_COPY_LOCATION *, const D3D12_BOX *);
EXPECT(ID3D12GraphicsCommandList, ClearRenderTargetView, void, D3D12_CPU_DESCRIPTOR_HANDLE, const float *, UINT, const D3D12_RECT *);
EXPECT(ID3D12GraphicsCommandList, OMSetRenderTargets, void, UINT, const D3D12_CPU_DESCRIPTOR_HANDLE *, BOOL, const D3D12_CPU_DESCRIPTOR_HANDLE *);
EXPECT(ID3D12GraphicsCommandList, SetGraphicsRootSignature, void, ID3D12RootSignature *);
EXPECT(ID3D12GraphicsCommandList, SetDescriptorHeaps, void, UINT, ID3D12DescriptorHeap *const *);
EXPECT(ID3D12GraphicsCommandList, SetGraphicsRootConstantBufferView, void, UINT, D3D12_GPU_VIRTUAL_ADDRESS);
EXPECT(ID3D12GraphicsCommandList, SetGraphicsRootDescriptorTable, void, UINT, D3D12_GPU_DESCRIPTOR_HANDLE);
EXPECT(ID3D12GraphicsCommandList, RSSetViewports, void, UINT, const D3D12_VIEWPORT *);
EXPECT(ID3D12GraphicsCommandList, RSSetScissorRects, void, UINT, const D3D12_RECT *);
EXPECT(ID3D12GraphicsCommandList, IASetVertexBuffers, void, UINT, UINT, const D3D12_VERTEX_BUFFER_VIEW *);
EXPECT(ID3D12GraphicsCommandList, IASetPrimitiveTopology, void, D3D12_PRIMITIVE_TOPOLOGY);
EXPECT(ID3D12GraphicsCommandList, DrawInstanced, void, UINT, UINT, UINT, UINT);
EXPECT(ID3D12GraphicsCommandList, Close, HRESULT);
#define RELEASE(I) EXPECT(I, Release, ULONG)
RELEASE(ID3DBlob); RELEASE(IDXGIFactory4); RELEASE(IDXGISwapChain1);
RELEASE(ID3D12Device); RELEASE(ID3D12CommandQueue); RELEASE(ID3D12CommandAllocator);
RELEASE(ID3D12GraphicsCommandList); RELEASE(ID3D12DescriptorHeap);
RELEASE(ID3D12RootSignature); RELEASE(ID3D12PipelineState); RELEASE(ID3D12Resource); RELEASE(ID3D12Fence);
'''


def main():
    compiler = shutil.which('clang')
    if not compiler:
        print('SKIP image SDK signatures: clang absent')
        return 3
    image = ROOT / 'runtime/win/image'
    with tempfile.TemporaryDirectory(prefix='image-sdk-') as temp:
        check = Path(temp) / 'check.c'
        command = [compiler, '-target', 'x86_64-pc-windows-msvc', '-std=c11',
                   '-ffreestanding', '-DRECOMP_WINDOWS_COMPILE_ONLY', '-Werror',
                   '-ferror-limit=1',
                   '-I', str(image), '-fsyntax-only', str(check)]
        check.write_text(SIGNATURES)
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[-2400:]
        for kind in ('CPU', 'GPU'):
            check.write_text('#include "sdk_compile.h"\n'
                             f'void bad(ID3D12DescriptorHeap *h) {{ h->lpVtbl->Get{kind}DescriptorHandleForHeapStart(h); }}\n')
            result = subprocess.run(command, capture_output=True, text=True)
            assert result.returncode != 0 and 'too few arguments' in result.stderr, result.stderr
        # Compile the actual caller against the checked declarations too.
        result = subprocess.run([*command[:-1], str(image / 'image.c')], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[-2400:]
    print('windows_image_sdk_test PASS: 38 method signatures + 12 Release; 2 old calls rejected; image.c compiles')
    return 0


if __name__ == '__main__':
    sys.exit(main())

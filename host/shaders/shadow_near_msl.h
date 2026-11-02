// Independent world receiver filter. Included by the translator implementation.
#pragma once
namespace sm {
struct NearProgram { uint64_t hash; int slot, tc, taps; };
static const NearProgram near_programs[] = {
    {0x629CD2E4C94C6BBCull,1,3,1}, // world t1
    {0xCC1387A106B0817Aull,1,3,4}, // world t2
    {0x92B6EAAEA4001456ull,1,3,16}, // world t3
    {0x58C4434A7033B3BAull,3,7,1}, // worldnormalmap t1
    {0xE5AF96AEE84674F6ull,3,7,4}, // worldnormalmap t2
    {0xD71DE6C5AA405EAAull,3,7,16}, // worldnormalmap t3
    {0xD2D9B9D538A06AEEull,4,7,1}, // worldreflect t1
    {0xCB861F5D715664A3ull,4,7,4}, // worldreflect t2
    {0x268CB5F65C872238ull,4,7,16}, // worldreflect t3
};
static const NearProgram *near_program(uint64_t hash) {
    for (const auto &p : near_programs) if (p.hash == hash) return &p;
    return nullptr;
}
static const char near_receiver_msl[] = R"MSL(
// Receiver ABI 2; buffer 28 / texture 30 are outside the guest/material slots.
struct NearShadowParams { float4 rows[4]; float4 usable, blend; uint mode, taps, pad1, pad2; };
static float4 near_shadow_coord(float4 far, constant NearShadowParams &k) {
    if (k.mode == 1u || k.mode == 5u) return float4(0.0f);
    float4 h=far.x*k.rows[0]+far.y*k.rows[1]+far.z*k.rows[2]+far.w*k.rows[3];
    if (!(abs(h.w)>1e-12f) || !all(isfinite(h))) return float4(0.0f);
    float3 p=h.xyz/h.w; // S2 includes centre alignment; divide exactly once.
    if (any(p.xy<k.usable.xy) || any(p.xy>k.usable.zw) || p.z<0.0f || p.z>1.0f) return float4(0.0f);
    float2 a=(p.xy-k.usable.xy)/(k.blend.xy-k.usable.xy);
    float2 b=(k.usable.zw-p.xy)/(k.usable.zw-k.blend.zw);
    float w=saturate(min(min(a.x,a.y),min(b.x,b.y))); w=w*w*(3.0f-2.0f*w);
    return float4(p,w);
}
static float2 near_shadow_eval(float4 far, constant NearShadowParams &k, depth2d<float> z) {
    float4 p=near_shadow_coord(far,k); if (!(p.w>0.0f)) return float2(1.0f,0.0f);
    constexpr sampler s(compare_func::less_equal, filter::linear, address::clamp_to_border, border_color::opaque_white);
    float v=0.0f;
    if (k.mode == 4u && k.taps > 1u) { // projection-only: legacy kernel in NEAR texels
        uint n=k.taps==4u?2u:4u;
        float2 pitch=1.0f/float2(z.get_width(),z.get_height());
        for(uint y=0;y<n;++y) for(uint x=0;x<n;++x)
            v+=z.sample_compare(s,p.xy+(float2(x,y)-float(n-1u)*.5f)*pitch,p.z);
        v/=float(n*n);
    } else v=z.sample_compare(s,p.xy,p.z);
    return float2(v,p.w);
}
)MSL";
// Return the per-component temporary dependencies of an instruction. Supported
// programs use componentwise arithmetic and projected texture coordinates here.
static void near_reads(const Instr &in, uint8_t output, uint8_t need[32]) {
    for (const auto &s : in.src) if (s.type==R_TEMP && s.num<32) {
        uint8_t mask=output;
        if(in.op==OP_TEX)mask=15;
        if(in.op==OP_RCP)mask=8;
        if(in.op==OP_DP3)mask=7;
        if(in.op==OP_DP4)mask=15;
        for(int c=0;c<4;++c)if(mask&(1<<c))need[s.num]|=1<<s.swz[c];
    }
}
static std::string near_comment(const Shader &sh, const Instr &in) {
    Shader one;one.ps=sh.ps;one.major=sh.major;one.minor=sh.minor;one.code.push_back(in);
    std::string d=disassemble(one);d=d.substr(d.find('\n')+1);
    while(!d.empty() && d.back()=='\n')d.pop_back();
    return "    // "+d+"\n";
}
static bool near_edit(std::string &s,const std::string &from,const std::string &to) {
    size_t at=s.find(from);if(at==s.npos)return false;s.replace(at,from.size(),to);return true;
}
static char *near_patch(const char *source,const uint32_t *tokens,size_t n,const SmResult &pc) {
    const NearProgram *p=near_program(pc.program_key);
    if(!p || pc.shader_type!=SM_PIXEL)return nullptr;
    bool hardware=false;
    for(int i=0;i<pc.n_samplers;++i)if(pc.samplers[i].reg==p->slot)hardware=pc.samplers[i].shadow;
    if(!hardware)return nullptr; // software comparison programs stay entirely retail
    Shader sh;if(!parse(tokens,n,sh))return nullptr;
    uint8_t taint[32]={};int compose=-1;Src value;
    for(size_t i=0;i<sh.code.size();++i){const auto &in=sh.code[i];
        if(!in.has_dst || in.dst.type!=R_TEMP || in.dst.num>=32)continue;
        bool fetch=in.op==OP_TEX && in.src.size()>1 && in.src[1].type==R_SAMPLER && (int)in.src[1].num==p->slot;
        uint8_t reads[32]={};near_reads(in,in.dst.mask,reads);bool dependent=false;
        for(int r=0;r<32;++r)dependent|=(reads[r]&taint[r])!=0;
        if(dependent && ((p->taps>1 && in.op==OP_MAD) || (p->taps==1 && in.op==OP_ADD))){
            compose=(int)i;value=in.src[0];break;
        }
        taint[in.dst.num]=(taint[in.dst.num]&~in.dst.mask)|((fetch||dependent)?in.dst.mask:0);
    }
    if(compose<0 || value.type!=R_TEMP)return nullptr;
    uint8_t need[32]={};need[value.num]=1<<value.swz[3];
    std::set<int> slice;int fetches=0;
    for(int i=compose-1;i>=0;--i){const auto &in=sh.code[i];
        if(!in.has_dst || in.dst.type!=R_TEMP || in.dst.num>=32)continue;
        uint8_t used=need[in.dst.num]&in.dst.mask;if(!used)continue;
        slice.insert(i);need[in.dst.num]&=~in.dst.mask;near_reads(in,used,need);
        if(in.op==OP_TEX)++fetches;
    }
    if(fetches!=p->taps)return nullptr;
    // Every slice live-out must be the filtered visibility only. Material
    // instructions interleaved with the original reductions remain in sm_body.
    uint8_t live[32]={};
    for(int i=(int)sh.code.size()-1;i>=0;--i){const auto &in=sh.code[i];
        if(!in.has_dst)continue;
        if(slice.count(i)) {
            uint8_t allowed=i==*slice.rbegin()?1<<value.swz[3]:0;
            if((live[in.dst.num]&in.dst.mask&~allowed)!=0)return nullptr;
            live[in.dst.num]&=~in.dst.mask;
        } else {
            if(in.dst.type==R_TEMP && in.dst.num<32)live[in.dst.num]&=~in.dst.mask;
            near_reads(in,in.dst.mask,live);
        }
    }
    std::string s=source, original;
    // Private register state in the far helper retains every old offset,
    // sample and reduction exactly; the near path never executes that block.
    size_t body=s.find("static void sm_body(");size_t first=s.find("    // ",body);
    if(body==s.npos || first==s.npos)return nullptr;
    size_t declarations=s.find("    const float4 ",body);
    if(declarations==s.npos || declarations>first)declarations=s.find("    float4 r",body);
    if(declarations==s.npos || declarations>first)return nullptr;
    std::string decl=s.substr(declarations,first-declarations);
    for(size_t at=0;at<decl.size();) {
        size_t end=decl.find('\n',at);if(end==decl.npos)break;
        std::string line=decl.substr(at,end-at+1);
        if(line.find("    const float4 k")==0 || line.find("    float4 r")==0)original+=line;
        at=end+1;
    }
    for(int i:slice){
        std::string comment=near_comment(sh,sh.code[i]);size_t at=s.find(comment,body);
        if(at==s.npos)return nullptr;
        size_t end=s.find('\n',at+comment.size());if(end==s.npos)return nullptr;++end;
        original+=s.substr(at,end-at);s.erase(at,end-at);
    }
    std::string scalar="r"+std::to_string(value.num)+"."+"xyzw"[value.swz[3]];
    std::string helper="static float near_retail_filter(thread const SmPSIn &si, constant float4 *c, constant SmPSAux &retail_aux, depth2d<float> tex"+std::to_string(p->slot)+", sampler smp"+std::to_string(p->slot)+") {\n"+original+"    (void)retail_aux; return "+scalar+"/"+std::to_string(p->taps)+".0f;\n}\n";
    size_t compose_at=s.find(near_comment(sh,sh.code[compose]),body);if(compose_at==s.npos)return nullptr;
    std::string far="si.tex["+std::to_string(p->tc)+"]";
    std::string call="near_retail_filter(si,c,aux,tex"+std::to_string(p->slot)+",smp"+std::to_string(p->slot)+")";
    std::string replacement="    float2 near_pair=near_shadow_eval("+far+",near_k,near_z);\n"
        "    float near_visibility;\n"
        "    if(near_k.mode==5u) {\n"
        "        constexpr sampler single(compare_func::less_equal,filter::linear,address::clamp_to_border,border_color::opaque_white);\n"
        "        near_visibility=tex"+std::to_string(p->slot)+".sample_compare(single,"+far+".xy/"+far+".w,"+far+".z/"+far+".w);\n"
        "    } else if(near_pair.y>=1.0f) near_visibility=near_pair.x;\n"
        "    else near_visibility=mix("+call+",near_pair.x,near_pair.y);\n"
        "    "+scalar+"=near_visibility*"+std::to_string(p->taps)+".0f;\n";
    s.insert(compose_at,replacement);
    if(!near_edit(s,"static void sm_body(",helper+"\nstatic void sm_body("))return nullptr;
    if(!near_edit(s,"using namespace metal;",std::string("using namespace metal;\n")+near_receiver_msl))return nullptr;
    if(!near_edit(s,"constant SmPSAux &aux,","constant SmPSAux &aux, constant NearShadowParams &near_k, depth2d<float> near_z,"))return nullptr;
    if(!near_edit(s,"constant SmPSAux &aux [[buffer(3)]]","constant SmPSAux &aux [[buffer(3)]], constant NearShadowParams &near_k [[buffer(28)]], depth2d<float> near_z [[texture(30)]]"))return nullptr;
    if(!near_edit(s,"sm_body(si, so, c, ci, cb, aux","sm_body(si, so, c, ci, cb, aux, near_k, near_z"))return nullptr;
    if(!near_edit(s,"    if (aux.alpha_func != 0u","    if (near_k.mode == 3u) so.oc[0].rgb=mix(so.oc[0].rgb,float3(0,1,0),.6f*near_shadow_coord("+far+",near_k).w);\n    if (aux.alpha_func != 0u"))return nullptr;
    return strdup(s.c_str());
}
} // namespace sm

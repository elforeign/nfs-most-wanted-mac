// Retail world receiver matching and the optional X360 shadow fade.
#pragma once
namespace sm {
struct ShadowEdgeProgram { uint64_t hash; int tc; };
static const ShadowEdgeProgram shadow_edge_programs[] = {
    {0x629CD2E4C94C6BBCull,3}, // world t1
    {0xCC1387A106B0817Aull,3}, // world t2
    {0x92B6EAAEA4001456ull,3}, // world t3
    {0x58C4434A7033B3BAull,7}, // worldnormalmap t1
    {0xE5AF96AEE84674F6ull,7}, // worldnormalmap t2
    {0xD71DE6C5AA405EAAull,7}, // worldnormalmap t3
    {0xD2D9B9D538A06AEEull,7}, // worldreflect t1
    {0xCB861F5D715664A3ull,7}, // worldreflect t2
    {0x268CB5F65C872238ull,7}, // worldreflect t3
};
static const ShadowEdgeProgram *shadow_edge_program(uint64_t hash) {
    for (const auto &p : shadow_edge_programs) if (p.hash == hash) return &p;
    return nullptr;
}
static std::string shadow_edge_comment(const Shader &sh, const Instr &in) {
    Shader one;one.ps=sh.ps;one.major=sh.major;one.minor=sh.minor;one.code.push_back(in);
    std::string d=disassemble(one);d=d.substr(d.find('\n')+1);
    while(!d.empty() && d.back()=='\n')d.pop_back();
    return "    // "+d+"\n";
}
// The original projected-Y fade is 1-5v; the selected shadow fit uses 1-4v.
// Keep both formulas in one compiled shader so switching fits needs no compile.
// This edits visibility composition only, leaving the original far fit/filter.
static char *shadow_edge_patch(const char *source,const uint32_t *tokens,size_t n,const SmResult &pc) {
    const ShadowEdgeProgram *p=shadow_edge_program(pc.program_key);if(!p || pc.shader_type!=SM_PIXEL)return nullptr;
    Shader sh;if(!parse(tokens,n,sh))return nullptr;
    for(size_t i=0;i<sh.code.size();++i) {
        const auto &mad=sh.code[i];
        if(mad.op!=OP_MAD || !mad.has_dst || mad.dst.type!=R_TEMP || mad.src.size()!=3 ||
           mad.src[0].type!=R_ADDR || mad.src[0].num!=(unsigned)p->tc || mad.src[0].swz[0]!=1 || mad.src[1].mod!=SM_NEG)continue;
        for(size_t j=i+1;j<sh.code.size();++j) {
            const auto &mul=sh.code[j];
            if(mul.op!=OP_MUL || !mul.dst.sat || mul.src.size()!=2 || mul.src[0].type!=R_TEMP ||
               mul.src[0].num!=mad.dst.num || mul.dst.type!=R_TEMP)continue;
            if((mul.dst.mask&(mul.dst.mask-1))!=0)return nullptr;
            int component=0;while(!(mul.dst.mask&(1<<component)))++component;
            std::string s=source,comment=shadow_edge_comment(sh,mul);
            size_t at=s.find(comment);if(at==s.npos)return nullptr;
            size_t end=s.find('\n',at+comment.size());if(end==s.npos)return nullptr;
            std::string value="r"+std::to_string(mul.dst.num)+"."+"xyzw"[component];
            std::string tc="si.tex["+std::to_string(p->tc)+"]";
            s.insert(end+1,"    if (aux.pad1 & 1u) "+value+"=saturate(1.0f-4.0f*("+tc+".y/"+tc+".w)); // projected shadow edge\n");
            return strdup(s.c_str());
        }
    }
    return nullptr;
}
}

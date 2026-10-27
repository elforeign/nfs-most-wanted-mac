import struct,sys,math,os,csv
import numpy as np
COEF=[(0,0),(0.9375,0),(1.796875,-0.8125),(1.53125,-0.859375)]
def dec(path):
    d=open(path,'rb').read()
    assert d[:6]==b'Gnsu20'
    n,rate=struct.unpack_from('<II',d,0x18)
    fr=(n+31)//32; start=len(d)-fr*0x13
    out=np.zeros(fr*32,np.int32); k=0
    for f in range(fr):
        o=start+f*0x13
        h=struct.unpack_from('<I',d,o)[0]
        c1,c2=COEF[h&3]
        h2=np.int16((h&0xFFF0)&0xFFFF).item() if False else ((h&0xFFF0)^0x8000)-0x8000
        h1=(((h>>16)&0xFFF0)^0x8000)-0x8000
        sh=(h>>16)&0xF
        out[k]=h2; out[k+1]=h1; k+=2
        for i in range(30):
            b=d[o+4+i//2]
            nib=(b>>4) if i%2==0 else (b&0xF)
            if nib>7: nib-=16
            s=(nib<<(20-sh)) + int(h1*c1*256.0 + h2*c2*256.0)
            s>>=8
            s=max(-32768,min(32767,s))
            out[k]=s; k+=1; h2=h1; h1=s
    return out[:n].astype(np.float64),rate
def stats(x):
    rms=math.sqrt(np.mean(x*x)); dif=math.sqrt(np.mean(np.diff(x)**2))
    return rms,dif,np.max(np.abs(x))
pc,xb=sys.argv[1],sys.argv[2]
names=sys.argv[3:]
for nm in names:
    ca=[f for f in os.listdir(pc) if f.lower()==nm.lower()]; cand=[f for f in os.listdir(xb) if f.lower()==nm.lower()]
    if not ca or not cand: print('MISSING',nm); continue
    a=os.path.join(pc,ca[0]); b=os.path.join(xb,cand[0])
    xa,ra=dec(a); xx,rx=dec(b)
    sa=stats(xa); sx=stats(xx)
    print('%-34s PC rms %7.0f peak %6.0f diff/rms %.2f | X360 rms %7.0f peak %6.0f diff/rms %.2f | X/PC %+.2f dB'%(nm,sa[0],sa[2],sa[1]/sa[0],sx[0],sx[2],sx[1]/sx[0],20*math.log10(sx[0]/sa[0])))

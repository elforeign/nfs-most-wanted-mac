"""Reference (Python) MPF v5 X360 -> PC music conversion, the oracle runtime/x360music_test.py compares the C
converter against; never changes source files.
Packed field widths are validated against every corresponding PC node/action.
Not a generic MPF converter: accepts only this exact retail layout and opcodes.
"""
from pathlib import Path
import struct,collections,json
# Inputs are set by the caller (runtime/x360music_test.py): the PC and X360 MW_Music.mpf, and the output path.
PC=X=None
OUT=Path('MW_Music_x360_pc.mpf')
NODE_WIDTHS=((16,5,6,5),(12,5,3,4,8),(16,9,2,5),(24,8))
ACTION_WIDTHS=(8,7,1,2,2,3,3,6)
ARG_WIDTHS={1:(16,16),2:(16,16),4:(16,8,1,7),5:(8,7,1,16),8:(16,8,8),9:(24,8),13:(16,16),14:(8,8,16)}
def repack(x,widths):
 assert sum(widths)==32
 result=0;bit=0
 for width in widths:
  result|=((x>>(32-bit-width))&((1<<width)-1))<<bit;bit+=width
 return result

def read(f,offset,n):f.seek(offset);b=f.read(n);assert len(b)==n;return b

def main():
 with X.open('rb') as src,OUT.open('w+b') as dst:
  while True:
   b=src.read(4096)
   if not b:break
   dst.write(b)
  def transform(off,widths=(32,)):
   val=int.from_bytes(read(src,off,4),'big');dst.seek(off);dst.write(repack(val,widths).to_bytes(4,'little'))
  def swap16(off):dst.seek(off);dst.write(read(src,off,2)[::-1])
  assert read(src,0,8)==bytes.fromhex('504644780501b001')
  assert struct.unpack('>10I',read(src,0x14,40))==(0x48,0x1d0c,0x178c0,0x1794c,0x1a8ac,0x1a910,0x1adf4,0x1adf8,0x1ae0c,0x213d4)
  transform(0);swap16(0x12)
  for off in range(0x14,0x3c,4):transform(off)
  nn=int.from_bytes(read(src,0x12,2),'big');assert nn==3681
  no=[int.from_bytes(read(src,0x48+i*2,2),'big')*4 for i in range(nn)]+[0x178c0]
  for i in range(nn):swap16(0x48+i*2)
  for off,end in zip(no,no[1:]):
   for j,widths in enumerate(NODE_WIDTHS):transform(off+j*4,widths)
   for pos in range(off+16,end,4):transform(pos,(8,8,16))
  ne=read(src,0xf,1)[0];assert ne==70
  eo=[int.from_bytes(read(src,0x178c0+i*2,2),'big')*4 for i in range(ne)]+[0x1a8ac]
  for i in range(ne):swap16(0x178c0+i*2)
  hist=collections.Counter()
  for off,end in zip(eo,eo[1:]):
   assert (end-off-20)%12==0
   for j in range(5):transform(off+j*4,(24,8) if j==3 else (32,))
   for pos in range(off+20,end,12):
    transform(pos)
    action=repack(int.from_bytes(read(src,pos+4,4),'big'),ACTION_WIDTHS);opcode=(action>>8)&127
    assert opcode in ARG_WIDTHS,opcode
    hist[opcode]+=1;transform(pos+4,ACTION_WIDTHS);transform(pos+8,ARG_WIDTHS[opcode])
  for off in range(0x1a910,0x1adf8,4):transform(off)
  for off in range(0x1ae0c,0x213d4,4):transform(off)
  dst.flush()
  diff_before=0;diff_table_words=0;duration_diff=0
  with PC.open('rb') as pc:
   for off in range(0,0x1ae0c,4):
    a=read(pc,off,4);b=read(dst,off,4);diff_before+=sum(i!=j for i,j in zip(a,b))
   for off in range(0x1ae0c,0x213d4,8):
    a=struct.unpack('<II',read(pc,off,8));b=struct.unpack('<II',read(dst,off,8));diff_table_words+=a[0]!=b[0];duration_diff+=a[1]!=b[1]
  result={'output':str(OUT),'size':OUT.stat().st_size,'nodes':nn,'events':ne,'commands':sum(hist.values()),'opcode_counts':dict(hist),'graph_byte_differences_from_pc':diff_before,'segment_offset_differences':diff_table_words,'segment_duration_differences':duration_diff}
  assert diff_before==0 and diff_table_words==3256 and duration_diff==0,result
  Path('mpf_conversion_result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':main()

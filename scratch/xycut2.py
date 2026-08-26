import fitz,numpy as np,cv2,io,sys
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8')
DPI=100; MIN_GUT=int(0.55*DPI); MIN_SIDE=int(1.5*DPI)
d=fitz.open('Skanksa.pdf')

def sheet_ink(p):
    pm=p.get_pixmap(dpi=DPI,colorspace=fitz.csGRAY)
    g=np.frombuffer(pm.samples,np.uint8).reshape(pm.height,pm.width)
    B=(g<235).astype(np.uint8); L=int(6.0*DPI)
    rules=cv2.dilate(
        cv2.morphologyEx(B,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(L,1)))|
        cv2.morphologyEx(B,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(1,L))),
        np.ones((5,5),np.uint8))
    return B&(1-rules)

def widest_gap(prof,noise=0):
    z=prof<=noise; best=(0,None); i=0
    while i<len(z):
        if z[i]:
            j=i
            while j<len(z) and z[j]: j+=1
            if i>0 and j<len(z) and j-i>best[0]: best=(j-i,(i+j)//2)
            i=j
        else: i+=1
    return best

def xycut(C,x,y,w,h,depth=0,out=None):
    out=[] if out is None else out
    sub=C[y:y+h,x:x+w]
    if sub.sum()==0: return out
    if depth<6 and w>2*MIN_SIDE and h>2*MIN_SIDE:
        nz=max(2,int(0.004*max(w,h)));
        gv,cv_=widest_gap(sub.sum(0),nz); gh,ch=widest_gap(sub.sum(1),nz)
        cand=[]
        if gv>=MIN_GUT and MIN_SIDE<cv_<w-MIN_SIDE: cand.append((gv,'v',cv_))
        if gh>=MIN_GUT and MIN_SIDE<ch<h-MIN_SIDE: cand.append((gh,'h',ch))
        if cand:
            g,ax,c=max(cand)
            if ax=='v':
                xycut(C,x,y,c,h,depth+1,out); xycut(C,x+c,y,w-c,h,depth+1,out)
            else:
                xycut(C,x,y,w,c,depth+1,out); xycut(C,x,y+c,w,h-c,depth+1,out)
            return out
    ys,xs=np.nonzero(sub)
    out.append((x+xs.min(),y+ys.min(),int(xs.max()-xs.min())+1,int(ys.max()-ys.min())+1,int(sub.sum())))
    return out

def caption(p,M,x,y,w,h):
    best=None
    for b in p.get_text('dict')['blocks']:
        if b['type']!=0: continue
        for l in b['lines']:
            for s in l['spans']:
                t=s['text'].strip()
                if not t or s['size']<13: continue
                r=fitz.Rect(s['bbox'])*M; cx,cy=r.x0/72,r.y0/72
                if x/DPI-1<cx<(x+w)/DPI+1 and (y+h)/DPI-1.2<cy<(y+h)/DPI+3.2:
                    if best is None or s['size']>best[0]: best=(s['size'],t)
    return best[1] if best else ''

def textdens(p,M,x,y,w,h):
    n=0
    for b in p.get_text('dict')['blocks']:
        if b['type']!=0: continue
        for l in b['lines']:
            for s in l['spans']:
                r=fitz.Rect(s['bbox'])*M
                if x<=r.x0/72*DPI<=x+w and y<=r.y0/72*DPI<=y+h: n+=len(s['text'].strip())
    return n/max((w/DPI)*(h/DPI),.01)

for pno in (25,3,4,13,23):
    p=d[pno]; M=p.rotation_matrix; C=sheet_ink(p)
    rs=[r for r in xycut(C,0,0,C.shape[1],C.shape[0]) if r[2]>MIN_SIDE and r[3]>MIN_SIDE]
    rs.sort(key=lambda r:-r[2]*r[3])
    print(f'\n=== PDF page {pno+1} ===')
    for x,y,w,h,ink in rs[:8]:
        td=textdens(p,M,x,y,w,h); fill=ink/(w*h)
        kind='NOTES' if td>150 else ('DRAWING' if fill>0.02 else '?')
        print(f'  {w/DPI:5.1f}x{h/DPI:5.1f}in @({x/DPI:5.1f},{y/DPI:5.1f}) fill={fill:.3f} txt={td:6.0f} {kind:7s} "{caption(p,M,x,y,w,h)}"')

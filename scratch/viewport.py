import fitz,numpy as np,cv2,io,sys,re
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8')
DPI=100; d=fitz.open('Skanksa.pdf')

def regions(pno, gutter_in=0.35):
    p=d[pno]; pm=p.get_pixmap(dpi=DPI,colorspace=fitz.csGRAY)
    g=np.frombuffer(pm.samples,np.uint8).reshape(pm.height,pm.width)
    B=(g<235).astype(np.uint8)
    k=int(gutter_in*DPI)                       # bridge gaps smaller than a gutter
    blob=cv2.dilate(B,np.ones((k,k),np.uint8))
    n,lab,st,cen=cv2.connectedComponentsWithStats(blob,8)
    out=[]
    for i in range(1,n):
        x,y,w,h,a=st[i]
        if w<0.06*pm.width or h<0.06*pm.height: continue
        m=(lab[y:y+h,x:x+w]==i)
        ink=B[y:y+h,x:x+w][m].sum()
        out.append(dict(px=(x,y,w,h), ink=int(ink), fill=ink/max(m.sum(),1)))
    return p,pm,out

def textstats(p,pm,r):
    x,y,w,h=r['px']; S=72.0/DPI
    R=fitz.Rect(x*S,y*S,(x+w)*S,(y+h)*S)
    chars=0; big=[]
    for b in p.get_text('dict',clip=R)['blocks']:
        if b['type']!=0: continue
        for l in b['lines']:
            for s in l['spans']:
                t=s['text'].strip()
                chars+=len(t)
                if s['size']>=20 and t: big.append((round(s['size'],1),t))
    area_in2=(w/DPI)*(h/DPI)
    return chars, chars/max(area_in2,.01), big

for pno in (3,4):
    p,pm,rs=regions(pno)
    print(f'\n=== PDF page {pno+1} ===  sheet {pm.width}x{pm.height}px @{DPI}dpi')
    rs.sort(key=lambda r:-r['px'][2]*r['px'][3])
    for r in rs:
        x,y,w,h=r['px']; chars,dens,big=textstats(p,pm,r)
        cap=[t for sz,t in big][:4]
        kind='TEXT/TABLE' if dens>120 else ('DRAWING' if r['fill']>0.02 else 'sparse')
        print(f"  {w/DPI:5.1f}x{h/DPI:5.1f}in at ({x/DPI:5.1f},{y/DPI:5.1f})  inkfill={r['fill']:.3f} "
              f"textdens={dens:6.1f}/in2 -> {kind:11s} {cap}")

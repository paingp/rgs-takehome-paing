import fitz,numpy as np,cv2,io,sys
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8')
DPI=100; d=fitz.open('Skanksa.pdf')
LONG=int(6.0*DPI)          # >6in of straight run = sheet border / title-block rule, not content

def segment(pno, gutter_in=0.30):
    p=d[pno]; pm=p.get_pixmap(dpi=DPI,colorspace=fitz.csGRAY)
    g=np.frombuffer(pm.samples,np.uint8).reshape(pm.height,pm.width)
    B=(g<235).astype(np.uint8)
    rules=cv2.dilate(
        cv2.morphologyEx(B,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(LONG,1)))|
        cv2.morphologyEx(B,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(1,LONG))),
        np.ones((5,5),np.uint8))
    C=B&(1-rules)
    k=int(gutter_in*DPI)
    n,lab,st,_=cv2.connectedComponentsWithStats(cv2.dilate(C,np.ones((k,k),np.uint8)),8)
    out=[]
    for i in range(1,n):
        x,y,w,h,_=st[i]
        if w*h < (1.2*DPI)**2: continue
        x,y=x+k//2,y+k//2; w,h=max(w-k,1),max(h-k,1)          # undo dilation halo
        out.append((x,y,w,h,int(C[y:y+h,x:x+w].sum())))
    return p,pm,out

def caption(p,x,y,w,h):
    S=72.0/DPI; R=fitz.Rect(x*S,y*S,(x+w)*S,(y+h)*S)
    chars=0; big=[]
    for b in p.get_text('dict',clip=R)['blocks']:
        if b['type']!=0: continue
        for l in b['lines']:
            for s in l['spans']:
                t=s['text'].strip(); chars+=len(t)
                if s['size']>=19 and t: big.append((round(s['size'],1),t))
    big.sort(reverse=True)
    return chars, [t for _,t in big][:5]

for pno in (3,4,25):
    p,pm,rs=segment(pno)
    print(f'\n=== PDF page {pno+1} ===')
    rs.sort(key=lambda r:-r[2]*r[3])
    for x,y,w,h,ink in rs[:9]:
        chars,big=caption(p,x,y,w,h)
        a=(w/DPI)*(h/DPI); dens=chars/max(a,.01); fill=ink/max(w*h,1)
        kind='TEXT'if dens>150 else('DRAWING'if fill>0.03 else'?')
        print(f'  {w/DPI:5.1f}x{h/DPI:5.1f}in @({x/DPI:5.1f},{y/DPI:5.1f}) fill={fill:.3f} txt={dens:6.0f}/in2 {kind:7s} {big}')

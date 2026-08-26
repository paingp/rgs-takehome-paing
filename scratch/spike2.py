import fitz,numpy as np,cv2
DPI=300;S=DPI/72.
d=fitz.open('Skanksa.pdf');p=d[25];PLAN=fitz.Rect(108,108,1656,1008)
pm=p.get_pixmap(dpi=DPI,clip=PLAN,colorspace=fitz.csGRAY)
img=np.frombuffer(pm.samples,np.uint8).reshape(pm.height,pm.width); ink=255-img
cx,cy,h=596.15,260.64,7.0
x0=int((cx-h-PLAN.x0)*S);y0=int((cy-h-PLAN.y0)*S);w=int(2*h*S)
T=ink[y0:y0+w,x0:x0+w].copy()

def patch(px,py):   # centred raster patch same size as T
    return ink[py-w//2:py-w//2+w, px-w//2:px-w//2+w].copy()

cands={'duplex(minus)':(2781,1015),'plus_symbol':(2605,1257)}
for n,(px,py) in cands.items():
    cv2.imwrite(f'scratch/c_{n}.png',255-patch(px,py))

def variants(T):
    out=[]
    for mir in (0,1):
        B=cv2.flip(T,1) if mir else T
        for a in range(0,360,15):
            M=cv2.getRotationMatrix2D((B.shape[1]/2-.5,B.shape[0]/2-.5),a,1.)
            out.append(cv2.warpAffine(B,M,B.shape[::-1],borderValue=0))
    return out
V=variants(T)
Tb=(T>25).astype(np.uint8); Vb=[(v>25).astype(np.uint8) for v in V]

def scores(P):
    Pb=(P>25).astype(np.uint8)
    ncc=max(float(cv2.matchTemplate(P.astype(np.float32),v.astype(np.float32),cv2.TM_CCOEFF_NORMED)[0,0]) for v in V)
    # ink IoU with 1px dilation tolerance
    Pd=cv2.dilate(Pb,np.ones((3,3),np.uint8))
    best=0
    for vb in Vb:
        vd=cv2.dilate(vb,np.ones((3,3),np.uint8))
        inter=((Pd&vb).sum()+(Pb&vd).sum())/2.
        union=(Pb|vb).sum()+1e-9
        best=max(best,inter/union)
    return ncc,best

print(f'{"patch":16s} {"maxNCC":>8s} {"inkIoU":>8s}')
for n,(px,py) in cands.items():
    a,b=scores(patch(px,py)); print(f'{n:16s} {a:8.3f} {b:8.3f}')
a,b=scores(T); print(f'{"self":16s} {a:8.3f} {b:8.3f}')

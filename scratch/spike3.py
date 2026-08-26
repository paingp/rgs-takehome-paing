import fitz,numpy as np,cv2
DPI=300;S=DPI/72.
d=fitz.open('Skanksa.pdf');p=d[25];PLAN=fitz.Rect(108,108,1656,1008)
pm=p.get_pixmap(dpi=DPI,clip=PLAN,colorspace=fitz.csGRAY)
ink=255-np.frombuffer(pm.samples,np.uint8).reshape(pm.height,pm.width)
cx,cy,h=596.15,260.64,7.0
x0=int((cx-h-PLAN.x0)*S);y0=int((cy-h-PLAN.y0)*S);w=int(2*h*S)
T=ink[y0:y0+w,x0:x0+w].copy()
def variants(T):
    o=[]
    for mir in(0,1):
        B=cv2.flip(T,1) if mir else T
        for a in range(0,360,15):
            M=cv2.getRotationMatrix2D((B.shape[1]/2-.5,)*2,a,1.)
            o.append((a,mir,cv2.warpAffine(B,M,B.shape[::-1],borderValue=0)))
    return o
V=variants(T); K=np.ones((3,3),np.uint8)
def sym_iou(Pb,vb):
    inter=((cv2.dilate(Pb,K)&vb).sum()+(Pb&cv2.dilate(vb,K)).sum())/2.
    return inter/((Pb|vb).sum()+1e-9)
def evaluate(px,py,name,R=8):
    win=ink[py-w//2-R:py-w//2+w+R, px-w//2-R:px-w//2+w+R].astype(np.float32)
    bn=(-9,None,None)
    for a,m,v in V:
        r=cv2.matchTemplate(win,v.astype(np.float32),cv2.TM_CCOEFF_NORMED)
        i=np.unravel_index(r.argmax(),r.shape)
        if r[i]>bn[0]: bn=(float(r[i]),(a,m,v),i)
    s,(a,m,v),(dy,dx)=bn
    P=ink[py-w//2-R+dy:py-w//2-R+dy+w, px-w//2-R+dx:px-w//2-R+dx+w]
    Pb=(P>25).astype(np.uint8); vb=(v>25).astype(np.uint8)
    cov_t=(cv2.dilate(Pb,K)&vb).sum()/max(vb.sum(),1)      # template explained by patch
    cov_p=(Pb&cv2.dilate(vb,K)).sum()/max(Pb.sum(),1)      # patch explained by template
    print(f'{name:22s} NCC={s:.3f} rot={a:3d} mir={m}  tmpl_covered={cov_t:.3f} patch_explained={cov_p:.3f} symIoU={sym_iou(Pb,vb):.3f}')
for n,(px,py) in {'duplex ⊖ (rot90)':(2781,1015),'plus ⊕ (other class)':(2605,1257)}.items():
    evaluate(px,py,n)
Tb=(T>25).astype(np.uint8); print(f'{"self":22s} NCC=1.000            tmpl_covered=1.000 patch_explained=1.000 symIoU=1.000')

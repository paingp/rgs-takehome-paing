import fitz,numpy as np,cv2,io,sys
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8')
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
V=variants(T)
TOL=2.0
def prep(v):
    vb=(v>25).astype(np.uint8)
    dt=cv2.distanceTransform(1-vb,cv2.DIST_L2,3)      # dist to template ink
    ys,xs=np.nonzero(vb); c=np.array([xs.mean(),ys.mean()])
    rad=np.sqrt(((np.stack([xs,ys],1)-c)**2).sum(1)).max()
    Y,X=np.mgrid[0:vb.shape[0],0:vb.shape[1]]
    disc=(((X-c[0])**2+(Y-c[1])**2)<=(rad*0.85)**2).astype(np.uint8)   # core support
    return vb,dt,disc
PV=[(a,m,v)+prep(v) for a,m,v in V]
def ev(px,py,name,R=8):
    win=ink[py-w//2-R:py-w//2+w+R, px-w//2-R:px-w//2+w+R].astype(np.float32)
    bn=None
    for a,m,v,vb,dt,disc in PV:
        r=cv2.matchTemplate(win,v.astype(np.float32),cv2.TM_CCOEFF_NORMED)
        i=np.unravel_index(r.argmax(),r.shape)
        if bn is None or r[i]>bn[0]: bn=(float(r[i]),a,m,vb,dt,disc,i)
    s,a,m,vb,dt,disc,(dy,dx)=bn
    Pb=(ink[py-w//2-R+dy:py-w//2-R+dy+w, px-w//2-R+dx:px-w//2-R+dx+w]>25).astype(np.uint8)
    dtP=cv2.distanceTransform(1-Pb,cv2.DIST_L2,3)
    recall=(dtP[vb>0]<=TOL).mean()                        # template strokes found in patch
    core=Pb&disc
    prec=(dt[core>0]<=TOL).mean() if core.sum() else 0.0  # patch ink in core explained by template
    print(f'{name:26s} NCC={s:.3f} rot={a:3d} mir={m}  recall={recall:.3f} core_precision={prec:.3f} F1={2*recall*prec/max(recall+prec,1e-9):.3f}')
for n,(px,py) in {'duplex (TARGET, rot90)':(2781,1015),'plus/quad (OTHER class)':(2605,1257)}.items(): ev(px,py,n)

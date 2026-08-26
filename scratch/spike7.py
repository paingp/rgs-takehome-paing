import fitz,numpy as np,cv2,io,sys,time
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8')
DPI=300;S=DPI/72.
d=fitz.open('Skanksa.pdf');p=d[25];PLAN=fitz.Rect(108,108,1656,1008)
pm=p.get_pixmap(dpi=DPI,clip=PLAN,colorspace=fitz.csGRAY)
ink=255-np.frombuffer(pm.samples,np.uint8).reshape(pm.height,pm.width)
B=(ink>25).astype(np.uint8); L=int(0.30*DPI)
lines=cv2.dilate(cv2.morphologyEx(B,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(L,1)))|
                 cv2.morphologyEx(B,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(1,L))),np.ones((3,3),np.uint8))
sym=B&(1-lines)
cx,cy,h=596.15,260.64,7.0
x0=int((cx-h-PLAN.x0)*S);y0=int((cy-h-PLAN.y0)*S);w=int(2*h*S)
T=sym[y0:y0+w,x0:x0+w].copy()
def variants(T):
    o=[]
    for mir in(0,1):
        Bv=cv2.flip(T,1) if mir else T
        for a in range(0,360,15):
            M=cv2.getRotationMatrix2D((Bv.shape[1]/2-.5,)*2,a,1.)
            o.append((a,mir,(cv2.warpAffine(Bv*255,M,Bv.shape[::-1],borderValue=0)>90).astype(np.uint8)))
    return o
V=[(a,m,v,cv2.distanceTransform(1-v,cv2.DIST_L2,3)) for a,m,v in variants(T) if v.sum()>0]
TOL=2.0
def ev(px,py,name,R=10):
    best=None
    for dy in range(-R,R+1):
        for dx in range(-R,R+1):
            Pb=sym[py-w//2+dy:py-w//2+dy+w, px-w//2+dx:px-w//2+dx+w]
            if Pb.shape!=(w,w) or Pb.sum()==0: continue
            dtP=cv2.distanceTransform(1-Pb,cv2.DIST_L2,3)
            for a,m,v,dtT in V:
                rec=(dtP[v>0]<=TOL).mean()
                pre=(dtT[Pb>0]<=TOL).mean()
                f=2*rec*pre/max(rec+pre,1e-9)
                if best is None or f>best[0]: best=(f,rec,pre,a,m)
    f,rec,pre,a,m=best
    print(f'{name:26s} F1={f:.3f}  recall={rec:.3f} precision={pre:.3f} rot={a:3d} mir={m}')
t=time.time()
for n,(px,py) in {'duplex (TARGET, rot90)':(2781,1015),'plus/quad (OTHER class)':(2605,1257),
                  'door arc (OTHER class)':(2560,1000)}.items(): ev(px,py,n)
print('%.1fs'%(time.time()-t))

import fitz, numpy as np, cv2, time

DPI=300; S=DPI/72.0
d=fitz.open('Skanksa.pdf'); p=d[25]
PLAN=fitz.Rect(108,108,1656,1008)          # plan area in PDF points
pm=p.get_pixmap(dpi=DPI, clip=PLAN, colorspace=fitz.csGRAY)
img=np.frombuffer(pm.samples,np.uint8).reshape(pm.height,pm.width)
print('raster',img.shape, 'MP=%.1f'%(img.size/1e6))

ink=255-img                                 # ink-positive
print('ink coverage %.3f%%'%(100*(ink>40).mean()))

# template: duplex receptacle found at PDF pt (596.15,260.64), half-box 7pt
cx,cy=596.15,260.64; h=7.0
x0=int((cx-h-PLAN.x0)*S); y0=int((cy-h-PLAN.y0)*S); w=int(2*h*S)
T=ink[y0:y0+w, x0:x0+w].copy()
cv2.imwrite('scratch/T.png',255-T)
print('template',T.shape,'inkfrac %.3f'%((T>40).mean()))

def rots(T):
    out=[]
    for mirror in (False,True):
        B=cv2.flip(T,1) if mirror else T
        for a in range(0,360,15):
            M=cv2.getRotationMatrix2D((B.shape[1]/2-.5,B.shape[0]/2-.5),a,1.0)
            out.append((a,mirror,cv2.warpAffine(B,M,B.shape[::-1],flags=cv2.INTER_LINEAR,borderValue=0)))
    return out

variants=rots(T)
t=time.time()
best=np.full(( img.shape[0]-T.shape[0]+1, img.shape[1]-T.shape[1]+1), -1, np.float32)
bestang=np.zeros_like(best,np.int16)
for i,(a,m,V) in enumerate(variants):
    r=cv2.matchTemplate(ink,V,cv2.TM_CCOEFF_NORMED)
    upd=r>best; best[upd]=r[upd]; bestang[upd]=a*(1 if not m else -1)
print('matchTemplate x%d in %.1fs'%(len(variants),time.time()-t))

def nms(score,thr,rad):
    ys,xs=np.where(score>=thr)
    v=score[ys,xs]; o=np.argsort(-v)
    keep=[]; taken=np.zeros(len(o),bool)
    pts=np.stack([xs[o],ys[o]],1); vv=v[o]
    used=np.zeros(score.shape,np.uint8)
    for (x,y),s in zip(pts,vv):
        if used[max(0,y-rad):y+rad, max(0,x-rad):x+rad].any(): continue
        used[y,x]=1; keep.append((x,y,s))
    return keep

for thr in (0.5,0.6,0.65,0.7,0.75,0.8):
    k=nms(best,thr,int(T.shape[0]*0.6))
    print('thr %.2f -> %d detections'%(thr,len(k)))

k=nms(best,0.65,int(T.shape[0]*0.6))
vis=cv2.cvtColor(img,cv2.COLOR_GRAY2BGR)
for x,y,s in k:
    cv2.rectangle(vis,(x,y),(x+T.shape[1],y+T.shape[0]),(0,0,255),3)
cv2.imwrite('scratch/spike_overlay.png',vis)
np.save('scratch/best.npy',best)
print('scores of top 40:', [round(float(s),3) for _,_,s in k[:40]])

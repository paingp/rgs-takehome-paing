import fitz,numpy as np,cv2,io,sys,time
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8')
DPI=300;S=DPI/72.
d=fitz.open('Skanksa.pdf');p=d[25];PLAN=fitz.Rect(108,108,1656,1008)
pm=p.get_pixmap(dpi=DPI,clip=PLAN,colorspace=fitz.csGRAY)
ink=255-np.frombuffer(pm.samples,np.uint8).reshape(pm.height,pm.width)
B=(ink>25).astype(np.uint8)
t=time.time()
L=int(0.30*DPI)          # 0.30in ~ 3x symbol size: anything this long is structure, not symbol
hor=cv2.morphologyEx(B,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(L,1)))
ver=cv2.morphologyEx(B,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(1,L)))
lines=cv2.dilate(hor|ver,np.ones((3,3),np.uint8))
sym=B&(1-lines)
print('line-removal %.1fs  ink px %d -> %d (%.0f%% removed)'%(time.time()-t,B.sum(),sym.sum(),100*(1-sym.sum()/B.sum())))
n,lab,stats,cen=cv2.connectedComponentsWithStats(sym,8)
areas=stats[1:,cv2.CC_STAT_AREA]; wI=stats[1:,cv2.CC_STAT_WIDTH]; hI=stats[1:,cv2.CC_STAT_HEIGHT]
mx=np.maximum(wI,hI)
print('components:',n-1)
for lo,hi in [(0,8),(8,16),(16,32),(32,64),(64,128),(128,10**9)]:
    print(f'  max-dim {lo:4d}-{hi if hi<10**8 else "inf"}: {int(((mx>=lo)&(mx<hi)).sum())}')
cv2.imwrite('scratch/sym_only.png',255-sym*255)
cv2.imwrite('scratch/sym_crop.png',255-sym[900:1500,2300:3500]*255)

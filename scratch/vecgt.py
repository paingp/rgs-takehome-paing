import pymupdf, io, sys, math
from collections import defaultdict
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8')

MAXDIM = 16.0        # pt — anything bigger is structure (walls, leaders), not a symbol primitive
GAP    = 1.2         # pt — paths this close belong to the same symbol

def page_prims(p):
    M=p.rotation_matrix; out=[]
    for dr in p.get_drawings():
        r=pymupdf.Rect(dr['rect'])*M
        if max(r.width,r.height) > MAXDIM or not dr['items']: continue
        types=''.join(sorted(it[0] for it in dr['items']))
        out.append((r, types, round(max(r.width,r.height),1), round(min(r.width,r.height),1)))
    return out

def cluster(prims):
    par=list(range(len(prims)))
    def find(a):
        while par[a]!=a: par[a]=par[par[a]]; a=par[a]
        return a
    def uni(a,b):
        a,b=find(a),find(b)
        if a!=b: par[b]=a
    # bucket by grid cell so this stays near-linear instead of O(n^2)
    grid=defaultdict(list); C=MAXDIM
    for i,(r,*_ ) in enumerate(prims):
        for gx in range(int(r.x0//C), int(r.x1//C)+1):
            for gy in range(int(r.y0//C), int(r.y1//C)+1):
                grid[(gx,gy)].append(i)
    for cell in grid.values():
        for a in range(len(cell)):
            for b in range(a+1,len(cell)):
                i,j=cell[a],cell[b]; ri,rj=prims[i][0],prims[j][0]
                if (ri.x0-GAP<rj.x1 and rj.x0-GAP<ri.x1 and
                    ri.y0-GAP<rj.y1 and rj.y0-GAP<ri.y1): uni(i,j)
    groups=defaultdict(list)
    for i in range(len(prims)): groups[find(i)].append(i)
    return list(groups.values())

def signature(prims, idxs):
    return tuple(sorted((prims[i][1], prims[i][2], prims[i][3]) for i in idxs))

for pno in (25, 4):
    p=pymupdf.open('Skanksa.pdf')[pno]
    prims=page_prims(p); cl=cluster(prims)
    sigs=defaultdict(list)
    for g in cl:
        if not (1 <= len(g) <= 8): continue
        xs=[prims[i][0] for i in g]
        bb=pymupdf.Rect(min(r.x0 for r in xs),min(r.y0 for r in xs),
                        max(r.x1 for r in xs),max(r.y1 for r in xs))
        if max(bb.width,bb.height) > 22: continue
        sigs[signature(prims,g)].append(bb)
    print(f'\n=== page {pno+1} ===  {len(prims)} symbol-scale paths -> {len(cl)} clusters -> {len(sigs)} distinct motifs')
    top=sorted(sigs.items(), key=lambda kv:-len(kv[1]))[:12]
    for sig,boxes in top:
        w=sum(b.width for b in boxes)/len(boxes); h=sum(b.height for b in boxes)/len(boxes)
        desc=' + '.join(f'{t}:{a}x{b}' for t,a,b in sig[:4]) + ('...' if len(sig)>4 else '')
        print(f'  {len(boxes):4d}  bbox {w:5.2f}x{h:5.2f}pt   {desc}')

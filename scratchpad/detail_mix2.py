import json, glob, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from calib import fixrec
from structlab.reading import ai_read, detail as D
from structlab.ai import tokens as T
from structlab.models import fmt_time
from PIL import Image
SIZES = {}
def fake_derive(src, tag, scale, box=None):
    with Image.open(src) as im:
        w, h = im.size
    if box: w, h = box[2]-box[0], box[3]-box[1]
    k = min(scale, D.MAX_EDGE / max(w, h))
    key = f"fake://{src}#{tag}"
    SIZES[key] = (w*k, h*k)
    return key
D.derive = fake_derive
def tok(p):
    if p in SIZES: return T.tokens_for_size(*SIZES[p])
    return T.image_tokens(p)
for d in sys.argv[1:]:
    for rp in glob.glob(d + '/files/*/reading.json'):
        r = fixrec(rp)
        if (r.get('role') or {}).get('kind') != 'lecture_detail': continue
        cats = {}; n_req = 0
        for sc in r['scenes']:
            if sc.get('same_as') is not None or sc.get('mode') == 'experiment': continue
            for req in ai_read.video_detail_requests(r['path'], sc, fmt_time):
                n_req += 1
                for im in req.images:
                    lab = im.label
                    c = ('rep_overview' if '場面の代表・全体' in lab else 'rep_tile' if '部分拡大' in lab else
                         'prev_overview' if '比較用' in lab else 'event_overview' if '変化・全体' in lab else
                         'change_zoom' if '変わった部分' in lab else 'other')
                    x = cats.setdefault(c, [0, 0]); x[0] += 1; x[1] += tok(str(im.path))
        w, h = Image.open(r['scenes'][0]['frame']).size
        tot = sum(v[1] for v in cats.values())
        print(r['path'][:30], 'native', (w, h), 'req', n_req, 'img_tokens', tot)
        for c, (n, t) in sorted(cats.items(), key=lambda x: -x[1][1]):
            print(f'   {c:15} n={n:5} tokens={t:9,} ({t/tot:.0%}) avg={t//max(n,1)}')

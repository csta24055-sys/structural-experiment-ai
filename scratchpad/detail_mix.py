import json, glob, os, sys, re
sys.path.insert(0, os.path.dirname(__file__))
from calib import fixrec
from structlab.reading import ai_read
from structlab.ai.tokens import image_tokens
from structlab.models import fmt_time
for d in sys.argv[1:]:
    for rp in glob.glob(d + '/files/*/reading.json'):
        r = fixrec(rp)
        if (r.get('role') or {}).get('kind') != 'lecture_detail':
            continue
        cats = {}
        n_req = 0; n_img = 0; scenes = 0; events = 0; native = {}
        for sc in r['scenes']:
            if sc.get('same_as') is not None or sc.get('mode') == 'experiment':
                continue
            scenes += 1
            events += len(sc.get('events_with_frames', []) or [])
            for req in ai_read.video_detail_requests(r['path'], sc, fmt_time):
                n_req += 1
                for im in req.images:
                    n_img += 1
                    lab = im.label
                    c = ('rep_overview' if '場面の代表・全体' in lab else 'rep_tile' if '部分拡大' in lab else
                         'prev_overview' if '比較用' in lab else 'event_overview' if '変化・全体' in lab else
                         'change_zoom' if '変わった部分' in lab else 'other')
                    t = image_tokens(im.path)
                    x = cats.setdefault(c, [0, 0]); x[0] += 1; x[1] += t
        from PIL import Image
        w, h = Image.open(r['scenes'][0]['frame']).size
        tot = sum(v[1] for v in cats.values())
        print(r['path'], 'native', (w, h), 'native_tokens', image_tokens(r['scenes'][0]['frame']), 'scenes', scenes, 'events', events, 'req', n_req, 'imgs', n_img)
        for c, (n, t) in sorted(cats.items(), key=lambda x: -x[1][1]):
            print(f'   {c:15} n={n:4} tokens={t:8,} ({t/tot:.0%}) avg={t//max(n,1)}')

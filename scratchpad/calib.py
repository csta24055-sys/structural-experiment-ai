import json, glob, os, sys
from structlab.reading import ai_read
def fixrec(rp):
    r = json.load(open(rp)); base = os.path.dirname(rp)
    def fix(p):
        if not p: return p
        for sub in ('frames', 'pages'):
            q = os.path.join(base, sub, os.path.basename(p))
            if os.path.exists(q): return q
        return p
    for sc in r.get('scenes', []):
        sc['frame'] = fix(sc.get('frame'))
        for e in sc.get('events_with_frames', []) or []: e['frame'] = fix(e.get('frame'))
        for k in ('window_frames', 'frames'):
            v = sc.get(k)
            if isinstance(v, list):
                sc[k] = [[a, fix(b)] if isinstance(b, str) else ([a[0], fix(a[1])] if isinstance(a, list) else a) for a, *rest in [(x if isinstance(x, list) else [x]) for x in v] for b in ([rest[0]] if rest else [None])]
    for p in r.get('pages', []): p['render'] = fix(p.get('render'))
    return r
def main():
    tot_e = tot_m = 0
    for d in sorted(glob.glob(sys.argv[1] + '/a*/')):
        tc = json.load(open(d + 'token_counts.json'))
        for rp in glob.glob(d + 'files/*/reading.json'):
            r = fixrec(rp)
            if r['path'] not in tc: continue
            est = {}
            for kind, img, text, item, cached in ai_read.plan_requests(r):
                f = ai_read._kind_family(kind); est.setdefault(f, [0, 0]); est[f][0] += 1; est[f][1] += img + text + cached + 1800
            meas = tc[r['path']]
            e = sum(v[1] for v in est.values()); m = sum(v['input_tokens'] for v in meas.values())
            tot_e += e; tot_m += m
            print(f"{r['path'][:28]:30} est {e:>9,} meas {m:>9,} ratio {e/m:.3f}", {k: v for k, v in est.items()}, {k: (v['requests'], v['input_tokens']) for k, v in meas.items()})
    print('TOTAL est', tot_e, 'meas', tot_m, 'ratio', round(tot_e / tot_m, 3))

if __name__ == "__main__":
    main()

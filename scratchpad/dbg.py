import sys, difflib
sys.path.insert(0, "tests")
from pathlib import Path
import tempfile
import test_reading as T
tmp = Path(tempfile.mkdtemp())
seq, a = T._semantic_run(tmp, "seq", False)
par, b = T._semantic_run(tmp, "par", True)
A, B = sorted(seq.sent), sorted(par.sent)
print(len(A), len(B), seq.batches, par.batches)
for x, y in zip(A, B):
    if x != y:
        print(x[0], x[1], y[0], y[1])
        for i,(p,q) in enumerate(zip(x,y)):
            if p!=q:
                print("field", i)
                for l in difflib.unified_diff(str(p).splitlines(), str(q).splitlines(), lineterm="", n=0):
                    print(l[:200])
        break
print(a == b)

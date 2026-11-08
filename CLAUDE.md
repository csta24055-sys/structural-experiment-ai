# Claude Code operating instructions: RC II eccentricity worksheet

You are working on the assignment in tasks/rc2-eccentricity. Do not alter earlier coursework or the main branch. Before doing anything, confirm that the current branch is task/rc2-eccentricity-2026-11-08.

Goal: solve the one-page 「06_保有水平耐力計算とは_課題.pdf」 using 「06_保有水平耐力計算とは_講義ノート.pdf」, then independently verify every number and create both a full Japanese explanation and a readable filled-in worksheet PDF when the source PDFs are locally available.

Mandatory workflow:
1. Read tasks/rc2-eccentricity/PROBLEM.md and the lecture's pages 20, 21 and 22. If local PDFs exist under tasks/rc2-eccentricity/materials/, open/render and VISUALLY inspect the original figures (not just extracted PDF text). Explicitly confirm what KY=8K means for the right-hand wall.
2. Reproduce the lecture's ordered calculations: (1) 重心, (2) 剛心, (3) 偏心距離, (4) ねじり剛性, (5) 弾力半径, (6) 偏心率. Carefully distinguish X-input and Y-input. For Rx use Y-direction stiffness, and for Ry use X-direction stiffness.
3. Keep K symbolic: K is a reference stiffness, not a numeric force. Retain units in all intermediate expressions. For the right wall treat KY=8K as the entire wall-with-columns equivalent spring ONCE at x=12 m; in X use six separate column springs K. If inspection contradicts this interpretation, STOP and document the discrepancy before altering the model.
4. Execute python3 tasks/rc2-eccentricity/solver.py and python3 -m unittest discover -s tasks/rc2-eccentricity -p 'test_*.py' -v. Independently re-derive the torsional stiffness by grouping columns/walls; test sensitivity to double-counting the wall.
5. Produce tasks/rc2-eccentricity/OUTPUT_SOLUTION.md with displayed substitution and arithmetic, not just final values. Make clear which eccentricity ratio exceeds the lecture threshold 0.15.
6. If the PDFs are available locally, use a Japanese-capable font and insert concise legible writing into the existing worksheet blanks, producing tasks/rc2-eccentricity/output/filled_assignment.pdf. Verify the rendered result for clipping, legibility and alignment. Also produce a detailed explanation PDF only if appropriate.
7. Do not commit lecture PDFs or the worksheet PDF into this PUBLIC GitHub repository, and never commit tokenized/signed Canvas CDN URLs. Do not claim you inspected a PDF if only PROBLEM.md was available. The source files should remain local under the ignored materials/ directory.
8. Commit only code, tests, source-derived input facts, explanatory solution and (if desired) generated original answer PDFs; do not push copyrighted lecture/source PDFs. Open or update a Draft PR against main and report the exact tests and outputs.

Reference conventions (from lecture pp.20-22):
GX=Σ(NX)/ΣN, GY=Σ(NY)/ΣN
RX=Σ(KY X)/ΣKY, RY=Σ(KX Y)/ΣKX
eX=|RX-GX|, eY=|RY-GY|
Kr=Σ(KX (Y-RY)^2)+Σ(KY (X-RX)^2)
reX=√(Kr/ΣKX), reY=√(Kr/ΣKY)
ReX=eY/reX, ReY=eX/reY

Do not blindly reuse the different numbers in the lecture example (its special column has 3K). In this assignment the right-hand resisting element is a WALL of KY=8K.
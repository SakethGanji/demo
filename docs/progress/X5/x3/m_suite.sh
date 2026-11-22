#!/usr/bin/env bash
# X5/x3: run X3 lane M's own scripts (X3/M/*.py, unchanged) against one API, in X3's order.
# Usage: m_suite.sh <port> <work dir> <out dir>
P=$1; W=$2; O=$(realpath -m $3); PY=/home/user/demo/backend/.venv/bin/python
mkdir -p $W $O; cp /home/user/demo/docs/progress/X3/M/*.py $W/; cd $W
export A=http://localhost:$P/api
for s in m1 m2 d1 d2 d3 d4 d5 d6 c1 c2 x1 x2 x4 fz; do
  timeout 900 $PY $s.py > $O/$s.out 2>&1; echo "$s exit $?" >> $O/_exit.txt
done
# x1 needs the Excel edge-case workbook (X3/M/mkxl.py); x4 is X3's memory measurement (not a finding) and is skipped
$PY mkxl.py $W/edge.xlsx > /dev/null 2>&1 && timeout 600 $PY x1.py $W/edge.xlsx > $O/x1.out 2>&1; echo "x1(edge.xlsx) exit $?" >> $O/_exit.txt

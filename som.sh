#!/bin/sh
DIR="`dirname \"$0\"`"
if [ -z "$PYTHON" ]; then
  PYTHON=pypy
fi
if [ -z "$PYPY_DIR" ]; then
  PYPY_DIR=$DIR/pypy
fi
export PYTHONPATH=$DIR/src:$PYPY_DIR:$PYTHONPATH
export SOM_INTERP=BC
exec $PYTHON $DIR/src/main.py -cp Smalltalk:Examples/Benchmarks/Richards:Examples/Benchmarks/CD:Examples/Benchmarks/NBody:Examples/Benchmarks/DeltaBlue:Examples/Benchmarks/Json:Examples/Benchmarks/GraphSearch "$@"

#!/bin/sh
DIR="`dirname \"$0\"`"
if [ -z "$PYTHON" ]; then
  PYTHON=pypy
fi
export PYTHONPATH=$DIR/src:$DIR/pypy:$PYTHONPATH
exec $PYTHON $DIR/src/som/vm/universe.py "$@"

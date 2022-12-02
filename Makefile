#!/usr/bin/env make -f

JOBS=$(subst -j,--make-jobs ,$(filter -j%, $(MAKEFLAGS)))
PYPY_DIR ?= pypy
RPYTHON  ?= $(PYPY_DIR)/rpython/bin/rpython $(JOBS)
RPYTHON_ARGS ?= # --lldebug
SOM_TIER=1

.PHONY: compile som-interp som-jit som-ast-jit som-bc-jit som-bc-interp som-ast-interp

all: compile

compile: som-bc-jit-interp som-bc-jit-tier1 som-bc-jit-tier2 som-bc-jit-hybrid

som-ast-jit: core-lib/.git
	SOM_INTERP=AST PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) $(RPYTHON) $(RPYTHON_ARGS) --batch -Ojit src/main_rpython.py

som-bc-jit:	core-lib/.git
	SOM_TIER=$(SOM_TIER) SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) $(RPYTHON) $(RPYTHON_ARGS) --batch -Ojit src/main_rpython.py

som-bc-jit-tier1: core-lib/.git
	SOM_TIER=1 SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) $(RPYTHON) $(RPYTHON_ARGS) --batch -Ojit src/main_rpython.py

som-bc-jit-tier2: core-lib/.git
	SOM_TIER=2 SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) $(RPYTHON) $(RPYTHON_ARGS) --batch -Ojit src/main_rpython.py

som-bc-jit-hybrid: core-lib/.git
	SOM_TIER=3 SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) $(RPYTHON) $(RPYTHON_ARGS) --batch -Ojit src/main_rpython.py

som-ast-interp: core-lib/.git
	SOM_INTERP=AST PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) $(RPYTHON) $(RPYTHON_ARG) --batch src/main_rpython.py

som-bc-interp: core-lib/.git
	SOM_TIER=1 SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) $(RPYTHON) $(RPYTHON_ARG) --batch src/main_rpython.py

som-interp: som-ast-interp som-bc-interp

som-jit: som-ast-jit som-bc-jit

test: compile
	PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) nosetests
	if [ -e ./som-ast-jit    ]; then ./som-ast-jit    -cp Smalltalk TestSuite/TestHarness.som; fi
	if [ -e ./som-bc-jit     ]; then ./som-bc-jit     -cp Smalltalk TestSuite/TestHarness.som; fi
	if [ -e ./som-ast-interp ]; then ./som-ast-interp -cp Smalltalk TestSuite/TestHarness.som; fi
	if [ -e ./som-bc-interp  ]; then ./som-bc-interp  -cp Smalltalk TestSuite/TestHarness.som; fi

clean:
	@-rm som-ast-jit som-ast-interp
	@-rm som-bc-jit  som-bc-interp

core-lib/.git:
	git submodule update --init

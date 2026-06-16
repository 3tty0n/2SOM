#!/usr/bin/env make -f

JOBS=$(subst -j,--make-jobs ,$(filter -j%, $(MAKEFLAGS)))
RTIME_EXT_DIR ?= site-packages/rtime_ext
PYPY_DIR ?= pypy
PYPY_NO_HO_DIR ?= pypy-no-handler-opt
RPYTHON  ?= $(PYPY_DIR)/rpython/bin/rpython $(JOBS)
RPYTHON_ARGS ?= # --lldebug
SOM_TIER=1

.PHONY: compile som-interp som-jit som-ast-jit som-bc-jit som-bc-interp som-ast-interp

all: compile

compile: som-bc-interp som-bc-jit-tier1 som-bc-jit-tier2 som-bc-jit-tier3 som-bc-jit-adaptive som-bc-jit-shaping som-bc-jit-hybrid

som-ast-jit: core-lib/.git
	SOM_INTERP=AST PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR):$(RTIME_EXT_DIR) $(RPYTHON) $(RPYTHON_ARGS) --batch -Ojit src/main_rpython.py

som-bc-jit:	core-lib/.git
	SOM_TIER=$(SOM_TIER) SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR):$(RTIME_EXT_DIR) $(RPYTHON) $(RPYTHON_ARGS) --batch -Ojit src/main_rpython.py

som-bc-jit-tier1: core-lib/.git
	SOM_TIER=1 SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR):$(RTIME_EXT_DIR) $(RPYTHON) $(RPYTHON_ARGS) --batch -Ojit src/main_rpython.py

som-bc-jit-tier1-no-ic: core-lib/.git
	SOM_TIER=6 SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_NO_HO_DIR):$(RTIME_EXT_DIR) $(RPYTHON) $(RPYTHON_ARGS) --batch -Ojit src/main_rpython.py

som-bc-jit-tier1-no-ic-no-handler-opt: core-lib/.git
	SOM_TIER=7 SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_NO_HO_DIR):$(RTIME_EXT_DIR) $(RPYTHON) $(RPYTHON_ARGS) --batch -Ojit src/main_rpython.py

# SOM_TIER=2 is now the stack inliner (was the tracing JIT); the tracing JIT is
# SOM_TIER=3 (som-bc-jit-tier3) and the adaptive hybrid is SOM_TIER=4.
som-bc-jit-tier2: core-lib/.git
	SOM_TIER=2 SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR):$(RTIME_EXT_DIR) $(RPYTHON) $(RPYTHON_ARGS) --batch -Ojit src/main_rpython.py

som-bc-jit-tier3: core-lib/.git
	SOM_TIER=3 SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR):$(RTIME_EXT_DIR) $(RPYTHON) $(RPYTHON_ARGS) --batch -Ojit src/main_rpython.py

som-bc-jit-adaptive: core-lib/.git
	SOM_TIER=4 SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR):$(RTIME_EXT_DIR) $(RPYTHON) $(RPYTHON_ARGS) --batch -Ojit src/main_rpython.py

# Tier 5 (adaptive trace shaping) built as its own standalone binary. It is the
# tracing JIT (is_tier3() is true) compiled via SOM_TIER5=1, so it can diverge
# from the plain tier-3 build. Run it plain for tier-3 behaviour, or with
# SOM_SHAPING=1 to enable trace shaping.
som-bc-jit-shaping: core-lib/.git
	SOM_TIER5=1 SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR):$(RTIME_EXT_DIR) $(RPYTHON) $(RPYTHON_ARGS) --batch -Ojit src/main_rpython.py

som-bc-jit-hybrid: core-lib/.git
	SOM_TIER=5 SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR):$(RTIME_EXT_DIR) $(RPYTHON) $(RPYTHON_ARGS) --batch -Ojit src/main_rpython.py

som-ast-interp: core-lib/.git
	SOM_INTERP=AST PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) $(RPYTHON):$(RTIME_EXT_DIR) $(RPYTHON_ARG) --batch src/main_rpython.py

som-bc-interp: core-lib/.git
	SOM_TIER=1 SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR):$(RTIME_EXT_DIR) $(RPYTHON) $(RPYTHON_ARG) --batch src/main_rpython.py

som-interp: som-ast-interp som-bc-interp

som-jit: som-ast-jit som-bc-jit

som-bc-jit: som-bc-jit-tier1 som-bc-jit-tier2 som-bc-jit-tier3 som-bc-jit-adaptive som-bc-jit-shaping som-bc-jit-hybrid

som-bc-jit-tier1-evaluation: som-bc-jit-tier1-no-ic som-bc-jit-tier1-no-ic-no-handler-opt

test: compile
	PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) nosetests
	if [ -e ./som-ast-jit    ]; then ./som-ast-jit    -cp Smalltalk TestSuite/TestHarness.som; fi
	if [ -e ./som-bc-jit     ]; then ./som-bc-jit     -cp Smalltalk TestSuite/TestHarness.som; fi
	if [ -e ./som-ast-interp ]; then ./som-ast-interp -cp Smalltalk TestSuite/TestHarness.som; fi
	if [ -e ./som-bc-interp  ]; then ./som-bc-interp  -cp Smalltalk TestSuite/TestHarness.som; fi

clean:
	@-rm som-ast-jit som-ast-interp
	@-rm som-bc-jit  som-bc-interp som-bc-jit-tier1 som-bc-jit-tier2 som-bc-jit-tier3 som-bc-jit-adaptive som-bc-jit-shaping som-bc-jit-hybrid
	@-rm som-bc-jit-tier1-no-ic som-bc-jit-tier1-no-ic-no-handler-opt som-bc-interp-tier1

core-lib/.git:
	git submodule update --init

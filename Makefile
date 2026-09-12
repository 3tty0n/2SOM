#!/usr/bin/env make -f

PYPY_DIR ?= pypy
RPYTHON  ?= $(PYPY_DIR)/rpython/bin/rpython

.PHONY: compile som-interp som-jit som-ast-jit som-bc-jit som-bc-interp som-ast-interp

all: compile

compile: som-ast-jit

som-ast-jit: core-lib/.git
	SOM_INTERP=AST PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) $(RPYTHON) --batch -Ojit src/main_rpython.py

som-bc-jit:	core-lib/.git
	SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) $(RPYTHON) --batch -Ojit src/main_rpython.py

som-bc-jit-send-aot: core-lib/.git
	SOM_SEND_PLACE=aot SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) $(RPYTHON) --batch -Ojit src/main_rpython.py

som-bc-jit-send-aot-prebuilt: core-lib/.git
	SOM_SEND_PLACE=aot SOM_INTERP=BC \
	  SOM_PREBUILT_CP="Smalltalk:Examples/Benchmarks:../benchmarks/Pipeline:../benchmarks/Actors:../benchmarks/Database:../benchmarks/Index:Examples/Benchmarks/Richards:Examples/Benchmarks/DeltaBlue:Examples/Benchmarks/Json" \
	  SOM_PREBUILT_CLASSES="BenchmarkHarness,Pipeline,Actors,Richards,DeltaBlue,Json" \
	  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) $(RPYTHON) --batch -Ojit src/main_rpython.py

som-bc-jit-send-jit: core-lib/.git
	SOM_SEND_PLACE=jit SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) $(RPYTHON) --batch -Ojit src/main_rpython.py

som-bc-jit-send-pgo: core-lib/.git
	SOM_SEND_PLACE=pgo SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) $(RPYTHON) --batch -Ojit src/main_rpython.py

som-ast-interp: core-lib/.git
	SOM_INTERP=AST PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) $(RPYTHON) --batch src/main_rpython.py

som-bc-interp: core-lib/.git
	SOM_INTERP=BC  PYTHONPATH=$(PYTHONPATH):$(PYPY_DIR) $(RPYTHON) --batch src/main_rpython.py

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

#!/bin/bash

tiny=(Fibonacci Dispatch Loop Sum Recurse)

micro=(Bounce BubbleSort Fannkuch List Mandelbrot Queens QuickSort Sieve Storage Towers TreeSort)

macro=(GraphSearch NBody Richards Json DeltaBlue PageRank)

[ ! -d data-bytecode-counts ] && mkdir data-bytecode-counts

dump_macro() {
    for bm in "${macro[@]}"; do
        printf "${bm} ..."
        innerIter=
        if [ $bm = PageRank ]; then
            innerIter=2
        fi
        SOM_INTERP=BC ./som.sh \
                      -d -cp Smalltalk:Examples/Benchmarks/${bm} \
                      Examples/Benchmarks/BenchmarkHarness.som ${bm} 1 ${innerIter} 2> \
                      data-bytecode-counts/${bm}.bytecode >/dev/null
        echo "done."
    done
}

dump_micro() {
    for bm in "${micro[@]}"; do
        printf "${bm} ..."
        SOM_INTERP=BC ./som.sh \
                      -d -cp Smalltalk \
                      Examples/Benchmarks/BenchmarkHarness.som ${bm} 1 2> \
                      data-bytecode-counts/${bm}.bytecode >/dev/null
        echo "done."
    done
}

dump_tiny() {
    for bm in "${tiny[@]}"; do
        printf "${bm} ..."
        SOM_INTERP=BC ./som.sh \
                      -d -cp Smalltalk:Examples/Benchmarks/LanguageFeatures \
                      Examples/Benchmarks/BenchmarkHarness.som ${bm} 1 2> \
                      data-bytecode-counts/${bm}.bytecode >/dev/null
        echo "done."
    done
}

count_size_bytecodes() {
    for bm in "${macro[@]}" "${micro[@]}" "${tiny[@]}"; do
        printf "${bm}, "
        grep 'bc_count' data-bytecode-counts/${bm}.bytecode | cut -d' ' -f5 | awk -F: '{sum += $1} END {print sum}'
    done
}

#dump_micro
dump_tiny
count_size_bytecodes

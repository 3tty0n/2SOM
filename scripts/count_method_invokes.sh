#!/bin/bash

benchs=(Bounce BubbleSort Fannkuch List Mandelbrot PageRank Queens QuickSort Sieve Storage Towers TreeSort
        GraphSearch NBody Json Richards DeltaBlue)

[ ! -d data-method-counts ] && mkdir data-method-counts

benchs=(Fibonacci Loop Sum Dispatch Recurse)

for bm in "${benchs[@]}"; do
    printf "${bm} ..."
    innerIter=
    if [ $bm = PageRank ]; then
        innerIter=2
    fi
    SOM_INTERP=BC ./som.sh \
        -cp Smalltalk:Examples/Benchmarks/LanguageFeatures:Examples/Benchmarks/NBody:Examples/Benchmarks/Json:Examples/Benchmarks/Richards:Examples/Benchmarks/GraphSearch:Examples/Benchmarks/DeltaBlue \
                  Examples/Benchmarks/BenchmarkHarnessNP.som ${bm} 1 ${innerIter} 2> data-method-counts/${bm}.txt >/dev/null
    echo "done."
done

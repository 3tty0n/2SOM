#!/bin/bash


BENCHS=("Permute 1500" "Queens 1000" "BubbleSort 2000" "QuickSort 2000" "TreeSort 2000"
        "PageRank 1000" "NBody 25000" "GraphSearch 40" "Fibonacci 2000" "Recurse 2000"
        "Sum 1000" "Towers 1000" "List 1000" "Fannkuch 9" "Bounce 4000" "Sieve 2500"
        "Storage 1000" "Mandelbrot 1000")

SOM=som.sh
SOM_ARG="-cp Smalltalk:Examples/Benchmarks/GraphSearch:Examples/Benchmarks/NBody:Examples/Benchmarks/LanguageFeatures:Examples/Benchmarks/TestSuite Examples/Benchmarks/BenchmarkHarness.som"

for arg in "${BENCHS[@]}"; do
    set -- ${arg}
    bm=$1
    extra_arg=$2
    echo $bm
    SOM_INTERP=BC ./${SOM} ${SOM_ARG} $bm 1 $extra_arg
done

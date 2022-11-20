#!/bin/bash

control_c() {
    pkill $0
    exit
}

trap control_c SIGINT

declare -a macro=("NBody 100" "GraphSearch 2" "PageRank 12")
declare -a micro=("Fannkuch 5" "Bounce 0" "Permute 0" "Queens 0" "List 0" "Storage 0" "Sieve 0" "BubbleSort 0"
                  "QuickSort 0" "TreeSort 0" "Mandelbrot 7")
declare -a tiny=("Fibonacci 0" "Dispatch 1" "Loop 0" "Recurse 0" "Sum 0")

collect_send_type_info() {
    echo "Benchmark,Primitive sends,Trivial sends,BcMethod sends"

    for tuple in "${macro[@]}" "${micro[@]}"
    do
        set -- $tuple
        bm=$1
        if [ $2 = 0 ]; then
            iter=
        else
            iter=$2
        fi

        printf "${bm},"
        SOM_INTERP=BC PYTHONPATH=./src ./som.sh -cp Smalltalk:Examples/Benchmarks/${bm} Examples/Benchmarks/BenchmarkHarness.som ${bm} 30 ${iter} >/dev/null
    done


    for tuple in "${tiny[@]}"
    do
        set -- $tuple
        bm=$1; iter=$2
        if [ $2 = 0 ]; then
            iter=
        else
            iter=$2
        fi

        printf "${bm},"
        SOM_INTERP=BC PYTHONPATH=./src ./som.sh -cp Smalltalk:Examples/Benchmarks/LanguageFeatures Examples/Benchmarks/BenchmarkHarness.som ${bm} 30 ${iter} >/dev/null
    done

    IFS=
}

collect_trace_info() {
    if [ ! -f ./som-bc-jit-tier1 ]; then
        echo "som-bc-jit-tier1 is not found. Pleaes make it by 'make som-bc-jit-tier1 SOM_TIER=1'"
    fi

    if [ ! -f ./som-bc-jit-tier2 ]; then
        echo "som-bc-jit-tier2 is not found. Pleaes make it by 'make som-bc-jit-tier1 SOM_TIER=2'"
    fi

    for tuple in "${macro[@]}" "${micro[@]}" "${tiny[@]}"
    do
        set -- $tuple
        bm=$1
        if [ $2 = 0 ]; then
            iter=
        else
            iter=$2
        fi

        if [ ${bm} = "Sum" ] || [ ${bm} = "Sieve" ]; then
            THRESHOLD=23
        elif [ ${bm} = "Dispatch" ]; then
            THRESHOLD=2
        else
            THRESHOLD=57
        fi

        PYPYLOG=jit-log-opt,jit-backend-addr,jit-backend-count,jit-summary:data/${bm}_tier1.trace \
            ./som-bc-jit-tier1 --jit function_threshold=${THRESHOLD} --jit threshold=${THRESHOLD} -cp Smalltalk:Examples/Benchmarks/LanguageFeatures:Examples/Benchmarks/${bm} Examples/Benchmarks/BenchmarkHarness.som ${bm} 30 ${iter} >/dev/null

        PYPYLOG=jit-log-opt,jit-backend-addr,jit-backend-count,jit-summary:data/${bm}_tier2.trace \
            ./som-bc-jit-tier2 --jit function_threshold=${THRESHOLD} --jit threshold=${THRESHOLD} -cp Smalltalk:Examples/Benchmarks/LanguageFeatures:Examples/Benchmarks/${bm} Examples/Benchmarks/BenchmarkHarness.som ${bm} 30 ${iter} >/dev/null
    done
}

calc_trace_length() {
    benchs=()
    arr_ops_threaded=()
    arr_ops_tracing=()
    for tuple in "${macro[@]}" "${micro[@]}" "${tiny[@]}"
    do
        set -- $tuple
        bm=$1

        # "threaded code,ops"
        # printf "${bm},"
        value=$(grep "^ops" data/"${bm}"_tier1.trace | awk '{print $2}')
        benchs+=( "${bm}" )
        arr_ops_threaded+=( "${value}" )

        # "tracing JIT,ops"
        value=$(grep "^ops" data/"${bm}"_tier2.trace | awk '{print $2}')
        arr_ops_tracing+=( "${value}" )
    done

    echo "Bench,threaded,tracing"
    for (( i=0; i < ${#arr_ops_threaded[*]}; ++i ))
    do
        bm="${benchs[i]}"
        ops_threaded="${arr_ops_threaded[i]}"
        ops_tracing="${arr_ops_tracing[i]}"
        echo "${bm},${ops_threaded},${ops_tracing}"
    done
}

calc_comp_time() {
    benchs=()
    arr_comp_time_threaded=()
    arr_comp_time_tracing=()
    for tuple in "${macro[@]}" "${micro[@]}" "${tiny[@]}"; do
        set -- $tuple
        bm=$1

        benchs+=( "${bm}" )
        comp_time=$(grep "^Tracing\|Backend" data/"${bm}"_tier1.trace | awk '{sum += $3} END {print sum}')
        arr_comp_time_threaded+=( "${comp_time}" )

        comp_time=$(grep "^Tracing\|Backend" data/"${bm}"_tier2.trace | awk '{sum += $3} END {print sum}')
        arr_comp_time_tracing+=( "${comp_time}" )
    done

    echo "Bench,threaded,tracing"
    for (( i=0; i < ${#arr_comp_time_threaded[*]}; ++i ))
    do
        bm="${benchs[i]}"
        comp_time_threaded="${arr_comp_time_threaded[i]}"
        comp_time_tracing="${arr_comp_time_tracing[i]}"
        echo "${bm},${comp_time_threaded},${comp_time_tracing}"
    done
}

# collect_trace_info
# calc_trace_length
calc_comp_time

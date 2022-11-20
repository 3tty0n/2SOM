#!/bin/bash

control_c() {
    pkill $0
    exit
}

trap control_c SIGINT

declare -a macro=("NBody 100" "GraphSearch 2" "PageRank 12")
declare -a micro=("Fannkuch 5" "Bounce 0" "Permute 0" "Queens 0" "List 0" "Storage 0" "Sieve 0" "BubbleSort 0" "QuickSort 0" "Towers 0" "TreeSort 0" "Mandelbrot 7")
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

shape_trace_length() {
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

count_execution_number_of_slow_path() {
    echo "Bench,slow_path,total - slow_path,total"
    for tuple in "${macro[@]}" "${micro[@]}" "${tiny[@]}"
    do
        set -- $tuple
        bm=$1

        slow_paths=$(grep "with 6 ops" -A 2 data/${bm}_tier1.trace | awk '{print $4}' | grep -o -E '[0-9]+')
        sum_slow_path=0
        for s in ${slow_paths}; do
            nums=$(grep ${s} data/${bm}_tier1.trace | grep -o -E '^TargetToken\([0-9]+\):[0-9]+' | awk -F':' '{ printf $NF }')
            for n in ${nums}; do
                (( sum_slow_path += n ))
            done
        done


        entries=$(grep "^entry [0-9]*:[0-9]*" data/${bm}_tier1.trace | awk -F':' '{ print $NF} ')
        sum_entry=0
        for entry in $entries; do
            (( sum_entry += entry ))
        done

        diff_slow_path_entry=$(( sum_entry - sum_slow_path ))

        echo "${bm},${sum_slow_path},${diff_slow_path_entry},${sum_entry}"
    done
}

#collect_trace_info

#shape_trace_length

count_execution_number_of_slow_path

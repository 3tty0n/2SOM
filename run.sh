#!/bin/bash

set -x

L3_SIZE_VALUE="$(lscpu | grep L3 | awk '{print $3 / 2}')"
L3_SIZE="${L3_SIZE_VALUE}"M

iter=(10 20 30)
nursery=(4M 8M 16M)

dir=rebench-$(date '+%Y%m%d-%H%M%s')

if [ ! -d ${dir} ]; then
    mkdir ${dir}
fi

for i in "${iter[@]}"; do
    for n in "${nursery[@]}"; do
        PYPY_GC_NURSERY=$n sudo rebench -it $i runbench.conf
        mv runbench.data ${dir}/runbench_${n}_${i}.data
    done
done

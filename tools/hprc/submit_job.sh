#!/usr/bin/env bash

matlabsubmit -t 00:20 \ # estimated wallclock time
             -s 20 \     # 4 cores
             -f "srt_fs_main(\"./input/theseus_hybrid_irec.inp\")"
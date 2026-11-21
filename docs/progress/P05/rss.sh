#!/bin/bash
# sample the RSS (kB) of a pid until a stop file appears; print the max
pid=$1; stop=$2; max=0
while [ ! -f "$stop" ]; do r=$(ps -o rss= -p $pid 2>/dev/null | tr -d ' '); [ -n "$r" ] && [ "$r" -gt "$max" ] && max=$r; sleep 0.2; done
echo "max_rss_kb $max"

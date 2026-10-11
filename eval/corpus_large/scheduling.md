# CPU Scheduling

The scheduler decides which ready process runs next on the CPU. Goals are high CPU utilisation, high throughput, low turnaround time, low waiting time and low response time.

## First Come First Served

FCFS runs processes in arrival order. It is simple but suffers from the convoy effect: one long CPU-bound job makes short jobs wait behind it.

## Shortest Job First

SJF picks the process with the smallest next CPU burst. It is provably optimal for minimising average waiting time. The difficulty is that the next burst length is unknown, so it is estimated with exponential averaging of previous bursts. The preemptive version is called shortest remaining time first.

## Round Robin

Round robin gives every process a fixed time quantum, usually 10 to 100 milliseconds, then moves it to the back of the ready queue. If the quantum is too large it degrades to FCFS; if it is too small, context switch overhead dominates.

## Priority Scheduling

Each process has a priority and the highest priority runs first. Low priority processes can suffer starvation. Aging solves this by gradually increasing the priority of processes that wait a long time.

## Multilevel Feedback Queue

Several queues with different priorities and quanta. Processes move between queues based on behaviour, so interactive jobs stay in high priority queues while CPU hogs sink to lower ones.

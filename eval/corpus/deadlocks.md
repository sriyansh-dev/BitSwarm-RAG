# Deadlocks

A deadlock is a state where a set of processes are each waiting for a resource held by another process in the set, so none can proceed.

## Four Necessary Conditions

Deadlock requires all four Coffman conditions at once: mutual exclusion, hold and wait, no preemption, and circular wait. Breaking any one prevents deadlock.

## Handling Deadlocks

Prevention removes one of the four conditions, for example by imposing a global ordering on resources to stop circular wait. Avoidance uses advance knowledge of maximum demands; the banker's algorithm only grants a request if the system stays in a safe state. Detection allows deadlock and then finds cycles in a wait-for graph. Recovery kills processes or preempts resources.

## Banker's Algorithm

The banker's algorithm keeps Available, Max, Allocation and Need matrices. A state is safe if there is an order in which every process can finish. The algorithm simulates granting a request and checks for a safe sequence before actually granting it.

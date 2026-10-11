# Process Synchronization

When processes share data, uncontrolled interleaving causes race conditions. The critical section problem asks for mutual exclusion, progress and bounded waiting.

## Semaphores

A semaphore is an integer variable accessed only through atomic wait and signal operations. A binary semaphore works like a mutex lock; a counting semaphore controls access to a pool of identical resources.

## Classic Problems

The producer consumer problem uses a bounded buffer with a mutex plus empty and full counting semaphores. The readers writers problem lets many readers share data but gives writers exclusive access. In the dining philosophers problem five philosophers share five chopsticks; a naive solution can deadlock, so one fix is to allow at most four philosophers to sit at the table.

## Monitors

A monitor is a language level construct that bundles shared data with the procedures that access it and allows only one process active inside at a time. Condition variables let a process wait inside a monitor.

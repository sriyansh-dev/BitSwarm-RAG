# Memory Management and Paging

Main memory is shared between processes, so the operating system must protect and translate addresses.

## Paging

Paging divides physical memory into fixed size frames and logical memory into pages of the same size. A page table maps each virtual page number to a frame number. Paging removes external fragmentation but causes some internal fragmentation in the last page of a process.

## TLB

The translation lookaside buffer is a small fast cache of recent page table entries. A TLB hit avoids an extra memory access to read the page table. The effective access time depends on the hit ratio.

## Virtual Memory and Page Faults

With demand paging a page is loaded only when referenced. Touching a page that is not in memory raises a page fault, and the OS loads the page from disk. If memory is full a page replacement algorithm picks a victim: FIFO, optimal, or least recently used. FIFO can show Belady's anomaly where more frames cause more faults.

## Thrashing

Thrashing happens when a process does not have enough frames, so it faults constantly and spends more time swapping than executing. The working set model prevents it by giving each process the frames it actively uses.

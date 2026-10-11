# File Systems

A file system organises data on disk into files and directories and tracks free space.

## Allocation Methods

Contiguous allocation stores a file in consecutive blocks; it is fast for sequential and direct access but suffers external fragmentation. Linked allocation chains blocks with pointers; it has no external fragmentation but random access is slow. Indexed allocation keeps all block pointers in an index block and supports direct access; Unix inodes use direct, single indirect, double indirect and triple indirect pointers.

## Free Space Management

Free blocks can be tracked with a bit vector, a linked list, grouping, or counting. A bit vector is simple and makes it easy to find contiguous free blocks.

## Journaling

A journaling file system writes intended metadata changes to a log before applying them. After a crash the log is replayed, so the file system recovers quickly without a full consistency scan. ext4 and NTFS use journaling.

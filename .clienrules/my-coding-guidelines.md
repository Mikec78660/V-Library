## Brief overview
This document outlines the coding guidelines for our project that will allow the operating system to access all the files on a tape changer even when the tapes are not loaded or mounted.
- Tape Library Service

A production-grade FUSE filesystem that provides seamless access to tape libraries by presenting them as a single mount point with intelligent caching and background operations.
Overview

The Tape Library Service transforms your MSL4048 tape library with HP LTO5 drives into a modern, easy-to-use filesystem. It handles the complexity of sequential tape storage while providing familiar filesystem operations to users and applications.
Key Features
 Virtual Filesystem

    Mount entire tape library as single mount point (/mnt/tape_library)
    Standard POSIX filesystem interface (read, write, create, delete, etc.)
    Transparent handling of sequential tape operations
    No application changes required - works with any software

 Intelligent Caching

    Fast file operations using local cache
    Configurable cache size (default: 100GB)
    Background tape transfers don't block user operations
    Write delay: 10 minutes by default (configurable)

 Automatic Tape Management

    Auto-index new tapes by LTFS on startup
    Smart defragmentation when >20% of capacity of files are deleted (configurable), contents will be moved to empty tape and old tape will be reformed.
    HP LTO5 capacity optimization (1.4TB usable per tape)

 Database Integration

    SQLite metadata database for fast directory listings
    Configurable location for the database
    No tape access needed for directory operations
    Complete file tracking and location mapping
    Efficient queries and indexing

 Production Ready

    Systemd service with proper process management
    Comprehensive logging and monitoring
    Error recovery and retry mechanisms, If the cache or database are lost tapes will be loaded, mounted and indexed to re-create the database.
    Security hardening and resource limits

## Communication style
- Do not put any code into the chat. Just inform me what file you are editing. I don't need to see the code.
## Development workflow
- Never use test data or mock data or simulated data. Always use the date output from the attached devices or output from executing binaries. Never simulate anything. Never create a demonstration.
- Always use the attached tape changer attached to this system. It is at /dev/sg1. executing "mtx -f /dev/sg1 status" will list all the tapes in the tape changer and verify the tape changer is attached.
- Always use the attached tape drive. The tape drive is attached at /dev/st0. executing "mt -f /dev/st0 status" will give you the status of the tape drive (ONLINE in the status means a tape is loaded, Empty in the status means no tape is loaded, Busy means the tape is mounted).
## Coding best practices
- Never create test snippits, mock data or test code. Never simulate any data. Always use live data from the system. Even when testing, use the actual codebase, do not create test code.
## Project context
- This project will be designed to run from a .servicce file. That is how it will start and stop.
- There will be environment vaiables in the services file to define the tape changer, the tape drive, the fuse mount point and the tmp mount directory for the tapes.
## Other guidelines
- Never issue a task complete statemnt until you have verified by running the actual code and looking at the logs and the mountpoint to verify the servcie is working correctly. 
- The main requirment of this project is that the files on the tape are avaiable at the fuse mount point. If there are no files at the fuse mount point then the service is not ready and the task is not complete.

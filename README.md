# Tape Library Service

A production-grade FUSE filesystem that provides seamless access to tape libraries by presenting them as a single mount point with intelligent caching and background operations.

## Overview

The Tape Library Service transforms your MSL4048 tape library with HP LTO5 drives into a modern, easy-to-use filesystem. It handles the complexity of sequential tape storage while providing familiar filesystem operations to users and applications.

## Key Features

- **Virtual Filesystem**: Mount entire tape library as single mount point (/mnt/tape_library) with standard POSIX filesystem interface
- **Intelligent Caching**: Fast file operations using local cache with configurable cache size (default: 100GB)
- **Automatic Tape Management**: Auto-index new tapes by LTFS on startup with smart defragmentation
- **Database Integration**: SQLite metadata database for fast directory listings
- **Production Ready**: Systemd service with proper process management and error recovery

## Project Context

This project will be designed to run from a .service file. Environment variables in the service file will define:
- The tape changer device
- The tape drive device  
- The fuse mount point
- The tmp mount directory for the tapes

## Development Guidelines

Based on the coding guidelines:
- Always use live data from the attached devices (/dev/sg1 for tape changer, /dev/st0 for tape drive)
- Never use test data, mock data, or simulated data
- The service must be verified by running actual code and checking logs and mountpoint
- The main requirement is that files on the tape are available at the fuse mount point

## Communication Style

- Do not put any code into the chat. Just inform what file you are editing.
- Never create test snippets, mock data or test code
- Always use actual codebase and live system data

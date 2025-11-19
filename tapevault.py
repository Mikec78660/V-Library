#!/usr/bin/env python3
import os
import sys
import sqlite3
import subprocess
import re
import time
import logging
import argparse
import threading
import errno
from fuse import FUSE, FuseOSError, Operations, LoggingMixIn
from stat import S_IFDIR, S_IFREG
from flask import Flask, render_template_string, request, redirect, url_for

# Configuration defaults (can be overridden by env vars)
CHANGER_DEVICE = os.environ.get('CHANGER_DEVICE', '/dev/sg1')
TAPE_DEVICE = os.environ.get('TAPE_DEVICE', '/dev/st0')
DB_PATH = os.environ.get('DB_PATH', '/var/lib/tapevault/tapevault.db')
TEMP_MOUNT_BASE = os.environ.get('TEMP_MOUNT_BASE', '/tmp/ltfs_mounts')
WEB_PORT = int(os.environ.get('WEB_PORT', 5002))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s')
log = logging.getLogger('tapevault')

app = Flask(__name__)

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize the SQLite database."""
    db_dir = os.path.dirname(DB_PATH)
    if not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    
    conn = get_db_connection()
    c = conn.cursor()
    # Check if tapes table exists and has new columns
    try:
        c.execute("SELECT total_space FROM tapes LIMIT 1")
    except sqlite3.OperationalError:
        # Table might not exist or column missing. 
        # Simplest is to drop and recreate if we want to enforce schema, 
        # but to be safe let's just create if not exists and then try to alter.
        pass

    c.execute('''CREATE TABLE IF NOT EXISTS tapes (
                    vol_tag TEXT PRIMARY KEY,
                    last_seen INTEGER,
                    total_space INTEGER DEFAULT 0,
                    free_space INTEGER DEFAULT 0
                )''')
    
    # Try adding columns if they don't exist (for migration)
    try:
        c.execute("ALTER TABLE tapes ADD COLUMN total_space INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE tapes ADD COLUMN free_space INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    c.execute('''CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vol_tag TEXT,
                    path TEXT,
                    size INTEGER,
                    mtime INTEGER,
                    FOREIGN KEY(vol_tag) REFERENCES tapes(vol_tag)
                )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_files_path ON files (path)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_files_vol_tag ON files (vol_tag)')
    conn.commit()
    conn.close()

def run_command(cmd):
    """Run a shell command and return stdout."""
    log.debug(f"Running command: {cmd}")
    try:
        result = subprocess.run(cmd, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        log.error(f"Command failed: {cmd}\nError: {e.stderr}")
        raise

def parse_mtx_status():
    """Parse mtx status output to find full slots and their volume tags."""
    output = run_command(f"mtx -f {CHANGER_DEVICE} status")
    
    tapes = {} # slot_id -> vol_tag
    drive_loaded = None # vol_tag if drive is loaded
    
    for line in output.splitlines():
        line = line.strip()
        
        # Check for Data Transfer Element (Drive)
        drive_match = re.search(r'Data Transfer Element (\d+):Full.*VolumeTag\s*=\s*(\S+)', line)
        if drive_match:
            drive_id = drive_match.group(1)
            vol_tag = drive_match.group(2)
            drive_loaded = {'drive_id': drive_id, 'vol_tag': vol_tag}
            continue

        # Check for Storage Elements
        slot_match = re.search(r'Storage Element (\d+):Full.*VolumeTag\s*=\s*(\S+)', line)
        if slot_match:
            slot_id = slot_match.group(1)
            vol_tag = slot_match.group(2)
            tapes[slot_id] = vol_tag
            continue
            
        if 'IMPORT/EXPORT' in line:
            continue

    return tapes, drive_loaded

def inventory_and_index():
    """
    Main startup routine:
    1. Get list of tapes from mtx.
    2. Remove tapes from DB that are no longer in library.
    3. Index new tapes.
    """
    log.info("Starting inventory...")
    tapes_in_library, drive_loaded = parse_mtx_status()
    
    current_vol_tags = set(tapes_in_library.values())
    if drive_loaded:
        current_vol_tags.add(drive_loaded['vol_tag'])
    
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT vol_tag FROM tapes")
    db_vol_tags = set(row[0] for row in c.fetchall())
    
    # 2. Remove missing tapes
    missing_tapes = db_vol_tags - current_vol_tags
    for vol in missing_tapes:
        log.info(f"Removing missing tape {vol} from database.")
        c.execute("DELETE FROM files WHERE vol_tag=?", (vol,))
        c.execute("DELETE FROM tapes WHERE vol_tag=?", (vol,))
    conn.commit()
    
    # 3. Index new tapes
    new_tapes = current_vol_tags - db_vol_tags
    
    if not new_tapes:
        log.info("No new tapes to index.")
        conn.close()
        return

    log.info(f"Found {len(new_tapes)} new tapes to index: {new_tapes}")
    
    vol_to_slot = {v: k for k, v in tapes_in_library.items()}
    
    for vol in new_tapes:
        try:
            index_tape(vol, vol_to_slot.get(vol), drive_loaded)
        except Exception as e:
            log.error(f"Failed to index tape {vol}: {e}")
            
    conn.close()

def index_tape(vol_tag, slot_id, drive_loaded_info):
    """
    Load a tape (if not loaded), mount it, read index, unmount, unload (if it was loaded from slot).
    """
    log.info(f"Indexing tape {vol_tag}...")
    
    loaded_by_us = False
    
    # Check if already in drive
    if drive_loaded_info and drive_loaded_info['vol_tag'] == vol_tag:
        log.info(f"Tape {vol_tag} is already in the drive.")
    else:
        run_command(f"mtx -f {CHANGER_DEVICE} unload || true")
        
        if slot_id is None:
            raise Exception(f"Tape {vol_tag} not found in any slot and not in drive?")
            
        log.info(f"Loading tape {vol_tag} from slot {slot_id}...")
        run_command(f"mtx -f {CHANGER_DEVICE} load {slot_id} 0")
        loaded_by_us = True

    # Mount LTFS
    mount_point = os.path.join(TEMP_MOUNT_BASE, vol_tag)
    if not os.path.exists(mount_point):
        os.makedirs(mount_point)
        
    log.info(f"Mounting {vol_tag} to {mount_point}...")
    try:
        run_command(f"ltfs -o devname={TAPE_DEVICE} {mount_point}")
        
        # Get disk usage
        st = os.statvfs(mount_point)
        total_space = st.f_blocks * st.f_frsize
        free_space = st.f_bavail * st.f_frsize
        
        # Walk and index
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute("DELETE FROM files WHERE vol_tag=?", (vol_tag,))
        
        count = 0
        for root, dirs, files in os.walk(mount_point):
            for name in files:
                fpath = os.path.join(root, name)
                rel_path = os.path.relpath(fpath, mount_point)
                stat = os.stat(fpath)
                c.execute("INSERT INTO files (vol_tag, path, size, mtime) VALUES (?, ?, ?, ?)",
                          (vol_tag, rel_path, stat.st_size, int(stat.st_mtime)))
                count += 1
        
        c.execute("INSERT OR REPLACE INTO tapes (vol_tag, last_seen, total_space, free_space) VALUES (?, ?, ?, ?)", 
                  (vol_tag, int(time.time()), total_space, free_space))
        conn.commit()
        conn.close()
        log.info(f"Indexed {count} files for {vol_tag}. Total: {total_space}, Free: {free_space}")
        
    finally:
        log.info(f"Unmounting {vol_tag}...")
        run_command(f"umount {mount_point} || fusermount -u {mount_point}")
        
        if loaded_by_us:
            log.info(f"Unloading tape {vol_tag}...")
            run_command(f"mtx -f {CHANGER_DEVICE} unload {slot_id} 0")

class TapeVault(Operations):
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.tape_lock = threading.Lock()
        
    def getattr(self, path, fh=None):
        if path == '/':
            return dict(st_mode=(S_IFDIR | 0o755), st_nlink=2)
        
        clean_path = path.lstrip('/')
        
        c = self.conn.cursor()
        c.execute("SELECT size, mtime FROM files WHERE path=?", (clean_path,))
        row = c.fetchone()
        if row:
            return dict(st_mode=(S_IFREG | 0o444), st_nlink=1, st_size=row['size'], st_mtime=row['mtime'])
            
        c.execute("SELECT 1 FROM files WHERE path GLOB ? LIMIT 1", (clean_path + '/*',))
        if c.fetchone():
             return dict(st_mode=(S_IFDIR | 0o755), st_nlink=2)
             
        raise FuseOSError(errno.ENOENT)

    def readdir(self, path, fh):
        dirents = ['.', '..']
        clean_path = path.lstrip('/')
        if clean_path:
            prefix = clean_path + '/'
        else:
            prefix = ""
            
        c = self.conn.cursor()
        c.execute("SELECT path FROM files WHERE path GLOB ?", (prefix + '*',))
        
        seen = set()
        for row in c.fetchall():
            p = row['path']
            remainder = p[len(prefix):]
            parts = remainder.split('/')
            child = parts[0]
            if child and child not in seen:
                seen.add(child)
                dirents.append(child)
                
        return dirents

    def open(self, path, flags):
        clean_path = path.lstrip('/')
        
        c = self.conn.cursor()
        c.execute("SELECT vol_tag, size FROM files WHERE path=?", (clean_path,))
        rows = c.fetchall()
        if not rows:
            raise FuseOSError(errno.ENOENT)
            
        vol_tag = rows[0]['vol_tag']
        cache_path = os.path.join(TEMP_MOUNT_BASE, 'cache', vol_tag, clean_path)
        if os.path.exists(cache_path):
            return os.open(cache_path, flags)
            
        self.fetch_file(vol_tag, clean_path)
        if not os.path.exists(cache_path):
             # If fetch_file returned but file is still missing, something went wrong
             raise FuseOSError(errno.EIO)
        return os.open(cache_path, flags)

    def fetch_file(self, vol_tag, rel_path):
        with self.tape_lock:
            cache_path = os.path.join(TEMP_MOUNT_BASE, 'cache', vol_tag, rel_path)
            if os.path.exists(cache_path):
                return

            log.info(f"Fetching file {vol_tag}/{rel_path}...")
            tapes_in_library, drive_loaded = parse_mtx_status()
            
            slot_id = None
            for s, v in tapes_in_library.items():
                if v == vol_tag:
                    slot_id = s
                    break
            
            in_drive = (drive_loaded and drive_loaded['vol_tag'] == vol_tag)
            
            if not in_drive and not slot_id:
                log.error(f"Tape {vol_tag} not found for fetching file!")
                return

            if not in_drive:
                log.info("Unloading current tape...")
                run_command(f"mtx -f {CHANGER_DEVICE} unload || true")
                log.info(f"Loading tape {vol_tag} from slot {slot_id}...")
                run_command(f"mtx -f {CHANGER_DEVICE} load {slot_id} 0")
            
            mount_point = os.path.join(TEMP_MOUNT_BASE, vol_tag)
            if not os.path.exists(mount_point):
                os.makedirs(mount_point)
            
            try:
                run_command(f"ltfs -o devname={TAPE_DEVICE} {mount_point}")
                
                src = os.path.join(mount_point, rel_path)
                dst = cache_path
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                
                log.info(f"Copying {src} to {dst}...")
                import shutil
                shutil.copy2(src, dst)
                log.info("Copy complete.")
                
            except Exception as e:
                log.error(f"Error fetching file: {e}")
            finally:
                run_command(f"umount {mount_point} || fusermount -u {mount_point}")
                if not in_drive: 
                     run_command(f"mtx -f {CHANGER_DEVICE} unload {slot_id} 0")

    def read(self, path, length, offset, fh):
        os.lseek(fh, offset, os.SEEK_SET)
        return os.read(fh, length)

    def release(self, path, fh):
        os.close(fh)
        return 0

    def statfs(self, path):
        c = self.conn.cursor()
        c.execute("SELECT SUM(total_space), SUM(free_space) FROM tapes")
        row = c.fetchone()
        total = row[0] if row[0] else 0
        free = row[1] if row[1] else 0
        
        # Block size 4096
        bsize = 4096
        blocks = total // bsize
        bfree = free // bsize
        
        return dict(f_bsize=bsize, f_frsize=bsize, f_blocks=blocks, f_bfree=bfree, f_bavail=bfree)

# Flask Web UI
@app.route('/')
def index():
    conn = get_db_connection()
    tapes = conn.execute('SELECT * FROM tapes').fetchall()
    conn.close()
    
    html = """
    <html>
    <head><title>Tape Vault</title></head>
    <body>
        <h1>Tape Vault</h1>
        <table border="1">
            <tr>
                <th>Volume Tag</th>
                <th>Total Space</th>
                <th>Free Space</th>
                <th>Last Seen</th>
                <th>Actions</th>
            </tr>
            {% for tape in tapes %}
            <tr>
                <td><a href="/browse/{{ tape.vol_tag }}/">{{ tape.vol_tag }}</a></td>
                <td>{{ tape.total_space | filesizeformat }}</td>
                <td>{{ tape.free_space | filesizeformat }}</td>
                <td>{{ tape.last_seen }}</td>
                <td><a href="/delete/{{ tape.vol_tag }}">Delete</a></td>
            </tr>
            {% endfor %}
        </table>
    </body>
    </html>
    """
    return render_template_string(html, tapes=tapes)

@app.route('/browse/<vol_tag>/')
@app.route('/browse/<vol_tag>/<path:subpath>')
def browse(vol_tag, subpath=""):
    conn = get_db_connection()
    
    # We want to list files in this directory for this tape
    # But we merged the namespace in FUSE.
    # The requirement says "browse thier directories from the databse".
    # So we can show files belonging to this tape.
    
    # If subpath is empty, list root of tape
    if subpath:
        prefix = subpath + '/'
    else:
        prefix = ""
        
    # Find direct children in this tape
    # We use the same logic as readdir but filtered by vol_tag
    c = conn.cursor()
    c.execute("SELECT path, size FROM files WHERE vol_tag=? AND path GLOB ?", (vol_tag, prefix + '*'))
    
    files = []
    dirs = set()
    
    for row in c.fetchall():
        p = row['path']
        remainder = p[len(prefix):]
        parts = remainder.split('/')
        
        if len(parts) == 1:
            # It's a file
            files.append({'name': parts[0], 'size': row['size'], 'path': p})
        else:
            # It's a directory
            dirs.add(parts[0])
            
    conn.close()
    
    html = """
    <html>
    <head><title>Browse {{ vol_tag }}</title></head>
    <body>
        <h1>Browse {{ vol_tag }}: /{{ subpath }}</h1>
        <a href="/">Back to Tapes</a>
        <ul>
            {% for d in dirs %}
            <li><a href="/browse/{{ vol_tag }}/{{ subpath + '/' + d if subpath else d }}">{{ d }}/</a></li>
            {% endfor %}
            {% for f in files %}
            <li>{{ f.name }} ({{ f.size | filesizeformat }})</li>
            {% endfor %}
        </ul>
    </body>
    </html>
    """
    return render_template_string(html, vol_tag=vol_tag, subpath=subpath, dirs=sorted(list(dirs)), files=files)

@app.route('/delete/<vol_tag>')
def delete_tape(vol_tag):
    conn = get_db_connection()
    conn.execute('DELETE FROM files WHERE vol_tag=?', (vol_tag,))
    conn.execute('DELETE FROM tapes WHERE vol_tag=?', (vol_tag,))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

def start_web_server():
    app.run(host='0.0.0.0', port=WEB_PORT, debug=False, use_reloader=False)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mount-point', default='/mnt/tape-vault')
    args = parser.parse_args()
    
    init_db()
    
    # Start Web UI in background thread
    t = threading.Thread(target=start_web_server)
    t.daemon = True
    t.start()
    
    inventory_and_index()
    
    if not os.path.exists(args.mount_point):
        os.makedirs(args.mount_point)
        
    log.info(f"Starting FUSE filesystem at {args.mount_point}")
    fuse = FUSE(TapeVault(), args.mount_point, foreground=True, allow_other=True)

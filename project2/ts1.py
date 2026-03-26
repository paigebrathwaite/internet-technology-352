# ts1.py
import socket
import sys
from typing import Dict, Tuple, Optional

BACKLOG = 50
READ_TIMEOUT = 10.0
DB_FILE = "ts1database.txt"
LOG_FILE = "ts1responses.txt"

def recv_line(sock: socket.socket):
    """
    Read one '\n'-terminated line.
    Return None if the peer closes OR if we hit a read timeout before getting a newline.
    """
    buf = bytearray()
    while True:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            # treat idle/quiet connection as closed 
            return None if not buf else buf.decode('utf-8', errors='replace').strip()
        if not chunk:
            # peer closed
            return None if not buf else buf.decode('utf-8', errors='replace').strip()
        buf.extend(chunk)
        if b'\n' in chunk:
            line, _, _rest = buf.partition(b'\n')
            return line.decode('utf-8', errors='replace').strip()

def send_line(sock: socket.socket, line: str) -> None:
    sock.sendall((line + "\n").encode("utf-8"))

def parse_request(line: str):
    # "0 DomainName identification"
    parts = line.strip().split()
    if len(parts) != 3 or parts[0] != "0":
        return None
    return parts[1], parts[2]  # (domain, ident)

def make_resp_aa(domain_out: str, ip: str, ident: str) -> str:
    return f"1 {domain_out} {ip} {ident} aa"

def make_resp_nx(domain: str, ident: str) -> str:
    return f"1 {domain} 0.0.0.0 {ident} nx"


def load_ts_db(path: str = DB_FILE) -> Dict[str, Tuple[str, str]]:
    """
    Returns: lower(domain) -> (stored_domain_casing, ip)
    Accepts lines like:
        rutgers.edu 128.1.1.4
        njit.edu    10.5.6.7
    Ignores blank lines and lines starting with '#'.
    """
    db: Dict[str, Tuple[str, str]] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    dom, ip = parts[0], parts[1]
                    db[dom.lower()] = (dom, ip)
                # silently ignore malformed/extra tokens
    except FileNotFoundError:
        pass
    return db


def handle_client(conn: socket.socket, addr, db: Dict[str, Tuple[str, str]], log_fp) -> None:
    conn.settimeout(READ_TIMEOUT)
    try:
        while True:
            try:
                line = recv_line(conn)  # returns None on EOF or on timeout (with the patched recv_line)
            except socket.timeout:
                # if recv_line (or anything it calls) raised, just close gracefully
                return

            if line is None:
                # peer closed OR we hit a read timeout without a full line
                return

            parsed = parse_request(line)
            if not parsed:
                # ignore and continue waiting for next line on this connection
                continue

            domain, ident = parsed
            key = domain.lower()

            if key in db:
                dom_out, ip = db[key]
                resp = make_resp_aa(dom_out, ip, ident)
            else:
                resp = make_resp_nx(domain, ident)

            send_line(conn, resp)
            log_fp.write(resp + "\n")
            log_fp.flush()
    finally:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        conn.close()

def ts1(rudns_port_str: str):
    try:
        port = int(rudns_port_str)
    except ValueError:
        print(f"Invalid rudns_port: {rudns_port_str}")
        sys.exit(2)

    db = load_ts_db(DB_FILE)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("", port))
        srv.listen(BACKLOG)
        print(f"[TS1] Listening on TCP {port} with {len(db)} records")

        with open(LOG_FILE, "a", encoding="utf-8") as log_fp:
            while True:
                conn, addr = srv.accept()
                handle_client(conn, addr, db, log_fp)
    finally:
        srv.close()

if __name__ == "__main__":
    args = sys.argv
    ts1(args[1])
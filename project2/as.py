# as.py
import socket
import sys
from typing import Dict, Tuple, Optional

BACKLOG = 50
READ_TIMEOUT = 10.0
DB_FILE = "asdatabase.txt"
LOG_FILE = "asresponses.txt"


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
            # idle connecions should be closed
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
    return parts[1], parts[2]  # domain, ident

def make_resp_aa(domain_out: str, ip: str, ident: str) -> str:
    return f"1 {domain_out} {ip} {ident} aa"

def make_resp_nx(domain: str, ident: str) -> str:
    return f"1 {domain} 0.0.0.0 {ident} nx"



def load_authoritative_db(path: str = DB_FILE) -> Dict[str, Tuple[str, str]]:
    """
    Returns dict: lower(domain) -> (stored_domain_casing, ip)
    Accepts either:
      "A <DomainName> <IPAddress>"  or  "<DomainName> <IPAddress>"
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
                if not parts:
                    continue
                if parts[0].upper() == "A" and len(parts) >= 3:
                    dom, ip = parts[1], parts[2]
                elif len(parts) >= 2:
                    dom, ip = parts[0], parts[1]
                else:
                    continue
                db[dom.lower()] = (dom, ip)
    except FileNotFoundError:
        pass
    return db

def handle_client(conn: socket.socket, addr, db: Dict[str, Tuple[str, str]], log_fp) -> None:
    # Make sure you have: import socket at the top of the file
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
def as_server(rudns_port_str: str):
    try:
        port = int(rudns_port_str)
    except ValueError:
        print(f"invalid rudns_port: {rudns_port_str}")
        sys.exit(2)

    db = load_authoritative_db(DB_FILE)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("", port))
        srv.listen(BACKLOG)
        print(f"[AS] Listening on TCP {port} with {len(db)} records")

        with open(LOG_FILE, "a", encoding="utf-8") as log_fp:
            while True:
                conn, addr = srv.accept()
                # Single-threaded. For concurrency, spawn a thread per client.
                handle_client(conn, addr, db, log_fp)
    finally:
        srv.close()

if __name__ == "__main__":
    args = sys.argv
    if len(args) != 2:
        print("python as.py needs an input port")
        sys.exit(1)
    as_server(args[1])
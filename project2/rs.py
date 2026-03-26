# rs.py
import socket
import sys
from typing import Dict, Optional, Tuple, List

BACKLOG = 50
READ_TIMEOUT = 10.0
LOG_FILE = "rsresponses.txt"

# ---------- Protocol helpers ----------

import socket  # ensure this import is present at top

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
            # Treat idle/quiet connection as closed for robustness
            return None if not buf else buf.decode('utf-8', errors='replace').strip()
        if not chunk:
            # Peer closed
            return None if not buf else buf.decode('utf-8', errors='replace').strip()
        buf.extend(chunk)
        if b'\n' in chunk:
            line, _, _rest = buf.partition(b'\n')
            return line.decode('utf-8', errors='replace').strip()
def send_line(sock: socket.socket, line: str) -> None:
    sock.sendall((line + "\n").encode("utf-8"))

def parse_request(line: str) -> Optional[Tuple[str, str, str]]:
    # "0 DomainName identification"
    parts = line.strip().split()
    if len(parts) != 3 or parts[0] != "0":
        return None
    return parts[0], parts[1], parts[2]

def make_ns(domain: str, ns_host: str, ident: str) -> str:
    # ns uses hostname in the "IPAddress" field per spec
    return f"1 {domain} {ns_host} {ident} ns"

def make_aa(domain: str, ip: str, ident: str) -> str:
    return f"1 {domain} {ip} {ident} aa"

def make_nx(domain: str, ident: str) -> str:
    return f"1 {domain} 0.0.0.0 {ident} nx"

# ---------- DB loading & matching ----------

class RSConfig:
    """
    Tolerant parser for rsdatabase.txt

    Supported lines (examples):
      # comments allowed
      A example.com 93.184.216.34
      NS com ts1.example.edu
      NS edu ts2.example.edu
      NS ai as.example.edu      # if AS handles .ai like a TLD
      ZONE co.uk ts2.example.edu # optional: multi-label 'TLD'/zone
    """
    def __init__(self) -> None:
        # lower(domain) -> (stored_casing_domain, ip)
        self.a_records: Dict[str, Tuple[str, str]] = {}
        # zones as list of tuples: (zone_suffix_lower, zone_host, stored_zone_token)
        # Match by case-insensitive suffix ('.' + zone) or exact zone for bare matches
        self.zones: List[Tuple[str, str, str]] = []

    def load(self, path: str = "rsdatabase.txt") -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    key = parts[0].upper()
                    if key == "A" and len(parts) >= 3:
                        domain, ip = parts[1], parts[2]
                        self.a_records[domain.lower()] = (domain, ip)
                    elif key in ("NS", "ZONE") and len(parts) >= 3:
                        zone, host = parts[1], parts[2]
                        # Store normalized lowercase for matching, but keep original zone token for debug
                        self.zones.append((zone.lower(), host, parts[1]))
                    else:
                        # ignore other lines
                        pass
        except FileNotFoundError:
            # OK: RS may run with an empty DB (only NS redirects)
            pass

    @staticmethod
    def _labels(domain: str) -> List[str]:
        return domain.split(".")

    @staticmethod
    def _lc(s: str) -> str:
        return s.lower()

    def find_zone_host(self, domain: str) -> Optional[str]:
        dl = self._lc(domain)
        best_len = -1
        best_host: Optional[str] = None
        for zone_lower, host, _stored in self.zones:
            # Match if domain == zone OR domain endswith '.' + zone
            if dl == zone_lower or dl.endswith("." + zone_lower):
                if len(zone_lower) > best_len:
                    best_len = len(zone_lower)
                    best_host = host
        return best_host

    def lookup_a(self, domain: str) -> Optional[Tuple[str, str]]:
        return self.a_records.get(domain.lower())

def handle_client(conn: socket.socket, addr, cfg: RSConfig, log_fp) -> None:
    conn.settimeout(READ_TIMEOUT)

    def reply(resp: str):
        # try to send; even if the client goes away, still log the response per spec.
        try:
            send_line(conn, resp)
        except (socket.timeout, BrokenPipeError, ConnectionResetError):
            pass
        log_fp.write(resp + "\n")
        log_fp.flush()

    try:
        while True:
            try:
                line = recv_line(conn)  # returns None on EOF or timeout 
            except socket.timeout:
                # treat idle connection as done
                return

            if line is None:
                return

            parsed = parse_request(line)
            if not parsed:
                # ignore malformed per spec
                continue

            _tag, domain, ident = parsed

            # 1) If domain is in a managed zone → ns redirection
            zone_host = cfg.find_zone_host(domain)
            if zone_host:
                reply(make_ns(domain, zone_host, ident))
                continue

            # 2) Otherwise, try RS's own A records
            a = cfg.lookup_a(domain)
            if a:
                stored_domain, ip = a
                reply(make_aa(stored_domain, ip, ident))
                continue

            # 3) Not found → nx
            reply(make_nx(domain, ident))

    finally:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        conn.close()
def rs(rudns_port_str: str):
    try:
        rudns_port = int(rudns_port_str)
    except ValueError:
        print(f"Invalid rudns_port: {rudns_port_str}")
        sys.exit(2)

    cfg = RSConfig()
    cfg.load("rsdatabase.txt")

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("", rudns_port))
        srv.listen(BACKLOG)
        print(f"[RS] Listening on TCP port {rudns_port}")

        with open(LOG_FILE, "a", encoding="utf-8") as log_fp:
            while True:
                conn, addr = srv.accept()
                # single-threaded; for concurrency, spawn a thread per connection
                handle_client(conn, addr, cfg, log_fp)
    finally:
        srv.close()

if __name__ == "__main__":
    args = sys.argv
    if len(args) != 2:
        print("Usage: python rs.py <rudns_port>")
        sys.exit(1)
    rs(args[1])
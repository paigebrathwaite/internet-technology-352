# ls.py
import socket
import sys
from typing import Dict, Tuple, Optional

BACKLOG = 50
READ_TIMEOUT = 10.0

def _connect_host_for(name: str) -> str:
    try:
        socket.getaddrinfo(name, None)   # does it resolve?
        return name
    except socket.gaierror:
        return "127.0.0.1"  

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
    sock.sendall((line + "\n").encode('utf-8'))

def parse_request(line: str):
    # "0 DomainName identification"
    parts = line.strip().split()
    if len(parts) != 3 or parts[0] != "0":
        return None
    return parts[1], parts[2]  # (domain, ident)

def parse_response(line: str):
    # "1 DomainName IPAddress identification flags"
    parts = line.strip().split()
    if len(parts) != 5 or parts[0] != "1":
        return None
    # tag=1, domain, ip_or_host, ident, flags
    return parts[1], parts[2], parts[3], parts[4]  # (domain, ip/host, ident, flags)

def make_resp_aa(domain_out: str, ip: str, ident: str) -> str:
    return f"1 {domain_out} {ip} {ident} aa"

def make_resp_nx(domain: str, ident: str) -> str:
    return f"1 {domain} 0.0.0.0 {ident} nx"

LOG_FILE = "lsresponses.txt"

import atexit

def dump_cache_to_file(cfg, path: str = "cache.txt") -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            for _k, (dom, ip) in sorted(cfg.cache.items(), key=lambda x: x[1][0].lower()):
                f.write(f"{dom} {ip}\n")
    except Exception:
        pass

class LSConfig:
    def __init__(self) -> None:
        # local cache: lower(domain) -> (stored_domain_casing, ip)
        self.cache: Dict[str, Tuple[str, str]] = {}
        # count authoritative hits for potential caching
        self.aa_counts: Dict[str, int] = {}

        # outbound connection port: set by ls() from CLI (spec uses same port everywhere)
        self.outbound_port: int = 0

        self.tld1: Optional[str] = None  
        self.tld2: Optional[str] = None  
        self.rs_host: Optional[str]  = None
        self.ts1_host: Optional[str] = None
        self.ts2_host: Optional[str] = None
        self.as_host: Optional[str]  = None

        self.rs_port: Optional[int] = None
        self.hostport: Dict[str, int] = {} # hostname -> port

    def _load_config_format(self, f):
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            key = parts[0].upper()
            if key == "A" and len(parts) >= 3:
                dom, ip = parts[1], parts[2]
                self.cache[dom.lower()] = (dom, ip)
            elif key == "CONFIG" and len(parts) >= 3:
                sub = parts[1].upper()
                val = parts[2]
                if sub == "RS_HOST": self.rs_host = val
                elif sub == "RS_PORT":
                    try: self.rs_port = int(val)
                    except: pass
                elif sub == "TS1_HOST": self.ts1_host = val
                elif sub == "TS2_HOST": self.ts2_host = val
                elif sub == "AS_HOST":  self.as_host  = val
                elif sub.endswith("_PORT"):
                    #  allow TS1_PORT/TS2_PORT/AS_PORT overrides
                    try:
                        port = int(val)
                        for h in [self.ts1_host, self.ts2_host, self.as_host]:
                            if h and h not in self.hostport:
                                self.hostport[h] = port
                    except:
                        pass
            elif key == "HOSTPORT" and len(parts) >= 3:
                host, port = parts[1], parts[2]
                try:
                    self.hostport[host] = int(port)
                except:
                    pass

    def _load_6line_spec_format(self, path: str):
        """
        Spec-format: first two non-empty, non-comment lines are TLDs,
        next four are RS, TS1, TS2, AS hostnames (in that order).
        """
        with open(path, "r", encoding="utf-8") as f2:
            lines = []
            for raw in f2:
                s = raw.strip()
                if not s or s.startswith("#"):
                    continue
                # ignore cache 'A ' lines here; only 6 header lines matter
                if s and not s.upper().startswith("A "):
                    lines.append(s)
                if len(lines) >= 6:
                    break

        if len(lines) < 6:
            raise RuntimeError("lsdatabase.txt: expecting at least 6 spec header lines (TLDs + RS/TS1/TS2/AS)")

        self.tld1, self.tld2 = lines[0], lines[1]
        self.rs_host, self.ts1_host, self.ts2_host, self.as_host = lines[2], lines[3], lines[4], lines[5]

        with open(path, "r", encoding="utf-8") as f3:
            for raw in f3:
                s = raw.strip()
                if not s or s.startswith("#"):
                    continue
                if s.upper().startswith("A "):
                    parts = s.split()
                    if len(parts) >= 3:
                        dom, ip = parts[1], parts[2]
                        self.cache[dom.lower()] = (dom, ip)

    def load(self, path: str = "lsdatabase.txt") -> None:
        """
        Attempts 'CONFIG' format first (back-compat).
        If no RS_HOST was found, falls back to 6-line spec format.
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                self._load_config_format(f)
        except FileNotFoundError:
            pass

        # If we didn't get RS host via CONFIG, try spec 6-line format
        if not self.rs_host:
            self._load_6line_spec_format(path)

        # If no explicit per-host ports were configured, default to ONE outbound port
        # (the LS CLI port), which ls() will set before calling load().
        # rs_port is optional; if missing, we'll use outbound_port at connect time.
        # Clean any placeholder entries that never got a port
        for h, p in list(self.hostport.items()):
            if p in (-1, None):
                del self.hostport[h]

        # require rs_host and outbound_port to be set by ls()
        if not self.rs_host:
            raise RuntimeError("Missing RS hostname in lsdatabase.txt")
        if not self.outbound_port:
            raise RuntimeError("LS outbound_port not set; ensure ls() sets cfg.outbound_port from CLI port")
        

def query_server(host: str, port: int, line_wo_newline: str) -> str:
    """Open TCP, send one line, read one line, return it (without trailing newline)."""
    with socket.create_connection((host, port), timeout=READ_TIMEOUT) as s:
        send_line(s, line_wo_newline)
        s.settimeout(READ_TIMEOUT)
        resp = recv_line(s)
        if resp is None:
            raise ConnectionError(f"{host}:{port} closed without response")
        return resp


def resolve_iterative(domain: str, client_ident: str, cfg: LSConfig) -> str:
    rs_port = cfg.rs_port if cfg.rs_port else cfg.outbound_port
    rs_req = f"0 {domain} {client_ident}"
    rs_host = _connect_host_for(cfg.rs_host)  
    try:
        rs_resp = query_server(rs_host, rs_port, rs_req)
    except Exception:
        return make_resp_nx(domain, client_ident)

    parsed = parse_response(rs_resp)
    if not parsed:
        return make_resp_nx(domain, client_ident)

    resp_domain, ip_or_host, _rs_ident, flags = parsed

    if flags == "ns":
    
        try:
            next_id = str(int(client_ident) + 1)
        except ValueError:
            next_id = client_ident + "_1"

        tld_host = ip_or_host                          # e.g., "ts1.local"
        tld_port = cfg.hostport.get(tld_host, (cfg.rs_port or cfg.outbound_port))

        # choose a connect host that works locally if name doesn't resolve
        connect_host = _connect_host_for(tld_host)  # returns "127.0.0.1" if ts1.local isn’t resolvable

        tld_req = f"0 {domain} {next_id}"
        try:
            tld_resp = query_server(connect_host, tld_port, tld_req)
        except Exception:
            return make_resp_nx(domain, client_ident)

        parsed2 = parse_response(tld_resp)
        if not parsed2:
            return make_resp_nx(domain, client_ident)

        d2, ip2, _id2, flags2 = parsed2

        if flags2 == "aa":
            key = d2.lower()
            cfg.aa_counts[key] = cfg.aa_counts.get(key, 0) + 1
            if cfg.aa_counts[key] > 3 and key not in cfg.cache:
                cfg.cache[key] = (d2, ip2)

        # ret final result to the client with the original ident
        return f"1 {d2} {ip2} {client_ident} {flags2}"

    elif flags in ("aa", "nx"):
        # RS answered directly; relay with original ident
        if flags == "aa":
            key = resp_domain.lower()
            cfg.aa_counts[key] = cfg.aa_counts.get(key, 0) + 1
            if cfg.aa_counts[key] > 3 and key not in cfg.cache:
                cfg.cache[key] = (resp_domain, ip_or_host)
        return f"1 {resp_domain} {ip_or_host} {client_ident} {flags}"

    return make_resp_nx(domain, client_ident)

def handle_client(conn: socket.socket, addr, cfg: LSConfig, log_fp) -> None:
    conn.settimeout(READ_TIMEOUT)
    try:
        while True:
            line = recv_line(conn)
            if line is None:
                return
            parsed = parse_request(line)
            if not parsed:
                continue

            domain, client_ident = parsed
            key = domain.lower()

            # local cache first
            hit = cfg.cache.get(key)
            if hit:
                dom_out, ip = hit
                resp = make_resp_aa(dom_out, ip, client_ident)
                send_line(conn, resp)
                log_fp.write(resp + "\n"); log_fp.flush() #log LS->client
                continue

            # iterative resolution 
            resp = resolve_iterative(domain, client_ident, cfg)
            send_line(conn, resp)
            log_fp.write(resp + "\n"); log_fp.flush() #log LS->client
    finally:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        conn.close()

def ls(rudns_port_str: str):
    try:
        port = int(rudns_port_str)
    except ValueError:
        print(f"Invalid rudns_port: {rudns_port_str}")
        sys.exit(2)

    cfg = LSConfig()
    cfg.outbound_port = port
    cfg.load("lsdatabase.txt")

    # dump cache at process exit
    atexit.register(dump_cache_to_file, cfg)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('', port))
        srv.listen(BACKLOG)
        print(f"[LS] Listening on TCP {port}; RS host={cfg.rs_host} (port={cfg.rs_port or cfg.outbound_port})")

        with open(LOG_FILE, "a", encoding="utf-8") as log_fp: # open once
            while True:
                conn, addr = srv.accept()
                handle_client(conn, addr, cfg, log_fp) # pass it in
    finally:
        srv.close()

if __name__ == '__main__':
    args = sys.argv
    if len(args) != 2:
        print("Usage: python ls.py <rudns_port>")
        sys.exit(1)
    ls(args[1])
# client.py (TCP version)
import socket
import sys

TIMEOUT_SEC = 5.0

def build_request(domain: str, ident: int) -> str:
    # RU-DNS request: "0 DomainName identification"
    return f"0 {domain} {ident}\n"  # newline-delimited framing

def recv_line(sock: socket.socket) -> str:
    """
    Read a single '\n'-terminated UTF-8 line from a TCP stream.
    Returns the line without the trailing newline.
    """
    buf = bytearray()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            # connection closed before newline arrived
            raise ConnectionError("Socket closed while waiting for a line")
        buf.extend(chunk)
        if b'\n' in chunk:
            break
    # split at first newline and keep data before it
    line, _, _rest = buf.partition(b'\n')
    return line.decode('utf-8', errors='replace').strip()

def client(ls_hostname: str, rudns_port_str: str):
    try:
        rudns_port = int(rudns_port_str)
    except ValueError:
        print(f"Invalid rudns_port: {rudns_port_str}")
        sys.exit(2)

    # Load hostnames (lowercased, skip blanks/comments)
    with open('hostnames.txt', 'r', encoding='utf-8') as fd:
        requests = []
        for raw in fd:
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            requests.append(line.lower())

    # prepare output file
    out = open('resolved.txt', 'w', encoding='utf-8')

    # create one persistent TCP connection to LS
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(TIMEOUT_SEC)

    sock.connect((ls_hostname, rudns_port))

    identification = 1

    try:
        for domain in requests:
            # send request
            req_line = build_request(domain, identification)
            sock.sendall(req_line.encode('utf-8'))

            # read single response line and write it as-is
            resp_line = recv_line(sock)

            # minimal validation: response should start with "1 " and contain the id
            # but we always record exactly what LS sent.
            out.write(resp_line + "\n")
            out.flush()

            identification += 1
    finally:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        sock.close()
        out.close()

if __name__ == '__main__':
    args = sys.argv
    client(args[1], args[2])
import threading
import time
import random
import socket
import select
import argparse
from functools import reduce

# settings
# retransmission timeout
RTO = 0.500
# number of application bytes in one packet
CHUNK_SIZE = 8
# initial sequence number for sender transmissions
INIT_SEQNO = 5
# dummy ack number for sender's packets
__ACK_UNUSED = 2345367

# watchdogs (no cli changes)
MAX_CONSEC_TIMEOUTS = 12 # ~6 seconds with rto = 0.5
MAX_TOTAL_RETX = 5000 # hard safety cap
STUCK_PROGRESS_SECS = 10.0 # abort if no forward ack progress for this long

class Msg:
    def __init__(self, seq, ack, msg):
        self.seq = int(seq)
        self.ack = int(ack)
        self.msg = str(msg)
        self.len = len(self.msg)

    def serialize(self):
        ser_repr = (
            str(self.seq)
            + " | "
            + str(self.ack)
            + " | "
            + str(self.len)
            + " | "
            + self.msg
        )
        return ser_repr.encode("utf-8")

    def __str__(self):
        repr = "Seq: " + str(self.seq) + "   "
        repr += "ACK: " + str(self.ack) + "   "
        repr += "Len: " + str(self.len) + "   "
        repr += "Msg: " + self.msg.strip()
        return repr

    @staticmethod
    def deserialize(ser_bytes_msg):
        ser_msg = ser_bytes_msg.decode("utf-8")
        parts = ser_msg.split("|")
        if len(parts) >= 4:
            return Msg(int(parts[0]), int(parts[1]), "|".join(parts[3:])[1:])
        else:
            print("Error in deserializing into Msg object.")
            exit(-1)

def init_socket(receiver_binding):
    try:
        cs = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        print("[S]: Sender socket created")
    except socket.error as err:
        print("socket open error: {} \n".format(err))
        exit()
    return cs

def get_filedata(filename):
    print("[S] Transmitting file {}".format(filename))
    f = open(filename, "r")
    filedata = f.read()
    f.close()
    return filedata

def chunk_data(filedata):
    global CHUNK_SIZE
    global INIT_SEQNO
    messages = [
        filedata[i : i + CHUNK_SIZE] for i in range(0, len(filedata), CHUNK_SIZE)
    ]
    messages = [str(len(filedata))] + messages
    content_len = reduce(lambda x, y: x + len(y), messages, 0)
    seq_to_msgindex = {}
    accumulated = INIT_SEQNO
    for i in range(0, len(messages)):
        seq_to_msgindex[accumulated] = i
        accumulated += len(messages[i])
    return messages, content_len, seq_to_msgindex

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--port",
        type=int,
        help="receiver port to connect to (default 50007)",
        default=50007,
    )
    parser.add_argument(
        "--infile",
        type=str,
        help="name of input file (default test-input.txt)",
        default="test-input.txt",
    )
    parser.add_argument(
        "--winsize",
        type=int,
        help="Window size to use in pipelined reliability",
        default=20,
    )
    args = parser.parse_args()
    return vars(args)

############################################
# main reliable sending loop
############################################
# main reliable sending loop
def send_reliable(cs, filedata, receiver_binding, win_size):
    global RTO
    global INIT_SEQNO
    global __ACK_UNUSED

    # break the file into fixed-size messages and assign sequence numbers
    messages, content_len, seq_to_msgindex = chunk_data(filedata)

    # the sliding-window edges (byte offsets)
    win_left_edge = INIT_SEQNO
    win_right_edge = min(win_left_edge + win_size, INIT_SEQNO + content_len)

    # turn verbose logging on/off (helps speed for long transfers)
    VERBOSE = False

    # prints only when verbose=True
    def vprint(*args, **kwargs):
        if VERBOSE:
            print(*args, **kwargs)

    # ------------------------------- window burst send -------------------------------
    # send all packets from the current window, starting at left_edge.
    # this is used for both the initial burst *and* when window slides.
    def transmit_entire_window_from(left_edge):
        latest_tx = left_edge
        while latest_tx < win_right_edge:
            # look up payload for packet starting at latest_tx seqno
            assert latest_tx in seq_to_msgindex
            idx = seq_to_msgindex[latest_tx]
            payload = messages[idx]

            # build and send packet
            m = Msg(latest_tx, __ACK_UNUSED, payload)
            cs.sendto(m.serialize(), receiver_binding)

            # track that this was a fresh send
            log_send("FRESH", latest_tx, len(payload))
            vprint("Transmitted {}".format(str(m)))

            # move to next packet in sequence space
            latest_tx += len(payload)

        return latest_tx

    # ------------------------------- single RETX for left edge -------------------------------
    # retransmit only the oldest unacked packet (go-back-n behavior on timeout)
    def retransmit_left_edge():
        assert win_left_edge in seq_to_msgindex
        idx = seq_to_msgindex[win_left_edge]
        payload = messages[idx]

        m = Msg(win_left_edge, __ACK_UNUSED, payload)
        cs.sendto(m.serialize(), receiver_binding)

        # mark as a retransmission
        log_send("RETX", win_left_edge, len(payload))
        vprint("Transmitted {}".format(str(m)) + "  [RETX]")

        return win_left_edge + len(payload)

    # ------------------------------- debug helpers -------------------------------
    import sys
    from datetime import datetime
    DEBUG = False
    DEBUG_HEARTBEAT = 1.0

    # timestamp for debugging logs
    def ts():
        return datetime.now().strftime("%H:%M:%S.%f")[:-3]

    # debug logger
    def dbg(*args, **kwargs):
        if DEBUG:
            print("[DBG", ts(), "]", *args, **kwargs, file=sys.stderr)

    # counters for instrumentation (not required by protocol, but useful)
    tx_seq_counter = 0
    sent_at = {} # timestamp when each seq was (re)sent
    retx_count = {} # number of retransmissions per seq
    last_heartbeat = time.time()

    # log each send/retransmission in a consistent way
    def log_send(kind, seq, payload_len):
        nonlocal tx_seq_counter
        tx_seq_counter += 1
        sent_at[seq] = time.time()
        if kind != "FRESH":
            retx_count[seq] = retx_count.get(seq, 0) + 1
        dbg(f"SEND#{tx_seq_counter} {kind}: seq={seq} len={payload_len} "
            f"(retx_for_seq={retx_count.get(seq,0)})")

    # final byte offset the sender expects to be cumulatively ACKed
    final_ack = INIT_SEQNO + content_len

    # -------------------------------- ack bookkeeping --------------------------------
    # track which packets have been fully acknowledged
    acked = {seq: False for seq in seq_to_msgindex.keys()}

    # map cumulative ACK numbers (which refer to end positions) to their packet's start seq
    endseq_to_seq = {}
    for seq_start, idx in seq_to_msgindex.items():
        plen = len(messages[idx])
        endseq_to_seq[seq_start + plen] = seq_start

    last_acked = INIT_SEQNO # cumulative ACK boundary
    dup_ack_cnt = 0 # count repeated ACKs to detect fast retransmit

    win_left_edge = INIT_SEQNO
    win_right_edge = min(win_left_edge + win_size, final_ack)
    first_to_tx = win_left_edge

    # -------------------------------- window sliding helper --------------------------------
    # slide win_left_edge forward as long as packets are ACKed contiguously
    def slide_window_left():
        nonlocal win_left_edge, last_acked
        s = win_left_edge
        # walk forward in seq space until you hit an unacked packet
        while s in acked and acked[s]:
            idx = seq_to_msgindex[s]
            s += len(messages[idx])
        win_left_edge = s
        last_acked = s

    # ====================== transmit header using stop-and-wait ======================
    # the header carries the file length and must be reliably received before pipelining begins
    hdr_seq = INIT_SEQNO
    hdr_idx = seq_to_msgindex[hdr_seq]
    hdr_msg = Msg(hdr_seq, __ACK_UNUSED, messages[hdr_idx])
    hdr_goal_ack = hdr_seq + len(hdr_msg.msg)

    while last_acked < hdr_goal_ack:
        # send header packet
        cs.sendto(hdr_msg.serialize(), receiver_binding)
        vprint("Transmitted {}  [HDR]".format(str(hdr_msg)))

        # wait for ack or timeout
        rlist, _, _ = select.select([cs], [], [], RTO)
        if not rlist:
            # header not yet acked -> try again
            vprint("[HDR] timeout; retransmitting length header")
            continue

        # got a packet back from receiver
        data_from_receiver, addr = cs.recvfrom(4096)
        rx = Msg.deserialize(data_from_receiver)

        # receiver never sends data payloads back, so len==0 for ACKs
        if rx.len == 0:
            ackno = rx.ack

            # clamp ack to header goal (defensive)
            if ackno > hdr_goal_ack:
                ackno = hdr_goal_ack

            # if header ack arrived, mark it as acked
            if ackno >= hdr_goal_ack:
                acked[hdr_seq] = True
                slide_window_left()
                win_right_edge = min(win_left_edge + win_size, final_ack)

    # ====================== initial pipelined "burst" ======================
    first_to_tx = transmit_entire_window_from(win_left_edge)
    dbg(f"AFTER_INITIAL_BURST: last_acked={last_acked}, "
        f"win=[{win_left_edge},{win_right_edge}), first_to_tx={first_to_tx}")

    # ====================== main pipelined sending loop ======================
    fast_retx_guard_ack = None
    consec_timeouts = 0
    total_retx = 0
    total_timeouts = 0
    last_progress_time = time.time()
    last_acked_reported = last_acked

    # run until all bytes are cumulatively acknowledged
    while last_acked < final_ack:

        # periodic debug output
        if DEBUG and (time.time() - last_heartbeat) >= DEBUG_HEARTBEAT:
            dbg(f"HEARTBEAT: last_acked={last_acked}, "
                f"win=[{win_left_edge},{win_right_edge}), first_to_tx={first_to_tx}")
            last_heartbeat = time.time()

        # abort if window has not moved for too long (safety purposes)
        if (time.time() - last_progress_time) > STUCK_PROGRESS_SECS:
            print(f"[S] Aborting: no ACK progress for {STUCK_PROGRESS_SECS:.1f}s "
                  f"(last_acked={last_acked}, final_ack={final_ack}).")
            break

        # ------------------------------- wait for ACK -------------------------------
        rlist, _, _ = select.select([cs], [], [], RTO)

        if rlist:
            # received an ACK
            data_from_receiver, addr = cs.recvfrom(4096)
            rx = Msg.deserialize(data_from_receiver)
            vprint(f"[S<-R] rx packet from {addr}: {rx}")
            dbg(f"RX: from={addr} parsed={str(rx)}")

            if rx.len != 0:
                # receiver never sends payloads back
                dbg("IGNORED_RX_WITH_DATA")
                continue

            ackno = rx.ack
            if ackno > final_ack:
                ackno = final_ack

            # mark every packet fully covered by cumulative ACK
            progress_made = False
            for seq_start, idx in seq_to_msgindex.items():
                pkt_end = seq_start + len(messages[idx])
                if pkt_end <= ackno and not acked[seq_start]:
                    acked[seq_start] = True
                    progress_made = True

            if progress_made:
                # new progress -> reset dup counter + timeout counters
                consec_timeouts = 0
                dup_ack_cnt = 0
                fast_retx_guard_ack = None
                last_progress_time = time.time()

                old_left = win_left_edge
                slide_window_left()
                win_right_edge = min(win_left_edge + win_size, final_ack)

                dbg(f"ACK_NEW: ackno={ackno}, "
                    f"win_left_edge {old_left}->{win_left_edge}, win_right_edge={win_right_edge}")

                # send any newly exposed packets in window
                if first_to_tx < win_right_edge:
                    prev_first = first_to_tx
                    first_to_tx = transmit_entire_window_from(first_to_tx)
                    dbg(f"FILL_WINDOW: {prev_first}->{first_to_tx}")

                if last_acked >= final_ack:
                    break

            else:
                # duplicate ack situation => check for fast retransmit
                dup_ack_cnt += 1
                consec_timeouts = 0
                dbg(f"ACK_DUP: ackno={ackno}, dup_cnt={dup_ack_cnt}")

                # fast retransmit trigger after 2 dup acks
                DUP_THRESH = 2
                if dup_ack_cnt >= DUP_THRESH and fast_retx_guard_ack != win_left_edge:
                    retransmit_left_edge()
                    total_retx += 1
                    fast_retx_guard_ack = win_left_edge

        else:
            # ------------------------------- timeout -------------------------------
            total_timeouts += 1
            dbg(f"TIMEOUT at seq={win_left_edge}")

            if win_left_edge >= final_ack:
                break

            consec_timeouts += 1
            if consec_timeouts >= MAX_CONSEC_TIMEOUTS:
                print(f"[S] Aborting: {consec_timeouts} consecutive timeouts at seq={win_left_edge}.")
                break

            # timeout -> retransmit oldest unacked packet
            retransmit_left_edge()
            total_retx += 1
            fast_retx_guard_ack = None

            if total_retx >= MAX_TOTAL_RETX:
                print(f"[S] Aborting: exceeded MAX_TOTAL_RETX={MAX_TOTAL_RETX}.")
                break

    dbg(f"[S] Stats: total_timeouts={total_timeouts}, total_retransmissions={total_retx}")

if __name__ == "__main__":
    args = parse_args()
    filedata = get_filedata(args["infile"])
    receiver_binding = ("", args["port"])
    cs = init_socket(receiver_binding)
    send_reliable(cs, filedata, receiver_binding, args["winsize"])
    cs.close()
    print("[S] Sender finished all transmissions.")
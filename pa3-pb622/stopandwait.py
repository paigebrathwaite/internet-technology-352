import threading
import time
import random
import socket
import select
import argparse
from functools import reduce

# Settings
# Retransmission timeout
RTO = 0.500
# Number of application bytes in one packet
CHUNK_SIZE = 8
# Initial sequence number for sender transmissions
INIT_SEQNO = 5
# dummy ACK number for sender's packets
__ACK_UNUSED = 2345367


# Message class: we use this class to structure our protocol
# message. The fields in our protocol are:
# seq no: the starting sequence number of application bytes
# on this packet
# ack no: the cumulative ACK number of application bytes
# being acknowledged in this ACK
# len: the number of application bytes being transmitted on
# this packet
# msg: the actual application payload on this packet
# The methods `serialize` and `deserialize` allow the
# conversion of a protocol object to bytes transmissible
# through a sendto() system call and the bytes from a
# recvfrom() system call into a protocol structure.
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


### Helper methods.
#### Initialize a UDP socket
def init_socket(receiver_binding):
    try:
        cs = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        print("[S]: Sender socket created")
    except socket.error as err:
        print("socket open error: {} \n".format(err))
        exit()
    return cs


#### Slurp a file into a single string.
#### Warning: do not use on very large files
def get_filedata(filename):
    print("[S] Transmitting file {}".format(filename))
    f = open(filename, "r")
    filedata = f.read()
    f.close()
    return filedata


#### Chunk a large string into fixed size chunks.
#### The first chunk is a string with the number of
#### following chunks.
#### `seq_to_msgindex` tracks the index of the packet
#### that will contain a given sequence number as its
#### starting sequence number.
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


#### Parse command line arguments
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
# Main reliable sending loop
def send_reliable(cs, filedata, receiver_binding, win_size):
    global RTO
    global INIT_SEQNO
    global __ACK_UNUSED
    messages, content_len, seq_to_msgindex = chunk_data(filedata)

    win_left_edge = INIT_SEQNO
    win_right_edge = min(win_left_edge + win_size, INIT_SEQNO + content_len)

    # Method to transmit all data between window left and
    # right edges. Typically used for just fresh
    # transmissions (retransmissions use transmit_one()).

   # ------------- helper: (leave TODO for pipelining in Phase 2) -------------
    def transmit_entire_window_from(left_edge):
        latest_tx = left_edge
        while latest_tx < win_right_edge:
            assert latest_tx in seq_to_msgindex
            # (Phase 2) Build Msg from messages[seq_to_msgindex[latest_tx]] and send.
            # For stop-and-wait we don't use this helper yet.
            break  # placeholder to avoid NotImplementedError during Phase 1
        return latest_tx  # return the seqno after last byte sent

        # raise NotImplementedError("transmit_entire_window_from() not implemented")

    # transmit one packet from the left edge of the window; this is used for retransmissions in pipelined reliability, and also for fresh transmissions in
    # stop-and-wait reliability
    def transmit_one():
        assert win_left_edge in seq_to_msgindex
        index = seq_to_msgindex[win_left_edge]
        msg = messages[index]
        m = Msg(win_left_edge, __ACK_UNUSED, msg)
        cs.sendto(m.serialize(), receiver_binding)
        print("Transmitted {}".format(str(m)))
        return win_left_edge + len(msg)

    # TODO: This is where you will make the rest of your changes.
     # ====================== STOP-AND-WAIT (Phase 1) ======================
    final_ack = INIT_SEQNO + content_len # done when last_acked == final_ack
    last_acked = INIT_SEQNO # cumulative ACK we've seen
    win_left_edge = INIT_SEQNO # first unACKed byte
    first_to_tx = win_left_edge

    # send the first packet
    first_to_tx = transmit_one()

    # replace the starter "blast loop" with this reliable loop
    while last_acked < final_ack:
        rlist, _, _ = select.select([cs], [], [], RTO)
        if rlist:
            # got something to read: parse ACK
            data_from_receiver, _ = cs.recvfrom(4096)
            ack_msg = Msg.deserialize(data_from_receiver)

            # ignore any packet that is not a pure ack (no payload)
            if ack_msg.len != 0:
                # receiver should not send data to the sender in stop-and-wait
                # so we just skip this and keep waiting for a real ack
                continue

            ackno = ack_msg.ack  # cumulative "next expected byte"

            # don't let ackno run past the final byte
            if ackno > final_ack:
                ackno = final_ack

            if ackno > last_acked:
                # ack progressed—slide window left edge
                last_acked = ackno
                win_left_edge = last_acked

                if last_acked >= final_ack:
                    break  #all bytes acknowledged

                # send exactly one next packet (stop-and-wait)
                first_to_tx = transmit_one()
            else:
                # duplicate/old ack—ignore and keep waiting
                pass
        else:
            # timeout: retransmit the same unacked packet at win_left_edge
            idx = seq_to_msgindex[win_left_edge]
            payload = messages[idx]
            m = Msg(win_left_edge, __ACK_UNUSED, payload)
            cs.sendto(m.serialize(), receiver_binding)
            print("Transmitted {}".format(str(m)) + "  [RETX]")


if __name__ == "__main__":
    args = parse_args()
    filedata = get_filedata(args["infile"])
    receiver_binding = ("", args["port"])
    cs = init_socket(receiver_binding)
    send_reliable(cs, filedata, receiver_binding, args["winsize"])
    cs.close()
    print("[S] Sender finished all transmissions.")

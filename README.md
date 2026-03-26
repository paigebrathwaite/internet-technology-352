Internet Technology / Networking Projects

This collection includes networking projects in Python focused on socket programming, application-layer protocols, reliable data transfer, and network simulation. The work moves from basic TCP client/server communication to a multi-server DNS-style system and then into stop-and-wait style reliable transport. Together, these projects show practice with sockets, protocol design, message framing, timeouts, retransmissions, and networked systems programming.

Programming Assignment 1: TCP Client/Server Communication

This project builds a series of TCP client and server programs in Python, starting from a minimal connection setup and progressing into echo communication, newline-delimited application protocols, and file-based message exchange. The assignment emphasizes socket creation, binding, listening, connecting, sending and receiving data, and handling basic application-layer transformations.
	1.	pa1-step1-client.py: Minimal TCP client that connects to a server, receives a short message, and prints connection details.
	2.	pa1-step1-server.py: Minimal TCP server that listens for one client connection and sends a basic response.
	3.	pa1-step2-client.py: Interactive echo client that sends user input to the server and prints the echoed reply.
	4.	pa1-step2-server.py: Echo server that receives client messages and sends them back unchanged.
	5.	pa1-step3-client.py: Client that sends newline-delimited messages and receives transformed line-based replies.
	6.	pa1-step3-server.py: Server that processes line-based messages and applies a string transformation before replying.
	7.	pa1-step4-client.py: File-driven client that reads input lines from a file, sends them to the server, and receives transformed responses.
	8.	pa1-step4-server.py: Server that processes file-driven requests, transforms each line, and writes results to an output file.
	9.	report.pdf: Assignment writeup or report describing the implementation and results.

Programming Assignment 2: RU-DNS Distributed Name Resolution

This project implements a TCP-based RU-DNS system with multiple cooperating servers that resolve domain names through referrals and authoritative responses. It includes a client, local server, root server, top-level servers, and an authoritative server. The assignment focuses on protocol design, server coordination, TCP stream handling, timeouts, and domain lookup logic.
	1.	client.py: Sends RU-DNS lookup requests and prints the resolved responses returned by the system.
	2.	ls.py: Local server that receives client requests and coordinates with the root server to complete resolution.
	3.	rs.py: Root server that routes requests to the correct top-level or authoritative server based on the domain.
	4.	ts1.py: Top-level server responsible for one set of domain mappings.
	5.	ts2.py: Second top-level server responsible for another set of domain mappings.
	6.	as.py: Authoritative server that returns final address records for supported domains.
	7.	Programming Assignment 2 Report.pdf: Project report describing the RU-DNS implementation and results.

Programming Assignment 3: Reliable Data Transfer with Stop-and-Wait

This project implements a simplified reliable transport protocol in Python using stop-and-wait style communication over sockets. It includes packet formatting, acknowledgments, retransmissions, timeout handling, and sender-side reliability logic. The assignment highlights reliable data transfer concepts that sit on top of an unreliable communication model.
	1.	stopandwait.py: Core stop-and-wait protocol implementation with packet structure, acknowledgments, retransmission logic, and timeout handling.
	2.	sender.py: Sender-side driver that transmits chunked application data and manages retransmissions based on ACK progress.
	3.	report.pdf: Assignment report summarizing the protocol design, implementation, and testing.

Homework 4: TCP Congestion Control, Routing, and CRC

This notebook-based assignment focuses on networking algorithms and simulation rather than socket-based implementation. It covers TCP congestion control behavior, routing algorithms, and cyclic redundancy check concepts, likely through code, analysis, and written explanations inside a Jupyter notebook.
	1.	HW4_PA-pb622.ipynb: Jupyter notebook for simulations and analysis involving TCP congestion control, routing algorithms, and CRC.

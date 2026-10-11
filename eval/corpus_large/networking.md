# Computer Networks Basics

## OSI and TCP/IP

The OSI model has seven layers: physical, data link, network, transport, session, presentation and application. The TCP/IP model collapses these into four layers: link, internet, transport and application.

## TCP vs UDP

TCP is connection oriented and reliable: it uses a three way handshake, sequence numbers, acknowledgements, retransmission and congestion control. UDP is connectionless with no delivery guarantee, which makes it fast and suitable for streaming, DNS and multicast discovery.

## IP Addressing and Multicast

An IPv4 address has 32 bits. Multicast sends one packet to a group of subscribed hosts using addresses in 224.0.0.0 to 239.255.255.255. A TTL of 1 keeps a multicast packet inside the local network. Many Wi-Fi networks isolate clients and block multicast.

## DNS

The domain name system translates names into IP addresses using a hierarchy of root, top level domain and authoritative servers, with caching at resolvers.

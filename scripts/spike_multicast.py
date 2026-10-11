"""SPIKE 2: does YOUR network pass multicast? Run `listen` on device 1 and `send` on device 2 (same Wi-Fi/hotspot).
  python spike_multicast.py listen
  python spike_multicast.py send
No packets arriving = client isolation / multicast blocked -> use manual peers (that is why they are mandatory)."""
import socket, struct, sys, time
GROUP, PORT = "239.255.49.50", 49500


def listen():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", PORT))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, struct.pack("4sl", socket.inet_aton(GROUP), socket.INADDR_ANY))
    s.settimeout(30)
    print(f"listening on {GROUP}:{PORT} for 30s ...")
    try:
        while True:
            data, addr = s.recvfrom(1024)
            print("GOT", addr, data[:60])
    except socket.timeout:
        print("nothing received -> multicast likely BLOCKED on this network")


def send():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    for i in range(10):
        s.sendto(f"bitswarm-ping-{i}".encode(), (GROUP, PORT)); print("sent", i); time.sleep(1)


if __name__ == "__main__":
    {"listen": listen, "send": send}[sys.argv[1] if len(sys.argv) > 1 else "listen"]()

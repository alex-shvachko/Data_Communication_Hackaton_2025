import socket
import struct
import threading
import time

# constants
MAGIC_COOKIE = 0xabcddcba
OFFER_MESSAGE_TYPE = 0x2
REQUEST_MESSAGE_TYPE = 0x3
PAYLOAD_MESSAGE_TYPE = 0x4


print_lock = threading.Lock()

UDP_PORT = 13117  
TCP_PORT = 12000 

def get_server_ip():
    """
    retrieves server ip for estabilishing connection
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'

def send_offer_messages(server_udp_port, server_tcp_port, stop_event):
    """
    sends offers via udp socket
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as offer_socket:
        offer_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        offer_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        broadcast_address = ('<broadcast>', 13117)  # port for offers
        while not stop_event.is_set():
            try:
               
                offer_message = struct.pack('!IBHH', MAGIC_COOKIE, OFFER_MESSAGE_TYPE, server_udp_port, server_tcp_port)
                offer_socket.sendto(offer_message, broadcast_address)
            except Exception as e:
                pass
            time.sleep(1)  # avoid busy waiting by sleeping for 1 second

def handle_tcp_client(conn, addr):
    """
    This function manages the connection with a TCP client. 
    It receives the file size requested by the client, sends the corresponding amount of data, and then closes the connection when the transfer is complete.
    """
    with conn:
        try:
            # receive data until newline
            data = b''
            while not data.endswith(b'\n'):
                chunk = conn.recv(1024)
                if not chunk:
                    break
                data += chunk
            if not data:
                return
            try:
                file_size_str = data.decode().strip()
                file_size = int(file_size_str)
                if file_size < 0:
                    raise ValueError("Negative file size")
            except ValueError:
                return
            # Send the requested data
            bytes_sent = 0
            chunk_size = 1024
            while bytes_sent < file_size:
                remaining = file_size - bytes_sent
                send_size = min(chunk_size, remaining)
                conn.sendall(b'\0' * send_size)
                bytes_sent += send_size
        except Exception as e:
            pass

def tcp_server(server_tcp_port, stop_event):
    """
    Sets up the TCP server to accept incoming connections.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_sock:
        tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp_sock.bind(('', server_tcp_port))
        tcp_sock.listen()
        tcp_sock.settimeout(1.0)  # Timeout to check for stop_event
        while not stop_event.is_set():
            try:
                conn, addr = tcp_sock.accept()
                client_thread = threading.Thread(target=handle_tcp_client, args=(conn, addr), daemon=True)
                client_thread.start()
            except socket.timeout:
                continue
            except Exception as e:
                pass

def handle_udp_request(request_data, client_address, server_udp_socket):
    """
    processes udp request sent by a client
    """
    try:
        if len(request_data) != 13:
            return
        magic_cookie, message_type, file_size = struct.unpack('!IBQ', request_data)
        if magic_cookie != MAGIC_COOKIE or message_type != REQUEST_MESSAGE_TYPE:
            return
        total_segments = file_size
        if total_segments == 0:
            return
        # send payload messages
        for segment in range(1, total_segments + 1):
            payload_message = struct.pack('!IBQQB', MAGIC_COOKIE, PAYLOAD_MESSAGE_TYPE, total_segments, segment, 0x00)
            server_udp_socket.sendto(payload_message, client_address)
    except Exception as e:
        pass

def udp_server(server_udp_port, stop_event):
    """
    set the UDP server to listen
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_sock:
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_sock.bind(('', server_udp_port))
        udp_sock.settimeout(1.0)  
        while not stop_event.is_set():
            try:
                data, addr = udp_sock.recvfrom(1024)  
                request_thread = threading.Thread(target=handle_udp_request, args=(data, addr, udp_sock), daemon=True)
                request_thread.start()
            except socket.timeout:
                continue
            except Exception as e:
                pass

def main():
    """
    Main function to start the server:
    - Initializes UDP and TCP sockets - startup phase at suggested architecture
    - Waiting to clients to connect - Looking for a server
    - then performs last step - Speed test
    """
    server_ip = get_server_ip()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as temp_udp_sock:
        temp_udp_sock.bind(('', 0)) 
        server_udp_port = temp_udp_sock.getsockname()[1]

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as temp_tcp_sock:
        temp_tcp_sock.bind(('', 0))  
        server_tcp_port = temp_tcp_sock.getsockname()[1]

    stop_event = threading.Event()
    try:
        offer_thread = threading.Thread(target=send_offer_messages, args=(server_udp_port, server_tcp_port, stop_event), daemon=True)
        offer_thread.start()
        tcp_thread = threading.Thread(target=tcp_server, args=(server_tcp_port, stop_event), daemon=True)
        tcp_thread.start()
        udp_thread = threading.Thread(target=udp_server, args=(server_udp_port, stop_event), daemon=True)
        udp_thread.start()
        with print_lock:
            print("Server started, listening on IP address 172.1.0.4")
            print(f"UDP Port: {server_udp_port}, TCP Port: {server_tcp_port}")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        with print_lock:
            print("\nShutting down the server...")
        stop_event.set()
        offer_thread.join()
        tcp_thread.join()
        udp_thread.join()
        with print_lock:
            print("Server successfully shut down.")
    except Exception as e:
        with print_lock:
            print(f"Server encountered an error: {e}")
        stop_event.set()

if __name__ == "__main__":
    main()

import socket
import struct
import threading
import time
import sys

# Constants
MAGIC_COOKIE = 0xabcddcba
OFFER_MESSAGE_TYPE = 0x2
REQUEST_MESSAGE_TYPE = 0x3
PAYLOAD_MESSAGE_TYPE = 0x4

# Lock for printing to avoid jumbled prints from multiple threads
print_lock = threading.Lock()

def get_server_ip():
    """
    Retrieves the server's IP address by connecting to an external host.
    """
    try:
        # Use a dummy connection to get the server's IP address
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # Doesn't have to be reachable
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'

def send_offer_messages(server_udp_port, server_tcp_port, stop_event):
    """
    Continuously sends offer messages via UDP broadcast every second.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as offer_socket:
        offer_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        offer_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        broadcast_address = ('<broadcast>', 13117)  # Standard port for offers
        while not stop_event.is_set():
            try:
                # Pack the offer message
                offer_message = struct.pack('!IBHH', MAGIC_COOKIE, OFFER_MESSAGE_TYPE, server_udp_port, server_tcp_port)
                offer_socket.sendto(offer_message, broadcast_address)
                with print_lock:
                    print(f"Sent offer message to {broadcast_address[0]}:{broadcast_address[1]}")
            except Exception as e:
                with print_lock:
                    print(f"Error sending offer message: {e}")
            time.sleep(1)  # Send offer every second

def handle_tcp_client(conn, addr):
    """
    Handles a TCP client connection:
    - Receives the requested file size.
    - Sends the specified amount of data.
    - Closes the connection.
    """
    with conn:
        try:
            with print_lock:
                print(f"TCP connection established with {addr}")
            # Receive data until newline
            data = b''
            while not data.endswith(b'\n'):
                chunk = conn.recv(1024)
                if not chunk:
                    break
                data += chunk
            if not data:
                with print_lock:
                    print(f"No data received from TCP client {addr}")
                return
            # Decode and parse file size
            try:
                file_size_str = data.decode().strip()
                file_size = int(file_size_str)
                if file_size < 0:
                    raise ValueError("Negative file size")
            except ValueError:
                with print_lock:
                    print(f"Invalid file size received from {addr}: {data}")
                return
            with print_lock:
                print(f"Sending {file_size} bytes to TCP client {addr}")
            # Send the specified amount of data
            bytes_sent = 0
            chunk_size = 1024
            while bytes_sent < file_size:
                remaining = file_size - bytes_sent
                send_size = min(chunk_size, remaining)
                conn.sendall(b'\0' * send_size)
                bytes_sent += send_size
            with print_lock:
                print(f"Finished sending data to TCP client {addr}")
        except Exception as e:
            with print_lock:
                print(f"Error handling TCP client {addr}: {e}")

def tcp_server(server_tcp_port, stop_event):
    """
    Sets up the TCP server to accept incoming connections.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_sock:
        tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp_sock.bind(('', server_tcp_port))
        tcp_sock.listen()
        with print_lock:
            print(f"TCP server listening on port {server_tcp_port}")
        tcp_sock.settimeout(1.0)  # Timeout to check for stop_event
        while not stop_event.is_set():
            try:
                conn, addr = tcp_sock.accept()
                client_thread = threading.Thread(target=handle_tcp_client, args=(conn, addr), daemon=True)
                client_thread.start()
            except socket.timeout:
                continue
            except Exception as e:
                with print_lock:
                    print(f"TCP server error: {e}")

def handle_udp_request(request_data, client_address, server_udp_socket):
    """
    Handles a UDP speed test request:
    - Validates the request message.
    - Sends payload messages with the specified file size.
    """
    try:
        if len(request_data) != 13:
            with print_lock:
                print(f"Invalid UDP request length from {client_address}")
            return
        # Unpack the request message
        magic_cookie, message_type, file_size = struct.unpack('!IBQ', request_data)
        if magic_cookie != MAGIC_COOKIE or message_type != REQUEST_MESSAGE_TYPE:
            with print_lock:
                print(f"Invalid UDP request from {client_address}")
            return
        with print_lock:
            print(f"Received UDP request from {client_address} for {file_size} bytes")
        # Calculate total segments (1 byte payload per segment)
        total_segments = file_size
        if total_segments == 0:
            with print_lock:
                print(f"File size is 0 for UDP client {client_address}")
            return
        # Send payload messages
        for segment in range(1, total_segments + 1):
            payload_message = struct.pack('!IBQQB', MAGIC_COOKIE, PAYLOAD_MESSAGE_TYPE, total_segments, segment, 0x00)
            server_udp_socket.sendto(payload_message, client_address)
            # Optional: Introduce a small delay to avoid network congestion
            # time.sleep(0.001)  # 1 ms
        with print_lock:
            print(f"Finished sending {total_segments} UDP payloads to {client_address}")
    except Exception as e:
        with print_lock:
            print(f"Error handling UDP request from {client_address}: {e}")

def udp_server(server_udp_port, stop_event):
    """
    Sets up the UDP server to listen for incoming speed test requests.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_sock:
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_sock.bind(('', server_udp_port))
        with print_lock:
            print(f"UDP server listening on port {server_udp_port}")
        udp_sock.settimeout(1.0)  # Timeout to check for stop_event
        while not stop_event.is_set():
            try:
                data, addr = udp_sock.recvfrom(1024)  # Buffer size is arbitrary
                # Handle each request in a separate thread
                request_thread = threading.Thread(target=handle_udp_request, args=(data, addr, udp_sock), daemon=True)
                request_thread.start()
            except socket.timeout:
                continue
            except Exception as e:
                with print_lock:
                    print(f"UDP server error: {e}")

def main():
    """
    Main function to start the server:
    - Initializes UDP and TCP sockets.
    - Starts offer sender, TCP server, and UDP server threads.
    - Waits for KeyboardInterrupt to gracefully shut down.
    """
    server_ip = get_server_ip()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as temp_udp_sock:
        temp_udp_sock.bind(('', 0))  # Bind to a free UDP port
        server_udp_port = temp_udp_sock.getsockname()[1]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as temp_tcp_sock:
        temp_tcp_sock.bind(('', 0))  # Bind to a free TCP port
        server_tcp_port = temp_tcp_sock.getsockname()[1]
    # Event to signal threads to stop
    stop_event = threading.Event()
    try:
        # Start offer sender thread
        offer_thread = threading.Thread(target=send_offer_messages, args=(server_udp_port, server_tcp_port, stop_event), daemon=True)
        offer_thread.start()
        # Start TCP server thread
        tcp_thread = threading.Thread(target=tcp_server, args=(server_tcp_port, stop_event), daemon=True)
        tcp_thread.start()
        # Start UDP server thread
        udp_thread = threading.Thread(target=udp_server, args=(server_udp_port, stop_event), daemon=True)
        udp_thread.start()
        with print_lock:
            print(f"Server started, listening on IP address {server_ip}")
            print(f"UDP Port: {server_udp_port}, TCP Port: {server_tcp_port}")
        # Keep the main thread alive until interrupted
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

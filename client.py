import socket
import struct
import threading
import time
import sys
import select
from datetime import datetime
import logging
import queue

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ANSI color codes for enhanced output readability
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

# Constants
MAGIC_COOKIE = 0xabcddcba
OFFER_MESSAGE_TYPE = 0x2
REQUEST_MESSAGE_TYPE = 0x3
PAYLOAD_MESSAGE_TYPE = 0x4
OFFER_LISTEN_PORT = 13117  # Standard port for offers
BUFFER_SIZE = 1024  # Buffer size for receiving data
UDP_TIMEOUT = 1.0  # Timeout to detect end of UDP transfer

# Lock for synchronized printing
print_lock = threading.Lock()

def colored_print(message, color=Colors.ENDC):
    """
    Prints a message with the specified ANSI color.
    """
    with print_lock:
        print(f"{color}{message}{Colors.ENDC}")

class Client:
    def __init__(self):
        self.UDP_PORT = 13117
        # ... other initialization code ...

    def start(self):
        # Create the thread correctly
        listener_thread = threading.Thread(target=self.listen_for_offers)
        listener_thread.start()

    def listen_for_offers(self):
        print(f"Client started, listening for offer requests on UDP port {self.UDP_PORT}...")
        print(f"[DEBUG] Client listening on UDP port {self.UDP_PORT}")
        
        while True:
            try:
                data, addr = self.udp_socket.recvfrom(1024)
                print(f"[DEBUG] Received data from {addr}: {data.hex()}")
                
                # Parse message
                if len(data) < 7:
                    print("[ERROR] Message too short")
                    continue
                    
                magic_cookie = data[:4].hex()
                message_type = data[4]
                server_port = int.from_bytes(data[5:7], 'big')
                
                print(f"[DEBUG] Parsed message:")
                print(f"[DEBUG] - Magic cookie: {magic_cookie}")
                print(f"[DEBUG] - Message type: {hex(message_type)}")
                print(f"[DEBUG] - Server port: {server_port}")
                
                if magic_cookie != 'abcddcba':
                    print("[ERROR] Invalid magic cookie")
                    continue
                    
                if message_type != 0x02:
                    print("[ERROR] Invalid message type")
                    continue
                    
                print(f"[DEBUG] Attempting TCP connection to {addr[0]}:{server_port}")
                
                try:
                    # Create TCP socket
                    tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    print(f"[DEBUG] TCP socket created")
                    
                    # Set timeout for connection
                    tcp_socket.settimeout(5)
                    print(f"[DEBUG] Attempting connection to {addr[0]}:{server_port}")
                    
                    # Connect
                    tcp_socket.connect((addr[0], server_port))
                    print(f"[DEBUG] TCP connection successful!")
                    
                    # Start your speed test here
                    return tcp_socket, addr[0], server_port
                    
                except Exception as e:
                    print(f"[ERROR] TCP connection failed: {str(e)}")
                    tcp_socket.close()
                    
            except Exception as e:
                print(f"[ERROR] Error processing offer: {str(e)}")

def get_user_parameters():
    """
    Prompts the user for file size, number of TCP connections, and number of UDP connections.
    Returns a tuple of (file_size, num_tcp, num_udp).
    """
    while True:
        try:
            file_size_input = input("Enter the file size to download (in bytes): ")
            file_size = int(file_size_input)
            if file_size <= 0:
                raise ValueError("File size must be positive.")
            num_tcp_input = input("Enter the number of TCP connections: ")
            num_tcp = int(num_tcp_input)
            if num_tcp < 0:
                raise ValueError("Number of TCP connections cannot be negative.")
            num_udp_input = input("Enter the number of UDP connections: ")
            num_udp = int(num_udp_input)
            if num_udp < 0:
                raise ValueError("Number of UDP connections cannot be negative.")
            return file_size, num_tcp, num_udp
        except ValueError as ve:
            colored_print(f"Invalid input: {ve}. Please try again.", Colors.WARNING)

def perform_tcp_transfer(server_ip, server_tcp_port, file_size, transfer_id):
    """
    Performs a TCP transfer to the specified server.
    Measures transfer time and calculates speed.
    Logs the results.
    """
    try:
        start_time = time.time()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_sock:
            tcp_sock.settimeout(10.0)  # Timeout for TCP connection
            tcp_sock.connect((server_ip, server_tcp_port))
            # Send file size as string followed by newline
            request = f"{file_size}\n".encode()
            tcp_sock.sendall(request)
            # Receive the data
            bytes_received = 0
            while bytes_received < file_size:
                chunk = tcp_sock.recv(BUFFER_SIZE)
                if not chunk:
                    break
                bytes_received += len(chunk)
        end_time = time.time()
        duration = end_time - start_time
        speed = (bytes_received * 8) / duration  # bits per second
        colored_print(f"TCP transfer #{transfer_id} finished, total time: {duration:.2f} seconds, total speed: {speed:.2f} bits/second", Colors.OKCYAN)
    except Exception as e:
        colored_print(f"TCP transfer #{transfer_id} failed: {e}", Colors.FAIL)

def perform_udp_transfer(server_ip, server_udp_port, file_size, transfer_id, stats_lock, stats):
    """
    Performs a UDP transfer to the specified server.
    Measures transfer time, speed, and packet loss percentage.
    Logs the results.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_sock:
            udp_sock.settimeout(UDP_TIMEOUT)
            # Send request message
            request_message = struct.pack('!IBQ', MAGIC_COOKIE, REQUEST_MESSAGE_TYPE, file_size)
            start_time = time.time()
            udp_sock.sendto(request_message, (server_ip, server_udp_port))
            expected_segments = file_size  # 1 byte per segment
            received_segments = 0
            last_receive_time = time.time()
            while True:
                try:
                    data, addr = udp_sock.recvfrom(1024)
                    current_time = time.time()
                    if len(data) < 21:
                        continue  # Invalid payload length
                    magic_cookie, message_type, total_segments, current_segment = struct.unpack('!IBQQ', data[:21])
                    if magic_cookie != MAGIC_COOKIE or message_type != PAYLOAD_MESSAGE_TYPE:
                        continue  # Invalid payload
                    received_segments += 1
                    last_receive_time = current_time
                except socket.timeout:
                    # Check if timeout has occurred since last received packet
                    if time.time() - last_receive_time >= UDP_TIMEOUT:
                        break
            end_time = time.time()
            duration = end_time - start_time
            speed = (received_segments * 8) / duration  # bits per second
            packet_loss = ((expected_segments - received_segments) / expected_segments) * 100 if expected_segments > 0 else 0
            colored_print(f"UDP transfer #{transfer_id} finished, total time: {duration:.2f} seconds, total speed: {speed:.2f} bits/second, percentage of packets received successfully: {100 - packet_loss:.2f}%", Colors.OKCYAN)
            # Update statistics
            with stats_lock:
                stats['udp_total_time'] += duration
                stats['udp_total_speed'] += speed
                stats['udp_total_packets'] += expected_segments
                stats['udp_received_packets'] += received_segments
    except Exception as e:
        colored_print(f"UDP transfer #{transfer_id} failed: {e}", Colors.FAIL)

def start_speed_test(server_info, file_size, num_tcp, num_udp):
    """
    Initiates the speed test by starting TCP and UDP transfer threads based on user input.
    Waits for all transfers to complete before returning.
    """
    server_ip, server_udp_port, server_tcp_port = server_info
    colored_print(f"Connecting to server {server_ip}:{server_tcp_port}", Colors.OKGREEN)
    threads = []
    stats = {
        'udp_total_time': 0.0,
        'udp_total_speed': 0.0,
        'udp_total_packets': 0,
        'udp_received_packets': 0
    }
    stats_lock = threading.Lock()
    # Start TCP transfer threads
    for i in range(1, num_tcp + 1):
        tcp_thread = threading.Thread(target=perform_tcp_transfer, args=(server_ip, server_tcp_port, file_size, i), daemon=True)
        threads.append(tcp_thread)
        tcp_thread.start()
    # Start UDP transfer threads
    for i in range(1, num_udp + 1):
        udp_thread = threading.Thread(target=perform_udp_transfer, args=(server_ip, server_udp_port, file_size, i, stats_lock, stats), daemon=True)
        threads.append(udp_thread)
        udp_thread.start()
    # Wait for all threads to complete
    for thread in threads:
        thread.join()
    # Optionally, print aggregated statistics
    if num_udp > 0:
        avg_udp_time = stats['udp_total_time'] / num_udp if num_udp > 0 else 0
        avg_udp_speed = stats['udp_total_speed'] / num_udp if num_udp > 0 else 0
        total_packets = stats['udp_total_packets']
        received_packets = stats['udp_received_packets']
        packet_loss = ((total_packets - received_packets) / total_packets) * 100 if total_packets > 0 else 0
        colored_print(f"Average UDP transfer time: {avg_udp_time:.2f} seconds", Colors.OKCYAN)
        colored_print(f"Average UDP transfer speed: {avg_udp_speed:.2f} bits/second", Colors.OKCYAN)
        colored_print(f"Total UDP packet loss: {packet_loss:.2f}%", Colors.OKCYAN)

# Define the function at module level (outside any class or other function)
def listen_for_offers(stop_event, offer_queue, file_size, transfer_id):
    print(f"[DEBUG] Client listening for offers...")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_sock:
        udp_sock.bind(('', 13117))  # Bind to the offer port
        while not stop_event.is_set():
            try:
                offer_message, server_address = udp_sock.recvfrom(1024)  # Buffer size of 1024 bytes
                # Unpack the offer message
                magic_cookie, message_type, server_udp_port, server_tcp_port = struct.unpack('!IBHH', offer_message)
                if magic_cookie == MAGIC_COOKIE and message_type == OFFER_MESSAGE_TYPE:
                    print(f"[DEBUG] Received offer from {server_address}: UDP Port {server_udp_port}, TCP Port {server_tcp_port}")
                    # Initiate TCP transfer
                    perform_tcp_transfer(server_address[0], server_tcp_port, file_size, transfer_id)
            except Exception as e:
                print(f"[ERROR] {e}")

def main():
    # Create the events and queue
    stop_event = threading.Event()
    offer_queue = queue.Queue()
    file_size, num_tcp, num_udp = get_user_parameters()
    transfer_id = 1
    
    # Create and start the thread
    offer_listener_thread = threading.Thread(
        target=listen_for_offers,  # Now this will be found
        args=(stop_event, offer_queue, file_size, transfer_id),
        daemon=True
    )
    offer_listener_thread.start()
    
    try:
        # Keep main thread alive
        while True:
            if not offer_queue.empty():
                # Handle offer
                pass
            threading.Event().wait(0.1)
    except KeyboardInterrupt:
        print("Shutting down...")
        stop_event.set()

if __name__ == "__main__":
    main()

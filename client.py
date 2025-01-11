import socket
import struct
import threading
import time
import sys
import select
from datetime import datetime

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

def listen_for_offers(stop_event, offer_queue):
    """
    Listens for UDP offer messages from servers and adds them to the offer_queue.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as offer_socket:
        offer_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        offer_socket.bind(('', OFFER_LISTEN_PORT))
        offer_socket.settimeout(1.0)  # Timeout to allow periodic stop_event checking
        colored_print(f"Client started, listening for offer requests on UDP port {OFFER_LISTEN_PORT}...", Colors.OKBLUE)
        while not stop_event.is_set():
            try:
                data, addr = offer_socket.recvfrom(BUFFER_SIZE)
                # Validate offer message
                if len(data) != 9:
                    continue  # Invalid offer length
                magic_cookie, message_type, server_udp_port, server_tcp_port = struct.unpack('!IBHH', data)
                if magic_cookie != MAGIC_COOKIE or message_type != OFFER_MESSAGE_TYPE:
                    continue  # Invalid offer format
                server_ip = addr[0]
                server_info = (server_ip, server_udp_port, server_tcp_port)
                with print_lock:
                    colored_print(f"Received offer from {server_ip}:{server_tcp_port}", Colors.OKGREEN)
                offer_queue.append(server_info)
            except socket.timeout:
                continue
            except Exception as e:
                with print_lock:
                    colored_print(f"Error receiving offer: {e}", Colors.FAIL)

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

def main():
    """
    Main function to run the client:
    - Listens for server offers.
    - Upon receiving an offer, prompts the user for parameters.
    - Initiates speed tests based on user input.
    - Continues to listen for new offers after completing transfers.
    """
    stop_event = threading.Event()
    offer_queue = []
    offer_listener_thread = threading.Thread(target=listen_for_offers, args=(stop_event, offer_queue), daemon=True)
    offer_listener_thread.start()
    try:
        while True:
            if offer_queue:
                server_info = offer_queue.pop(0)
                # Prompt user for parameters
                file_size, num_tcp, num_udp = get_user_parameters()
                # Start speed test
                start_speed_test(server_info, file_size, num_tcp, num_udp)
                colored_print("All transfers complete, listening for offer requests...", Colors.OKBLUE)
            else:
                time.sleep(1)  # Wait for offers
    except KeyboardInterrupt:
        colored_print("\nClient shutting down...", Colors.WARNING)
        stop_event.set()
        offer_listener_thread.join()
        colored_print("Client successfully shut down.", Colors.OKGREEN)
    except Exception as e:
        colored_print(f"Client encountered an error: {e}", Colors.FAIL)
        stop_event.set()

if __name__ == "__main__":
    main()

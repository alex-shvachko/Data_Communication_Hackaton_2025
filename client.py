import logging
import queue
import socket
import struct
import threading
import time

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
COOLDOWN_PERIOD = 5  # Wait at least 5 seconds between tests

# Lock for synchronized printing
print_lock = threading.Lock()


def colored_print(message, color=Colors.ENDC):
    """
    Prints a message with the specified ANSI color.
    """
    with print_lock:
        print(f"{color}{message}{Colors.ENDC}")


def get_user_parameters():
    """
    gets user parameters
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

            # send file size as string followed by newline
            request = f"{file_size}\n".encode()
            tcp_sock.sendall(request)

            # receive the data
            bytes_received = 0
            while bytes_received < file_size:
                chunk = tcp_sock.recv(BUFFER_SIZE)
                if not chunk:
                    break
                bytes_received += len(chunk)

        end_time = time.time()
        duration = end_time - start_time
        speed = (bytes_received * 8) / duration  # bits per second
        colored_print(
            f"TCP transfer #{transfer_id} finished, total time: {duration:.2f} seconds, total speed: {speed:.2f} bits/second",
            Colors.OKCYAN)
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
            # timeout for udp socket connection - prevents busy waiting
            '''
            If no message arrives within 1 second, a socket.timeout exception is raised and caught, 
            allowing the thread to briefly pause before trying again.
            '''
            udp_sock.settimeout(UDP_TIMEOUT)

            # Send request message
            request_message = struct.pack('!IBQ', MAGIC_COOKIE, REQUEST_MESSAGE_TYPE, file_size)
            start_time = time.time()
            udp_sock.sendto(request_message, (server_ip, server_udp_port))

            expected_segments = file_size  # UDP_PAYLOAD_SIZE
            received_segments = 0
            last_receive_time = time.time()

            while True:
                try:
                    data, addr = udp_sock.recvfrom(1024)
                    current_time = time.time()

                    if len(data) < 21:
                        continue  # not correct payload length

                    magic_cookie, message_type, total_segments, current_segment = struct.unpack('!IBQQ', data[:21])
                    if magic_cookie != MAGIC_COOKIE or message_type != PAYLOAD_MESSAGE_TYPE:
                        continue  # invalid payload

                    received_segments += 1
                    last_receive_time = current_time

                except socket.timeout:
                    if time.time() - last_receive_time >= UDP_TIMEOUT:
                        break

            end_time = time.time()
            duration = end_time - start_time
            speed = (received_segments * 8) / duration  # bits per second
            packet_loss = ((
                                       expected_segments - received_segments) / expected_segments) * 100 if expected_segments > 0 else 0

            colored_print(
                f"UDP transfer #{transfer_id} finished, total time: {duration:.2f} seconds, "
                f"total speed: {speed:.2f} bits/second, "
                f"percentage of packets received successfully: {100 - packet_loss:.2f}%",
                Colors.OKCYAN)

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
    initiates the speed test. execute all at the same time and waits until all are done
    """
    server_ip, server_udp_port, server_tcp_port = server_info
    # colored_print(f"Connecting to server {server_ip}:{server_tcp_port}", Colors.OKGREEN)

    threads = []
    stats = {
        'udp_total_time': 0.0,
        'udp_total_speed': 0.0,
        'udp_total_packets': 0,
        'udp_received_packets': 0
    }
    stats_lock = threading.Lock()

    # start TCP threads
    for i in range(1, num_tcp + 1):
        tcp_thread = threading.Thread(
            target=perform_tcp_transfer,
            args=(server_ip, server_tcp_port, file_size, i),
            daemon=True
        )
        threads.append(tcp_thread)
        tcp_thread.start()

    # start UDP threads
    for i in range(1, num_udp + 1):
        udp_thread = threading.Thread(
            target=perform_udp_transfer,
            args=(server_ip, server_udp_port, file_size, i, stats_lock, stats),
            daemon=True
        )
        threads.append(udp_thread)
        udp_thread.start()

    # wait for all threads to complete
    for thread in threads:
        thread.join()

    # print aggregated statistics - average time, average speed, packet loss
    if num_udp > 0:
        avg_udp_time = stats['udp_total_time'] / num_udp if num_udp > 0 else 0
        avg_udp_speed = stats['udp_total_speed'] / num_udp if num_udp > 0 else 0
        total_packets = stats['udp_total_packets']
        received_packets = stats['udp_received_packets']
        packet_loss = ((total_packets - received_packets) / total_packets) * 100 if total_packets > 0 else 0

        colored_print(f"Average UDP transfer time: {avg_udp_time:.2f} seconds", Colors.OKCYAN)
        colored_print(f"Average UDP transfer speed: {avg_udp_speed:.2f} bits/second", Colors.OKCYAN)
        colored_print(f"Total UDP packet loss: {packet_loss:.2f}%", Colors.OKCYAN)


def listen_for_offers(stop_event, offer_queue, file_size, transfer_id):
    """
    listen for udp offer messages from client
    """
    colored_print("Client started, listening for offer requests...", Colors.OKBLUE)
    current_server = None

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_sock:
        udp_sock.bind(('', OFFER_LISTEN_PORT))
        udp_sock.settimeout(1.0)

        while not stop_event.is_set():
            try:
                offer_message, server_address = udp_sock.recvfrom(BUFFER_SIZE)
                # unpack the  message for parts - magic cookie, message type,
                # server_udp_port, server_tcp_port
                magic_cookie, message_type, server_udp_port, server_tcp_port = struct.unpack('!IBHH', offer_message)

                if magic_cookie == MAGIC_COOKIE and message_type == OFFER_MESSAGE_TYPE: # checks valid
                    server_info = (server_address[0], server_udp_port, server_tcp_port)

                    # if we dont have server or this server is our current
                    if current_server is None or current_server == server_info: # checks if new server or not
                        if current_server is None:
                            # colored_print(f"Connected to server {server_address[0]}:{server_tcp_port}", Colors.OKBLUE) # so it's the first server we are connecting to
                            current_server = server_info
                        offer_queue.put(server_info)
            except socket.timeout:
                # if happens timeout
                continue
            except Exception as e:
                # if other exeption
                colored_print(f"Error in offer listener: {e}", Colors.FAIL)


def main():
    """
    main function for client:
    1. gets user parameters
    2. starts listening for offers
    3. handles offers and makes sure to initiates speed tests
    """
    stop_event = threading.Event()
    offer_queue = queue.Queue()

    try:
        # get user parameters
        file_size, num_tcp, num_udp = get_user_parameters()
        transfer_id = 1

        # start offer listener
        offer_listener_thread = threading.Thread(
            target=listen_for_offers,
            args=(stop_event, offer_queue, file_size, transfer_id),
            daemon=True
        )
        offer_listener_thread.start()

        while True:
            try:
                # get server info - with timeout to prevent busy waiting
                server_info = offer_queue.get(timeout=1.0)

                # clear the queue of pending offers before we statr
                while not offer_queue.empty():
                    try:
                        offer_queue.get_nowait()
                    except queue.Empty:
                        break

                # perform speed test with the received server info
                start_speed_test(server_info, file_size, num_tcp, num_udp)
                colored_print("All transfers complete, listening to offer requests...", Colors.OKBLUE)
                transfer_id += 1  # Increment  ID every round

            except queue.Empty:
                continue  # keep waiting if there is no offer

    except KeyboardInterrupt:
        # colored_print("\nShutting down...", Colors.WARNING)
        stop_event.set()
        offer_listener_thread.join(timeout=2.0)
    except Exception as e:
        # colored_print(f"Error in main: {e}", Colors.FAIL)
        stop_event.set()


if __name__ == "__main__":
    main()
